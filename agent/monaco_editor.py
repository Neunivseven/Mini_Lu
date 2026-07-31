"""代码编辑器组件：QPlainTextEdit + 语法高亮 + 代码补全。

替代 QWebEngineView/Monaco 方案，避免与 termqt forkpty 冲突。
补全：Python 走 jedi（变量/库成员，后台线程）；其它语言回退关键词 + 缓冲区单词。
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRegularExpression, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QCompleter,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

_JEDI_OK: bool | None = None
_JEDI_POOL = None

# —— #include 头文件补全 ——

_INCLUDE_RE = re.compile(r'^\s*#\s*include\s*[<"]([\w./+-]*)$')

_STD_HEADERS_CPP = (
    "iostream vector string map unordered_map unordered_set algorithm memory thread "
    "mutex functional chrono cmath cstdio cstdlib cstring cstdint cassert cctype ctime "
    "fstream sstream iomanip array deque list set queue stack utility tuple optional "
    "variant any atomic condition_variable future random regex filesystem numeric "
    "iterator limits exception stdexcept typeinfo type_traits bitset complex "
    "initializer_list ratio locale codecvt shared_mutex string_view span numbers "
    "concepts ranges format source_location coroutine barrier latch semaphore bit "
).split()

_STD_HEADERS_C = (
    "stdio.h stdlib.h string.h math.h time.h ctype.h stdint.h stddef.h stdbool.h "
    "assert.h errno.h signal.h limits.h float.h stdarg.h setjmp.h locale.h wchar.h "
    "unistd.h fcntl.h pthread.h semaphore.h dirent.h dlfcn.h netdb.h poll.h "
    "sys/types.h sys/stat.h sys/time.h sys/wait.h sys/socket.h sys/mman.h sys/ioctl.h "
    "arpa/inet.h netinet/in.h netinet/tcp.h "
).split()

_SYS_HEADERS: list[str] | None = None
_COMP_CFG: dict | None = None

_HEADER_SUFFIXES = {".h", ".hpp", ".hh", ".hxx", ""}  # ""：Eigen/Dense 这类无后缀头


def _completion_config() -> dict:
    """config/editor_completion.yaml：用户扩展词库与头文件扫描路径（缓存）。"""
    global _COMP_CFG
    if _COMP_CFG is not None:
        return _COMP_CFG
    cfg: dict = {"include_paths": [], "words": {}}
    try:
        import yaml

        from agent.llm_client import config_read_path

        p = config_read_path("editor_completion.yaml")
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                cfg["include_paths"] = [
                    str(x) for x in (raw.get("include_paths") or []) if x
                ]
                words = raw.get("words") or {}
                if isinstance(words, dict):
                    cfg["words"] = {
                        str(k): [str(x) for x in v]
                        for k, v in words.items()
                        if isinstance(v, list)
                    }
    except Exception:
        pass
    _COMP_CFG = cfg
    return cfg


def _scan_headers(root: Path, out: set[str], *, depth: int, prefix: str = "", cap: int = 20000) -> None:
    """收集 root 下的头文件相对路径（depth 层）；目录以 name/ 形式加入。"""

    def walk(d: Path, rel: str, level: int) -> None:
        if len(out) >= cap:
            return
        try:
            entries = sorted(d.iterdir())
        except Exception:
            return
        for p in entries:
            if len(out) >= cap:
                return
            name = p.name
            if name.startswith("."):
                continue
            if p.is_file():
                if p.suffix in _HEADER_SUFFIXES and not name.endswith("~"):
                    out.add(rel + name)
            elif p.is_dir():
                out.add(rel + name + "/")
                if level < depth:
                    walk(p, rel + name + "/", level + 1)

    if root.is_dir():
        walk(root, prefix, 1)


def _system_headers() -> list[str]:
    """真实存在的头文件清单（缓存）：系统目录 + 自动探测的第三方库 + 用户配置路径。"""
    global _SYS_HEADERS
    if _SYS_HEADERS is not None:
        return _SYS_HEADERS
    out: set[str] = set()
    try:
        _scan_headers(Path("/usr/include"), out, depth=1)
        _scan_headers(Path("/usr/include/sys"), out, depth=1, prefix="sys/")
        _scan_headers(Path("/usr/local/include"), out, depth=2)
        # 常见第三方库（需要 -I 指到子目录，include 时写相对名）
        for sub in ("eigen3", "opencv4"):
            _scan_headers(Path("/usr/include") / sub, out, depth=2)
        # ROS2：include 根是 /opt/ros/<distro>/include/<pkg>/，逐包扫描
        ros = Path("/opt/ros")
        if ros.is_dir():
            for distro in sorted(ros.iterdir()):
                inc = distro / "include"
                if not inc.is_dir():
                    continue
                for pkg in sorted(inc.iterdir()):
                    _scan_headers(pkg, out, depth=3)
        # 用户配置的额外根目录
        for extra in _completion_config()["include_paths"]:
            _scan_headers(Path(extra).expanduser(), out, depth=2)
    except Exception:
        pass
    _SYS_HEADERS = sorted(out)[:20000]
    return _SYS_HEADERS


def _header_candidates(prefix: str, lang: str) -> list[str]:
    pool: set[str] = set(_STD_HEADERS_C) | set(_system_headers())
    if lang == "cpp":
        pool |= set(_STD_HEADERS_CPP)
    low = prefix.lower()
    return sorted(h for h in pool if h.lower().startswith(low) and h != prefix)[:120]


def _jedi_ok() -> bool:
    global _JEDI_OK
    if _JEDI_OK is None:
        try:
            import jedi  # noqa: F401

            _JEDI_OK = True
        except Exception:
            _JEDI_OK = False
    return bool(_JEDI_OK)


def _jedi_pool():
    global _JEDI_POOL
    if _JEDI_POOL is None:
        import concurrent.futures

        _JEDI_POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="editor-jedi"
        )
    return _JEDI_POOL

_EXT_LANG = {
    ".py": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "scss", ".less": "less", ".xml": "xml",
    ".sql": "sql", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".lua": "lua", ".r": "r",
    ".swift": "swift", ".kt": "kotlin", ".dart": "dart",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini",
    ".txt": "plaintext", ".log": "plaintext",
    ".dockerfile": "dockerfile", ".gitignore": "plaintext",
}


def lang_from_path(path: str | Path) -> str:
    p = Path(path)
    name = p.name.lower()
    if name == "dockerfile":
        return "dockerfile"
    if name == "makefile":
        return "makefile"
    if name == "cmakelists.txt" or p.suffix.lower() == ".cmake":
        return "cmake"
    return _EXT_LANG.get(p.suffix.lower(), "plaintext")


# 语法高亮规则

_PYTHON_KEYWORDS = (
    "False None True and as assert async await break class continue "
    "def del elif else except finally for from global if import in is "
    "lambda nonlocal not or pass raise return try while with yield"
).split()

_JS_KEYWORDS = (
    "break case catch class const continue debugger default delete do "
    "else export extends false finally for function if import in instanceof "
    "let new null return super switch this throw true try typeof var void while with yield"
).split()

_C_KEYWORDS = (
    "auto break case char const continue default do double else enum extern "
    "float for goto if int long register return short signed sizeof static "
    "struct switch typedef union unsigned void volatile while"
).split()

_CMAKE_COMMANDS = (
    "cmake_minimum_required project set unset option message include "
    "add_executable add_library add_subdirectory add_definitions add_compile_options "
    "add_compile_definitions add_custom_command add_custom_target add_dependencies add_test "
    "target_link_libraries target_include_directories target_compile_options "
    "target_compile_definitions target_compile_features target_sources target_link_options "
    "find_package find_library find_path find_program find_file "
    "include_directories link_directories link_libraries aux_source_directory "
    "set_target_properties set_property get_property get_target_property "
    "install export enable_testing enable_language "
    "if elseif else endif foreach endforeach while endwhile "
    "function endfunction macro endmacro return break continue "
    "file list string math cmake_policy cmake_parse_arguments "
    "configure_file execute_process source_group separate_arguments "
    "mark_as_advanced site_name try_compile try_run variable_watch"
).split()

# 补全增强词典（不参与高亮）：CMake 常用变量 / 关键字参数 / 常见包名
_CMAKE_EXTRA = (
    "CMAKE_CXX_STANDARD CMAKE_CXX_STANDARD_REQUIRED CMAKE_CXX_FLAGS CMAKE_C_FLAGS "
    "CMAKE_C_STANDARD CMAKE_BUILD_TYPE CMAKE_SOURCE_DIR CMAKE_BINARY_DIR "
    "CMAKE_CURRENT_SOURCE_DIR CMAKE_CURRENT_BINARY_DIR CMAKE_CURRENT_LIST_DIR "
    "CMAKE_MODULE_PATH CMAKE_PREFIX_PATH CMAKE_INSTALL_PREFIX CMAKE_TOOLCHAIN_FILE "
    "CMAKE_EXPORT_COMPILE_COMMANDS CMAKE_RUNTIME_OUTPUT_DIRECTORY "
    "CMAKE_LIBRARY_OUTPUT_DIRECTORY CMAKE_ARCHIVE_OUTPUT_DIRECTORY "
    "CMAKE_POSITION_INDEPENDENT_CODE CMAKE_VERBOSE_MAKEFILE CMAKE_THREAD_LIBS_INIT "
    "PROJECT_NAME PROJECT_SOURCE_DIR PROJECT_BINARY_DIR PROJECT_VERSION "
    "EXECUTABLE_OUTPUT_PATH LIBRARY_OUTPUT_PATH BUILD_SHARED_LIBS "
    "PUBLIC PRIVATE INTERFACE REQUIRED QUIET COMPONENTS NO_MODULE "
    "STATIC SHARED MODULE OBJECT ALIAS IMPORTED GLOBAL "
    "VERSION LANGUAGES DESTINATION TARGETS FILES DIRECTORY PROPERTIES "
    "GLOB GLOB_RECURSE APPEND PREPEND REMOVE_ITEM STREQUAL MATCHES DEFINED "
    "STATUS WARNING FATAL_ERROR SEND_ERROR DEBUG NOTICE VERBOSE "
    "Boost Eigen3 OpenCV Threads OpenMP Python3 PkgConfig GTest fmt spdlog "
    "catkin ament_cmake rclcpp rclpy std_msgs geometry_msgs sensor_msgs nav_msgs "
).split()

# 有专用词典的语言：1 个字符即触发补全
_EXTRA_WORDS: dict[str, list[str]] = {
    "cmake": _CMAKE_EXTRA,
}

_LANG_KEYWORDS: dict[str, list[str]] = {
    "cmake": _CMAKE_COMMANDS,
    "python": _PYTHON_KEYWORDS,
    "javascript": _JS_KEYWORDS,
    "typescript": _JS_KEYWORDS + ["interface", "type", "enum", "implements", "namespace", "declare", "abstract", "readonly"],
    "c": _C_KEYWORDS,
    "cpp": _C_KEYWORDS + ["class", "namespace", "template", "virtual", "override", "public", "private", "protected", "using", "new", "delete", "try", "catch", "throw", "bool", "true", "false"],
    "java": ["abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class", "const", "continue", "default", "do", "double", "else", "enum", "extends", "final", "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int", "interface", "long", "native", "new", "null", "package", "private", "protected", "public", "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this", "throw", "throws", "transient", "try", "void", "volatile", "while", "true", "false"],
    "go": ["break", "case", "chan", "const", "continue", "default", "defer", "else", "fallthrough", "for", "func", "go", "goto", "if", "import", "interface", "map", "package", "range", "return", "select", "struct", "switch", "type", "var", "true", "false", "nil"],
    "rust": ["as", "break", "const", "continue", "crate", "else", "enum", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop", "match", "mod", "move", "mut", "pub", "ref", "return", "self", "Self", "static", "struct", "super", "trait", "true", "type", "unsafe", "use", "where", "while"],
    "shell": ["if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case", "esac", "in", "function", "return", "exit", "local", "export", "source", "alias", "unset", "set", "echo", "read", "true", "false"],
    "sql": ["SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TABLE", "INDEX", "INTO", "VALUES", "SET", "AND", "OR", "NOT", "NULL", "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON", "AS", "ORDER", "BY", "GROUP", "HAVING", "LIMIT", "OFFSET", "UNION", "DISTINCT", "COUNT", "SUM", "AVG", "MAX", "MIN", "LIKE", "IN", "BETWEEN", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END", "BEGIN", "COMMIT", "ROLLBACK"],
}


def _fmt(fg: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(fg))
    if bold:
        f.setFontWeight(QFont.Bold)
    if italic:
        f.setFontItalic(True)
    return f


_THEME = {
    "keyword": _fmt("#7C3AED", bold=True),
    "string": _fmt("#B45309"),
    "comment": _fmt("#6B7280", italic=True),
    "number": _fmt("#0369A1"),
    "function": _fmt("#9333EA"),
    "decorator": _fmt("#B45309"),
    "type": _fmt("#0E7490"),
    "operator": _fmt("#475569"),
}


class _CodeHighlighter(QSyntaxHighlighter):
    def __init__(self, parent=None, lang: str = "plaintext"):
        super().__init__(parent)
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = []
        self._multi_start: QRegularExpression | None = None
        self._multi_end: QRegularExpression | None = None
        self._multi_fmt: QTextCharFormat = _THEME["comment"]
        self.set_language(lang)

    def set_language(self, lang: str) -> None:
        self._rules.clear()
        self._multi_start = None
        self._multi_end = None

        keywords = _LANG_KEYWORDS.get(lang, [])
        if keywords:
            kw_pattern = r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b"
            flags = "" if lang == "sql" else ""
            if lang == "sql":
                self._rules.append((QRegularExpression(f"(?i){kw_pattern}"), _THEME["keyword"]))
            else:
                self._rules.append((QRegularExpression(kw_pattern), _THEME["keyword"]))

        # numbers
        self._rules.append((QRegularExpression(r"\b\d+\.?\d*(?:[eE][+-]?\d+)?\b"), _THEME["number"]))
        self._rules.append((QRegularExpression(r"\b0[xX][0-9a-fA-F]+\b"), _THEME["number"]))

        # strings
        self._rules.append((QRegularExpression(r'"[^"\\]*(\\.[^"\\]*)*"'), _THEME["string"]))
        self._rules.append((QRegularExpression(r"'[^'\\]*(\\.[^'\\]*)*'"), _THEME["string"]))
        if lang in ("javascript", "typescript"):
            self._rules.append((QRegularExpression(r"`[^`\\]*(\\.[^`\\]*)*`"), _THEME["string"]))

        # decorators (python)
        if lang == "python":
            self._rules.append((QRegularExpression(r"@\w+(\.\w+)*"), _THEME["decorator"]))

        # function calls
        self._rules.append((QRegularExpression(r"\b\w+(?=\s*\()"), _THEME["function"]))

        # single-line comments
        if lang in ("python", "shell", "ruby", "yaml", "toml", "ini", "cmake"):
            self._rules.append((QRegularExpression(r"#.*$"), _THEME["comment"]))
        elif lang in ("c", "cpp", "java", "javascript", "typescript", "go", "rust", "kotlin", "dart", "swift", "scss", "less"):
            self._rules.append((QRegularExpression(r"//.*$"), _THEME["comment"]))
        elif lang == "sql":
            self._rules.append((QRegularExpression(r"--.*$"), _THEME["comment"]))
        elif lang == "lua":
            self._rules.append((QRegularExpression(r"--.*$"), _THEME["comment"]))

        # multi-line comments
        if lang in ("c", "cpp", "java", "javascript", "typescript", "go", "rust", "kotlin", "dart", "swift", "css", "scss", "less"):
            self._multi_start = QRegularExpression(r"/\*")
            self._multi_end = QRegularExpression(r"\*/")
        elif lang == "python":
            self._multi_start = QRegularExpression(r'"""')
            self._multi_end = QRegularExpression(r'"""')
        elif lang == "html" or lang == "xml":
            self._multi_start = QRegularExpression(r"<!--")
            self._multi_end = QRegularExpression(r"-->")

        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self._rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)

        # multi-line comment/string handling
        if self._multi_start is None:
            return
        self.setCurrentBlockState(0)
        start_idx = 0
        if self.previousBlockState() != 1:
            m = self._multi_start.match(text)
            start_idx = m.capturedStart() if m.hasMatch() else -1
        while start_idx >= 0:
            m_end = self._multi_end.match(text, start_idx + 1)
            if not m_end.hasMatch() or m_end.capturedStart() < start_idx:
                self.setCurrentBlockState(1)
                length = len(text) - start_idx
            else:
                length = m_end.capturedEnd() - start_idx
            self.setFormat(start_idx, length, self._multi_fmt)
            m = self._multi_start.match(text, start_idx + length)
            start_idx = m.capturedStart() if m.hasMatch() else -1


class _LineNumberArea(QWidget):
    def __init__(self, editor: "_CodeEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return self._editor._line_number_area_size()

    def paintEvent(self, event):
        self._editor._paint_line_numbers(event)


class _CodeEdit(QPlainTextEdit):
    """带行号、代码补全的编辑器。"""

    save_requested = Signal()
    _jedi_done = Signal(int, list)  # (请求号, 候选)，工作线程 → UI 线程

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(32)

        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self._update_line_area_width()

        self._file_path = ""
        self._lang = "plaintext"
        self._comp_req = 0
        self._comp_model = QStringListModel(self)
        self._completer = QCompleter(self._comp_model, self)
        self._completer.setWidget(self)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.activated.connect(self._insert_completion)
        self._jedi_done.connect(self._on_jedi_done)
        self._comp_timer = QTimer(self)
        self._comp_timer.setSingleShot(True)
        self._comp_timer.setInterval(180)
        self._comp_timer.timeout.connect(self._request_completions)

        self._bg = "#FFFFFF"
        self._fg = "#1E293B"
        self._sel_bg = "#B4D5FE"
        self._ln_bg = "#F3F6FA"
        self._ln_fg = "#94A3B8"
        self._apply_editor_style()

    def _apply_editor_style(self):
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {self._bg};
                color: {self._fg};
                selection-background-color: {self._sel_bg};
                border: none;
                padding: 4px 0px 4px 0px;
            }}
        """)
        if hasattr(self, "_completer"):
            self._completer.popup().setStyleSheet(
                f"QListView {{ background: {self._bg}; color: {self._fg};"
                f" selection-background-color: {self._sel_bg};"
                f" border: 1px solid {self._ln_fg}; font-family: monospace; }}"
            )

    # —— 补全 ——

    def _word_prefix(self) -> str:
        cur = self.textCursor()
        text = cur.block().text()[: cur.positionInBlock()]
        m = re.search(r"[A-Za-z_][A-Za-z0-9_]*$", text)
        return m.group(0) if m else ""

    def _dot_context(self) -> bool:
        cur = self.textCursor()
        text = cur.block().text()[: cur.positionInBlock()]
        stripped = text[: len(text) - len(self._word_prefix())]
        return stripped.endswith(".")

    def _include_prefix(self) -> str | None:
        """光标处于 #include <…> / \"…\" 内时返回已输入的头文件前缀。"""
        if self._lang not in ("c", "cpp"):
            return None
        cur = self.textCursor()
        text = cur.block().text()[: cur.positionInBlock()]
        m = _INCLUDE_RE.match(text)
        return m.group(1) if m else None

    def keyPressEvent(self, e):
        if e.modifiers() & Qt.ControlModifier and e.key() == Qt.Key_S:
            self.save_requested.emit()
            e.accept()
            return
        popup = self._completer.popup()
        if popup.isVisible() and e.key() in (
            Qt.Key_Enter,
            Qt.Key_Return,
            Qt.Key_Tab,
            Qt.Key_Backtab,
            Qt.Key_Escape,
        ):
            e.ignore()
            return
        super().keyPressEvent(e)
        if self.isReadOnly():
            return
        t = e.text()
        in_include = self._include_prefix() is not None
        typing_ident = bool(t) and (t[-1].isalnum() or t[-1] in "_.")
        typing_include = bool(t) and in_include and t[-1] in '<"/'
        if typing_ident or typing_include:
            if popup.isVisible() and not in_include:
                prefix = self._word_prefix()
                self._completer.setCompletionPrefix(prefix)
                if self._completer.completionCount() == 0:
                    popup.hide()
            self._comp_timer.start()
        elif e.key() == Qt.Key_Backspace and popup.isVisible():
            prefix = self._word_prefix()
            if prefix:
                self._completer.setCompletionPrefix(prefix)
            else:
                popup.hide()
        elif popup.isVisible():
            popup.hide()

    def _request_completions(self):
        if self.isReadOnly():
            return
        # #include 上下文：补头文件名（前缀可含 . / 等非标识符字符）
        inc = self._include_prefix()
        if inc is not None:
            self._show_completions(_header_candidates(inc, self._lang), inc)
            return
        prefix = self._word_prefix()
        dot = self._dot_context()
        if self._lang == "python" and _jedi_ok():
            if not prefix and not dot:
                self._completer.popup().hide()
                return
            self._comp_req += 1
            cur = self.textCursor()
            _jedi_pool().submit(
                self._jedi_worker,
                self._comp_req,
                self.toPlainText(),
                cur.blockNumber() + 1,
                cur.positionInBlock(),
                self._file_path,
            )
            return
        # 有专用词典（内置或用户配置）的语言 1 字符即触发
        has_dict = self._lang in _EXTRA_WORDS or self._lang in _completion_config()["words"]
        min_len = 1 if has_dict else 2
        if len(prefix) < min_len:
            self._completer.popup().hide()
            return
        self._show_completions(self._fallback_names(prefix), prefix)

    def _jedi_worker(self, req: int, text: str, line: int, col: int, path: str):
        try:
            import jedi

            script = jedi.Script(code=text, path=path or None)
            names = [c.name for c in script.complete(line, col)[:200]]
        except Exception:
            names = []
        try:
            self._jedi_done.emit(req, names)
        except RuntimeError:
            pass  # 编辑器已销毁

    def _on_jedi_done(self, req: int, names: list):
        if req != self._comp_req:
            return
        prefix = self._word_prefix()
        if not names and len(prefix) >= 2:
            names = self._fallback_names(prefix)
        self._show_completions(names, prefix)

    def _fallback_names(self, prefix: str) -> list[str]:
        kws = set(_LANG_KEYWORDS.get(self._lang, []))
        kws |= set(_EXTRA_WORDS.get(self._lang, []))
        kws |= set(_completion_config()["words"].get(self._lang, []))
        words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", self.toPlainText()[:200_000]))
        low = prefix.lower()
        pool = sorted(
            (w for w in (kws | words) if w.lower().startswith(low) and w != prefix),
            key=str.lower,
        )
        return pool[:80]

    def _show_completions(self, names: list, prefix: str):
        popup = self._completer.popup()
        if not names or (len(names) == 1 and names[0] == prefix):
            popup.hide()
            return
        self._comp_model.setStringList(names)
        self._completer.setCompletionPrefix(prefix)
        if self._completer.completionCount() == 0:
            popup.hide()
            return
        popup.setCurrentIndex(self._completer.completionModel().index(0, 0))
        cr = self.cursorRect()
        cr.setWidth(
            popup.sizeHintForColumn(0)
            + popup.verticalScrollBar().sizeHint().width()
            + 24
        )
        self._completer.complete(cr)

    def _insert_completion(self, text: str):
        cur = self.textCursor()
        prefix = self._completer.completionPrefix()
        if prefix:
            cur.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        cur.insertText(text)
        self.setTextCursor(cur)

    def _line_number_area_size(self):
        from PySide6.QtCore import QSize
        digits = max(3, len(str(self.blockCount())))
        w = 12 + self.fontMetrics().horizontalAdvance("9") * digits
        return QSize(w, 0)

    def _update_line_area_width(self, _=0):
        w = self._line_number_area_size().width()
        self.setViewportMargins(w, 0, 0, 0)

    def _update_line_area(self, rect, dy):
        if dy:
            self._line_area.scroll(0, dy)
        else:
            self._line_area.update(0, rect.y(), self._line_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_area_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        w = self._line_number_area_size().width()
        self._line_area.setGeometry(cr.left(), cr.top(), w, cr.height())

    def _paint_line_numbers(self, event):
        from PySide6.QtGui import QPainter
        painter = QPainter(self._line_area)
        painter.fillRect(event.rect(), QColor(self._ln_bg))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        fg = QColor(self._ln_fg)
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.setPen(fg)
                painter.drawText(
                    0, top, self._line_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignRight | Qt.AlignVCenter,
                    str(num + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1
        painter.end()


class MonacoEditor(QWidget):
    """代码编辑器组件（QPlainTextEdit + 语法高亮）。

    API 与原 QWebEngineView 版本兼容。
    """
    content_changed = Signal()
    save_requested = Signal()
    ready = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        from agent.ui_fonts import mono_font_family
        self._edit = _CodeEdit(self)
        self._edit.setFont(QFont(mono_font_family(), 11))
        self._edit.setPlaceholderText("在文件目录双击文件即可编辑…")
        self._edit.textChanged.connect(self.content_changed.emit)
        self._edit.save_requested.connect(self.save_requested.emit)
        lay.addWidget(self._edit, 1)

        self._highlighter = _CodeHighlighter(self._edit.document(), "plaintext")

    def set_content(self, text: str, language: str = "plaintext") -> None:
        self._edit.blockSignals(True)
        self._edit.setPlainText(text)
        self._edit.blockSignals(False)
        self._edit._lang = language
        self._highlighter.set_language(language)

    def get_content(self, callback) -> None:
        callback(self._edit.toPlainText())

    def set_language(self, lang: str) -> None:
        self._edit._lang = lang
        self._highlighter.set_language(lang)

    def set_file_path(self, path: str) -> None:
        """补全（jedi）需要文件路径来解析项目内导入。"""
        self._edit._file_path = str(path or "")

    def get_view_state(self) -> dict:
        return {
            "cursor": self._edit.textCursor().position(),
            "scroll": self._edit.verticalScrollBar().value(),
        }

    def set_view_state(self, state: dict) -> None:
        try:
            pos = min(int(state.get("cursor", 0)), len(self._edit.toPlainText()))
            cur = self._edit.textCursor()
            cur.setPosition(max(0, pos))
            self._edit.setTextCursor(cur)
            self._edit.verticalScrollBar().setValue(int(state.get("scroll", 0)))
        except Exception:
            pass

    def set_read_only(self, read_only: bool) -> None:
        self._edit.setReadOnly(read_only)

    def set_font_size(self, size: int) -> None:
        f = self._edit.font()
        f.setPointSize(size)
        self._edit.setFont(f)

    def set_theme(self, *, bg: str = "#FFFFFF", fg: str = "#1E293B",
                  sel_bg: str = "#B4D5FE", ln_bg: str = "#F3F6FA",
                  ln_fg: str = "#94A3B8") -> None:
        """设置编辑器色彩风格。"""
        self._edit._bg = bg
        self._edit._fg = fg
        self._edit._sel_bg = sel_bg
        self._edit._ln_bg = ln_bg
        self._edit._ln_fg = ln_fg
        self._edit._apply_editor_style()
