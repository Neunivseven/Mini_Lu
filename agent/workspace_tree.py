"""工作区文件树：浏览 / 新建 / 删除 / 复制粘贴。"""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from agent.ui_dialogs import ask_choice, ask_text, confirm, inform, warn
from agent.ui_fonts import ui_font, ui_font_family
from agent.ui_zoom import pt, px

_SKIP_DIRS = {
    ".git",
    ".svn",
    ".hg",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    ".idea",
    ".vs",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    ".pet_edit",
}

_SKIP_SUFFIX = {".pet.before", ".pyc", ".pyo", ".obj", ".o", ".exe", ".dll", ".so"}

MAX_CHILDREN = 400

TREE_BG = "#F8FBFE"
TREE_BORDER = "#B6C8D8"
TREE_TEXT = "#1E293B"
TREE_MUTED = "#64748B"
TREE_SEL = "#C8DEF0"
TREE_HOVER = "#E4EEF6"
TREE_FOLDER = "#2F6A8C"
TREE_ACCENT = "#3D7EA6"


def _safe_name(name: str) -> str | None:
    name = (name or "").strip().strip("/\\")
    if not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name or "\0" in name:
        return None
    if name.startswith(".") and name not in (".github", ".gitignore", ".env.example"):
        # 允许常见点文件，禁止 .. 等
        if ".." in name:
            return None
    return name


class WorkspaceFileTree(QWidget):
    """工作区目录树；双击文件发出 file_open_requested。"""

    file_open_requested = Signal(str)
    refresh_requested = Signal()
    tree_changed = Signal()  # 本地增删改后通知

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root: Path | None = None
        self._clip_paths: list[Path] = []  # 内部剪贴板（复制/剪切源）
        self._clip_cut = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(4)
        self.root_lab = QLabel("（未设置工作区）")
        self.root_lab.setObjectName("meta")
        self.root_lab.setWordWrap(True)
        self.root_lab.setStyleSheet(f"color: {TREE_FOLDER};")
        head.addWidget(self.root_lab, 1)

        self.btn_new = QPushButton()
        self.btn_new.setObjectName("ghost")
        self.btn_new.setFixedSize(28, 28)
        self.btn_new.setToolTip("新建文件或文件夹")
        self.btn_new.clicked.connect(self._action_new)
        self.btn_del = QPushButton()
        self.btn_del.setObjectName("ghost")
        self.btn_del.setFixedSize(28, 28)
        self.btn_del.setToolTip("删除选中项（Delete）")
        self.btn_del.clicked.connect(self._action_delete)
        self.btn_refresh = QPushButton()
        self.btn_refresh.setObjectName("ghost")
        self.btn_refresh.setFixedSize(28, 28)
        self.btn_refresh.setToolTip("刷新目录")
        self.btn_refresh.clicked.connect(self.reload)
        try:
            from agent.ui_icons import decorate_button

            decorate_button(self.btn_new, "plus", size=14, text="")
            decorate_button(self.btn_del, "discard", size=14, text="")
            decorate_button(self.btn_refresh, "refresh", size=14, text="")
        except Exception:
            self.btn_new.setText("+")
            self.btn_del.setText("⌫")
            self.btn_refresh.setText("↻")
        head.addWidget(self.btn_new)
        head.addWidget(self.btn_del)
        head.addWidget(self.btn_refresh)
        lay.addLayout(head)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(16)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_context_menu)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.itemDoubleClicked.connect(self._on_double)
        lay.addWidget(self.tree, 1)

        self.tip = QLabel("双击打开 · 右键更多 · Delete 删除")
        self.tip.setWordWrap(True)
        lay.addWidget(self.tip)

        self._apply_fonts()

        for key in (Qt.Key_Delete, Qt.Key_Backspace):
            sc = QShortcut(QKeySequence(key), self.tree)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(self._action_delete)

        sc_copy = QShortcut(QKeySequence.Copy, self.tree)
        sc_copy.setContext(Qt.WidgetWithChildrenShortcut)
        sc_copy.activated.connect(self._action_copy)
        sc_paste = QShortcut(QKeySequence.Paste, self.tree)
        sc_paste.setContext(Qt.WidgetWithChildrenShortcut)
        sc_paste.activated.connect(self._action_paste)

    def _tree_stylesheet(self) -> str:
        return f"""
            QTreeWidget {{
                background: {TREE_BG};
                border: 1px solid {TREE_BORDER};
                border-radius: 8px;
                padding: 6px 4px;
                color: {TREE_TEXT};
                outline: none;
                font-family: "{ui_font_family()}";
                font-size: {px(12)};
                font-weight: 500;
            }}
            QTreeWidget::item {{
                padding: 4px 6px;
                margin: 1px 2px;
                border-radius: 5px;
                min-height: {max(18, pt(22))}px;
            }}
            QTreeWidget::item:selected {{
                background: {TREE_SEL};
                color: #0F3A52;
            }}
            QTreeWidget::item:hover {{
                background: {TREE_HOVER};
            }}
            QTreeWidget::branch:has-children:!has-siblings:closed,
            QTreeWidget::branch:closed:has-children:has-siblings {{
                image: none;
                border-image: none;
            }}
            """

    def _apply_fonts(self) -> None:
        self.root_lab.setFont(ui_font(pt(10), QFont.DemiBold))
        self.tree.setFont(ui_font(pt(11)))
        self.tree.setStyleSheet(self._tree_stylesheet())
        self.tip.setStyleSheet(f"color: {TREE_MUTED}; font-size: {px(10)};")
        for b in (self.btn_new, self.btn_del, self.btn_refresh):
            b.setStyleSheet(
                f"""
                QPushButton#ghost {{
                    background: #E8EEF5;
                    border: 1px solid {TREE_BORDER};
                    border-radius: 6px;
                    color: {TREE_FOLDER};
                }}
                QPushButton#ghost:hover {{
                    background: {TREE_SEL};
                }}
                """
            )

    def apply_font_zoom(self) -> None:
        expanded: list[str] = []

        def _collect(item: QTreeWidgetItem):
            if item.isExpanded() and item.data(0, Qt.UserRole + 1) == "dir":
                p = item.data(0, Qt.UserRole)
                if p:
                    expanded.append(str(p))
            for i in range(item.childCount()):
                _collect(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            _collect(self.tree.topLevelItem(i))

        self._apply_fonts()
        self.reload()
        if expanded:
            self._restore_expanded(set(expanded))

    def _restore_expanded(self, paths: set[str]) -> None:
        def _walk(item: QTreeWidgetItem):
            p = item.data(0, Qt.UserRole)
            if p and str(p) in paths and item.data(0, Qt.UserRole + 1) == "dir":
                item.setExpanded(True)
            for i in range(item.childCount()):
                _walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            _walk(self.tree.topLevelItem(i))

    # —— 路径安全 ——

    def _under_root(self, path: Path) -> bool:
        if not self._root:
            return False
        try:
            path.resolve().relative_to(self._root.resolve())
            return True
        except Exception:
            return path.resolve() == self._root.resolve()

    def _selected_item(self) -> QTreeWidgetItem | None:
        items = self.tree.selectedItems()
        return items[0] if items else None

    def _selected_path(self) -> tuple[Path | None, str | None]:
        item = self._selected_item()
        if not item:
            return None, None
        raw = item.data(0, Qt.UserRole)
        kind = item.data(0, Qt.UserRole + 1)
        if not raw:
            return None, None
        return Path(str(raw)), str(kind) if kind else None

    def _target_dir(self) -> Path | None:
        """新建/粘贴的目标目录：选中文件夹则用其本身，选中文件则用父目录。"""
        if not self._root:
            return None
        path, kind = self._selected_path()
        if path is None:
            return self._root
        if kind == "dir":
            return path if path.is_dir() else self._root
        return path.parent if path.parent.is_dir() else self._root

    def _parent_item_for(self, directory: Path) -> QTreeWidgetItem | None:
        target = str(directory.resolve())

        def _walk(item: QTreeWidgetItem) -> QTreeWidgetItem | None:
            p = item.data(0, Qt.UserRole)
            if p and Path(str(p)).resolve() == Path(target) and item.data(0, Qt.UserRole + 1) == "dir":
                return item
            for i in range(item.childCount()):
                found = _walk(item.child(i))
                if found:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = _walk(self.tree.topLevelItem(i))
            if found:
                return found
        return None

    def _refresh_dir_node(self, directory: Path) -> None:
        item = self._parent_item_for(directory)
        if item is None:
            self.reload()
            return
        while item.childCount():
            item.takeChild(0)
        self._populate(item)
        item.setExpanded(True)

    # —— 加载 ——

    def reload(self):
        try:
            from agent.file_workspace import get_active_root

            root = get_active_root()
        except Exception:
            root = None
        self._root = root
        self.tree.clear()
        enabled = bool(root and root.is_dir())
        self.btn_new.setEnabled(enabled)
        self.btn_del.setEnabled(enabled)
        if not enabled:
            self.root_lab.setText("未设置工作区")
            self.root_lab.setToolTip("先在上方切换文件夹")
            return
        self.root_lab.setText(root.name)
        self.root_lab.setToolTip(str(root))
        top = QTreeWidgetItem([root.name])
        top.setData(0, Qt.UserRole, str(root))
        top.setData(0, Qt.UserRole + 1, "dir")
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        top.setForeground(0, QColor(TREE_FOLDER))
        top.addChild(QTreeWidgetItem(["…"]))
        self.tree.addTopLevelItem(top)
        top.setExpanded(True)

    def _on_expand(self, item: QTreeWidgetItem):
        if item.data(0, Qt.UserRole + 1) != "dir":
            return
        if item.childCount() == 1 and item.child(0).text(0) == "…":
            item.takeChild(0)
            self._populate(item)
        elif item.childCount() == 0:
            self._populate(item)

    def _populate(self, parent: QTreeWidgetItem):
        path_s = parent.data(0, Qt.UserRole)
        if not path_s:
            return
        p = Path(str(path_s))
        if not p.is_dir():
            return
        try:
            entries = sorted(
                p.iterdir(),
                key=lambda x: (not x.is_dir(), x.name.lower()),
            )
        except Exception:
            return
        n = 0
        for child in entries:
            if n >= MAX_CHILDREN:
                more = QTreeWidgetItem([f"…（超过 {MAX_CHILDREN} 项）"])
                more.setForeground(0, QColor(TREE_MUTED))
                parent.addChild(more)
                break
            name = child.name
            if name in _SKIP_DIRS or name.startswith("."):
                if name not in (".github",):
                    continue
            if any(name.endswith(suf) for suf in _SKIP_SUFFIX):
                continue
            if child.is_dir():
                node = QTreeWidgetItem([f"{name}/"])
                node.setData(0, Qt.UserRole, str(child))
                node.setData(0, Qt.UserRole + 1, "dir")
                f = node.font(0)
                f.setBold(True)
                node.setFont(0, f)
                node.setForeground(0, QColor(TREE_FOLDER))
                node.addChild(QTreeWidgetItem(["…"]))
                parent.addChild(node)
            else:
                node = QTreeWidgetItem([name])
                node.setData(0, Qt.UserRole, str(child))
                node.setData(0, Qt.UserRole + 1, "file")
                parent.addChild(node)
            n += 1

    def _on_double(self, item: QTreeWidgetItem, _col: int):
        kind = item.data(0, Qt.UserRole + 1)
        path = item.data(0, Qt.UserRole)
        if kind == "file" and path:
            self.file_open_requested.emit(str(path))

    # —— 右键菜单 ——

    def _on_context_menu(self, pos):
        if not self._root:
            return
        item = self.tree.itemAt(pos)
        if item:
            self.tree.setCurrentItem(item)

        menu = QMenu(self)
        menu.setStyleSheet(
            f"""
            QMenu {{
                background: #FFFFFF;
                border: 1px solid {TREE_BORDER};
                border-radius: 8px;
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 18px;
                border-radius: 4px;
                color: {TREE_TEXT};
            }}
            QMenu::item:selected {{
                background: {TREE_SEL};
            }}
            """
        )
        act_new_file = menu.addAction("新建文件…")
        act_new_dir = menu.addAction("新建文件夹…")
        menu.addSeparator()
        act_copy = menu.addAction("复制")
        act_paste = menu.addAction("粘贴")
        act_paste.setEnabled(bool(self._clip_paths))
        menu.addSeparator()
        act_path = menu.addAction("复制路径")
        act_rel = menu.addAction("复制相对路径")
        menu.addSeparator()
        act_del = menu.addAction("删除")
        act_open = menu.addAction("打开文件")

        path, kind = self._selected_path()
        act_open.setEnabled(kind == "file")
        act_del.setEnabled(bool(path) and path != self._root)
        act_copy.setEnabled(bool(path) and path != self._root)
        act_path.setEnabled(bool(path))
        act_rel.setEnabled(bool(path))

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_new_file:
            self._create_entry(as_dir=False)
        elif chosen == act_new_dir:
            self._create_entry(as_dir=True)
        elif chosen == act_copy:
            self._action_copy()
        elif chosen == act_paste:
            self._action_paste()
        elif chosen == act_path:
            self._action_copy_path(relative=False)
        elif chosen == act_rel:
            self._action_copy_path(relative=True)
        elif chosen == act_del:
            self._action_delete()
        elif chosen == act_open and path and kind == "file":
            self.file_open_requested.emit(str(path))

    # —— 操作 ——

    def _action_new(self):
        if not self._root:
            warn(self, "文件目录", "请先设置工作区。")
            return
        kind = ask_choice(
            self,
            "新建",
            "要创建文件还是文件夹？",
            choices=[
                ("file", "文件"),
                ("dir", "文件夹"),
                ("cancel", "取消"),
            ],
        )
        if kind == "cancel" or not kind:
            return
        self._create_entry(as_dir=(kind == "dir"))

    def _create_entry(self, *, as_dir: bool):
        parent_dir = self._target_dir()
        if not parent_dir or not self._under_root(parent_dir):
            warn(self, "新建", "无效的目标目录。")
            return
        title = "新建文件夹" if as_dir else "新建文件"
        name, ok = ask_text(
            self,
            title,
            f"将在以下目录创建：\n{parent_dir}",
            placeholder="folder-name" if as_dir else "notes.md",
            ok_text="创建",
        )
        if not ok:
            return
        safe = _safe_name(name)
        if not safe:
            warn(self, "新建", "名称无效（勿含路径分隔符，勿为空）。")
            return
        dest = parent_dir / safe
        if not self._under_root(dest):
            warn(self, "新建", "路径超出工作区。")
            return
        if dest.exists():
            warn(self, "新建", f"已存在：{safe}")
            return
        try:
            if as_dir:
                dest.mkdir(parents=False, exist_ok=False)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text("", encoding="utf-8")
        except Exception as e:
            warn(self, "新建", str(e))
            return
        self._refresh_dir_node(parent_dir)
        self.tree_changed.emit()
        if not as_dir:
            self.file_open_requested.emit(str(dest))

    def _action_delete(self):
        path, kind = self._selected_path()
        if not path or not self._root:
            return
        if path.resolve() == self._root.resolve():
            warn(self, "删除", "不能删除工作区根目录。")
            return
        if not self._under_root(path):
            warn(self, "删除", "路径不在工作区内。")
            return
        if not path.exists():
            self.reload()
            return
        label = f"{path.name}/" if kind == "dir" else path.name
        extra = "\n（将删除整个文件夹及其内容）" if kind == "dir" else ""
        if not confirm(
            self,
            "删除",
            f"确定删除？\n\n{label}\n{path}{extra}",
            yes_text="删除",
            danger=True,
        ):
            return
        parent = path.parent
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except Exception as e:
            warn(self, "删除", str(e))
            return
        self._refresh_dir_node(parent)
        self.tree_changed.emit()

    def _action_copy(self):
        path, _kind = self._selected_path()
        if not path or not self._root:
            return
        if path.resolve() == self._root.resolve():
            warn(self, "复制", "请选择工作区内的文件或子文件夹。")
            return
        if not self._under_root(path):
            return
        self._clip_paths = [path]
        self._clip_cut = False
        # 同步绝对路径到系统剪贴板，方便外用
        QGuiApplication.clipboard().setText(str(path))

    def _action_paste(self):
        if not self._clip_paths:
            inform(self, "粘贴", "剪贴板为空，请先复制文件或文件夹。")
            return
        dest_dir = self._target_dir()
        if not dest_dir or not self._under_root(dest_dir):
            warn(self, "粘贴", "无效的目标目录。")
            return
        for src in list(self._clip_paths):
            if not src.exists():
                warn(self, "粘贴", f"源已不存在：{src.name}")
                continue
            if not self._under_root(src):
                continue
            name = src.name
            dest = dest_dir / name
            # 同目录粘贴时自动改名
            if dest.resolve() == src.resolve() or dest.exists():
                stem = src.stem if src.is_file() else src.name
                suffix = src.suffix if src.is_file() else ""
                n = 1
                while True:
                    candidate = (
                        dest_dir / f"{stem} - 副本{n}{suffix}"
                        if src.is_file()
                        else dest_dir / f"{src.name} - 副本{n}"
                    )
                    if not candidate.exists():
                        dest = candidate
                        break
                    n += 1
            if not self._under_root(dest):
                warn(self, "粘贴", "目标超出工作区。")
                continue
            try:
                if src.is_dir():
                    shutil.copytree(src, dest)
                else:
                    shutil.copy2(src, dest)
                if self._clip_cut:
                    if src.is_dir():
                        shutil.rmtree(src)
                    else:
                        src.unlink(missing_ok=True)
            except Exception as e:
                warn(self, "粘贴", str(e))
                return
        if self._clip_cut:
            self._clip_paths = []
            self._clip_cut = False
            self.reload()
        else:
            self._refresh_dir_node(dest_dir)
        self.tree_changed.emit()

    def _action_copy_path(self, *, relative: bool):
        path, _ = self._selected_path()
        if not path:
            return
        if relative and self._root:
            try:
                text = str(path.resolve().relative_to(self._root.resolve()))
            except Exception:
                text = str(path)
        else:
            text = str(path.resolve())
        QGuiApplication.clipboard().setText(text)
        inform(self, "路径", f"已复制到剪贴板：\n{text}")
