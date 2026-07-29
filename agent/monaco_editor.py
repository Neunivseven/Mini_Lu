"""代码编辑器组件：QPlainTextEdit + 语法高亮。

替代 QWebEngineView/Monaco 方案，避免与 termqt forkpty 冲突。
"""
from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

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
    return _EXT_LANG.get(p.suffix.lower(), "plaintext")


# ── 语法高亮规则 ──

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

_LANG_KEYWORDS: dict[str, list[str]] = {
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
        if lang in ("python", "shell", "ruby", "yaml", "toml", "ini"):
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
    """带行号和暗色主题的代码编辑器。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(32)

        self._line_area = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_area_width)
        self.updateRequest.connect(self._update_line_area)
        self._update_line_area_width()

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
        lay.addWidget(self._edit, 1)

        self._highlighter = _CodeHighlighter(self._edit.document(), "plaintext")

    def set_content(self, text: str, language: str = "plaintext") -> None:
        self._edit.blockSignals(True)
        self._edit.setPlainText(text)
        self._edit.blockSignals(False)
        self._highlighter.set_language(language)

    def get_content(self, callback) -> None:
        callback(self._edit.toPlainText())

    def set_language(self, lang: str) -> None:
        self._highlighter.set_language(lang)

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
