"""工作区面板：打开文件夹 / 管理可读写根目录（类似 VS Code Open Folder）。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent.file_workspace import (
    add_workspace_root,
    clear_active_workspace,
    get_active_root,
    list_user_roots,
    load_workspace_roots,
    remove_workspace_root,
    set_active_workspace,
)
from agent.llm_client import app_dir
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import inform, warn
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at

CLOTH = "#8EB4D8"
CREAM = "#FFF8F0"
INK = "#2C2420"


class WorkspacePanel(QWidget):
    closed = Signal()
    changed = Signal()  # 根目录或当前项目变更

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        
        self.setStyleSheet(
            f"""
            QWidget#wsRoot {{
                background: {CREAM};
                border: 2px solid #3D3D3D;
                border-radius: 12px;
            }}
            QLabel#title {{ color: {INK}; font-weight: 700; }}
            QLabel#meta {{ color: #666; font-size: 11px; }}
            QListWidget {{
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 4px;
                color: {INK};
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-radius: 6px;
            }}
            QListWidget::item:selected {{
                background: {CLOTH};
                color: white;
            }}
            QPushButton {{
                background: {CLOTH};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 600;
            }}
            QPushButton:hover {{ background: #6A96C0; }}
            QPushButton#ghost {{
                background: #F0EBE3;
                color: #444;
                font-weight: 500;
            }}
            QPushButton#danger {{ background: #C75B5B; }}
            QPushButton#danger:hover {{ background: #B04949; }}
            QPushButton#closeBtn {{
                background: transparent;
                color: #666;
                font-size: 14px;
                padding: 2px 8px;
            }}
            QPushButton#closeBtn:hover {{ color: {INK}; background: #EEE; }}
            """
        )

        root = QWidget(self)
        root.setObjectName("wsRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        title = QLabel("工作区")
        title.setObjectName("title")
        title.setFont(QFont("Microsoft YaHei UI", 11))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self.hide_panel)
        header = build_panel_header(title, close_btn)
        layout.addWidget(header)

        self.hint = QLabel("")
        self.hint.setObjectName("meta")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

        self.list = QListWidget()
        self.list.setFont(QFont("Microsoft YaHei UI", 9))
        self.list.itemDoubleClicked.connect(self._activate_selected)
        layout.addWidget(self.list, stretch=1)

        row1 = QHBoxLayout()
        btn_open = QPushButton("打开文件夹…")
        btn_open.setToolTip("选择项目文件夹并设为当前（类似 VS Code）")
        btn_open.clicked.connect(self.pick_open_folder)
        btn_add = QPushButton("添加文件夹…")
        btn_add.setObjectName("ghost")
        btn_add.setToolTip("加入白名单，不切换当前项目")
        btn_add.clicked.connect(self.pick_add_folder)
        btn_file = QPushButton("从文件…")
        btn_file.setObjectName("ghost")
        btn_file.setToolTip("选一个文件，把它所在目录加入并设为当前")
        btn_file.clicked.connect(self.pick_from_file)
        row1.addWidget(btn_open)
        row1.addWidget(btn_add)
        row1.addWidget(btn_file)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        btn_act = QPushButton("设为当前")
        btn_act.setObjectName("ghost")
        btn_act.clicked.connect(self._activate_selected)
        btn_clear = QPushButton("清除当前")
        btn_clear.setObjectName("ghost")
        btn_clear.clicked.connect(self._clear_active)
        btn_rm = QPushButton("移除")
        btn_rm.setObjectName("danger")
        btn_rm.clicked.connect(self._remove_selected)
        btn_refresh = QPushButton("刷新")
        btn_refresh.setObjectName("ghost")
        btn_refresh.clicked.connect(self.reload)
        row2.addWidget(btn_act)
        row2.addWidget(btn_clear)
        row2.addWidget(btn_rm)
        row2.addWidget(btn_refresh)
        layout.addLayout(row2)
        attach_move_resize(
            self,
            header,
            width=420,
            height=420,
            min_width=340,
            min_height=320,
        )
        seal_hidden_toplevel(self)

    def reload(self):
        active = get_active_root()
        self.hint.setText(
            f"当前项目：{active if active else '未设置'}。"
            "相对路径会相对「当前项目」解析；应用目录始终可访问。"
        )
        self.list.clear()
        app = str(app_dir().resolve())
        for r in load_workspace_roots():
            s = str(r)
            label = s
            tags = []
            if active and r.resolve() == active.resolve():
                tags.append("当前")
            if r.resolve() == app_dir().resolve():
                tags.append("内置")
            if tags:
                label = f"[{' · '.join(tags)}] {s}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, s)
            # 内置不可当「用户根」移除，但可选为当前
            self.list.addItem(item)
            if active and r.resolve() == active.resolve():
                item.setSelected(True)

    def _selected_path(self) -> str | None:
        items = self.list.selectedItems()
        if not items:
            return None
        return items[0].data(Qt.UserRole)

    def pick_open_folder(self):
        start = str(get_active_root() or Path.home())
        path = QFileDialog.getExistingDirectory(self, "打开文件夹（设为当前项目）", start)
        if not path:
            return
        try:
            add_workspace_root(path, set_active=True)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.reload()
        self.changed.emit()

    def pick_add_folder(self):
        start = str(get_active_root() or Path.home())
        path = QFileDialog.getExistingDirectory(self, "添加文件夹到工作区", start)
        if not path:
            return
        try:
            add_workspace_root(path, set_active=False)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.reload()
        self.changed.emit()

    def pick_from_file(self):
        start = str(get_active_root() or Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "选择文件（使用其所在目录）", start)
        if not path:
            return
        folder = str(Path(path).resolve().parent)
        try:
            add_workspace_root(folder, set_active=True)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.reload()
        self.changed.emit()

    def _activate_selected(self):
        path = self._selected_path()
        if not path:
            inform(self, "工作区", "请先选中一个目录。")
            return
        try:
            set_active_workspace(path)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.reload()
        self.changed.emit()

    def _clear_active(self):
        clear_active_workspace()
        self.reload()
        self.changed.emit()

    def _remove_selected(self):
        path = self._selected_path()
        if not path:
            return
        if Path(path).resolve() == app_dir().resolve():
            inform(self, "工作区", "内置应用目录不能移除。")
            return
        if path not in list_user_roots() and Path(path).resolve() not in {
            Path(r).resolve() for r in list_user_roots()
        }:
            # 可能是仅 active 指向内置
            pass
        try:
            remove_workspace_root(path)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.reload()
        self.changed.emit()

    def show_panel(self):
        prepare_toplevel_show(self, activate=True)
        self.reload()
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_panel(self):
        self.hide()
        self.closed.emit()

    def place_near(self, global_x: int, global_y: int, pet_w: int = 200, pet_h: int = 260):
        from PySide6.QtCore import QPoint

        from agent.hover_tip import screen_geometry_at

        x = global_x - self.width() - 8
        y = global_y + 10
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        if x < screen.left() + 8:
            x = global_x + pet_w + 8
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
