"""记忆面板：LangGraph Store（长期）说明 + 条目查看/清空。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.lg_runtime import (
    checkpoint_db_path,
    clear_long_term_store,
    list_long_term_items,
    store_db_path,
)
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import confirm, inform
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at

CLOTH = "#8EB4D8"
CREAM = "#FFF8F0"
INK = "#2C2420"


class MemoryPanel(QWidget):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        
        self.setStyleSheet(
            f"""
            QWidget#memRoot {{
                background: {CREAM};
                border: 2px solid #3D3D3D;
                border-radius: 12px;
            }}
            QLabel#title {{ color: {INK}; font-weight: 700; }}
            QLabel#meta {{ color: #666; font-size: 11px; }}
            QTextEdit {{
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 8px;
                color: {INK};
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
        root.setObjectName("memRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        title = QLabel("记忆 · LangGraph")
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

        self.body = QTextEdit()
        self.body.setReadOnly(True)
        self.body.setFont(QFont("Microsoft YaHei UI", 9))
        layout.addWidget(self.body, stretch=1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("ghost")
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_reset = QPushButton("清空长期…")
        self.btn_reset.setObjectName("danger")
        self.btn_reset.clicked.connect(self._reset)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.btn_reset)
        layout.addLayout(row)
        attach_move_resize(
            self,
            header,
            width=400,
            height=460,
            min_width=320,
            min_height=360,
        )
        seal_hidden_toplevel(self)

    def reload(self):
        self.hint.setText(
            "短时：Checkpointer（按会话 thread_id）\n"
            f"  {checkpoint_db_path()}\n"
            "长时：Store（跨会话，下列条目）\n"
            f"  {store_db_path()}"
        )
        items = list_long_term_items(80)
        if not items:
            self.body.setPlainText("（Store 暂无长期记忆；可对 Agent 说「记住…」）")
            return
        lines = []
        for it in items:
            lines.append(f"[{it.get('key')}] {it.get('text')}")
            if it.get("updated_at"):
                lines.append(f"    · {it['updated_at']}")
        self.body.setPlainText("\n".join(lines))

    def _reset(self):
        if not confirm(
            self,
            "清空长期记忆",
            "清空 LangGraph Store 中的全部长期记忆？\n（当前会话短时对话不会删除）",
            yes_text="清空",
            danger=True,
        ):
            return
        n = clear_long_term_store()
        self.reload()
        inform(self, "记忆", f"已清空 {n} 条。")

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
