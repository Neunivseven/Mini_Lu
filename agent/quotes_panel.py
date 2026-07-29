"""待机语录面板：查看 / 添加 / 删除 / 开关。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent import quotes_store
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import confirm, warn
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at


class QuotesPanel(QWidget):
    closed = Signal()
    settings_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setStyleSheet(
            """
            QWidget#root {
                background: #FFF8F0;
                border: 2px solid #3D3D3D;
                border-radius: 12px;
            }
            QLabel#title { color: #2A2A2A; font-weight: 700; }
            QLabel#hint { color: #666; font-size: 11px; }
            QListWidget {
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 4px;
                outline: none;
            }
            QListWidget::item {
                padding: 8px 6px;
                border-bottom: 1px solid #F0E6D8;
                color: #222;
            }
            QListWidget::item:selected { background: #E8F0FE; color: #111; }
            QLineEdit {
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 6px 8px;
                color: #222;
            }
            QPushButton {
                background: #4A90D9;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #3A7BC8; }
            QPushButton#ghost {
                background: #F0EBE3;
                color: #444;
                font-weight: 500;
            }
            QPushButton#ghost:hover { background: #E4DDD2; color: #111; }
            QPushButton#danger { background: #C75B5B; }
            QPushButton#danger:hover { background: #B04949; }
            QPushButton#closeBtn {
                background: transparent; color: #666;
                font-size: 14px; padding: 2px 8px;
            }
            QPushButton#closeBtn:hover { color: #111; background: #EEE; }
            QCheckBox { color: #333; }
            """
        )

        root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        title = QLabel("待机语录")
        title.setObjectName("title")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.hide_panel)
        header = build_panel_header(title, close_btn)
        lay.addWidget(header)

        self.enabled_cb = QCheckBox("启用待机语录（空闲时偶尔冒泡）")
        self.enabled_cb.stateChanged.connect(self._on_toggle_enabled)
        lay.addWidget(self.enabled_cb)

        hint = QLabel("默认语录来自 config/quotes.yaml；可在下方手动添加。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.list = QListWidget()
        lay.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("写一句新语录…")
        self.input.returnPressed.connect(self._on_add)
        add_row.addWidget(self.input, 1)
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self._on_add)
        add_row.addWidget(add_btn)
        lay.addLayout(add_row)

        btn_row = QHBoxLayout()
        del_btn = QPushButton("删除选中")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(del_btn)
        reset_btn = QPushButton("恢复默认")
        reset_btn.setObjectName("ghost")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.reload()
        attach_move_resize(
            self,
            header,
            width=360,
            height=440,
            min_width=300,
            min_height=340,
        )
        seal_hidden_toplevel(self)

    def reload(self):
        settings = quotes_store.get_settings()
        self.enabled_cb.blockSignals(True)
        self.enabled_cb.setChecked(bool(settings.get("enabled", True)))
        self.enabled_cb.blockSignals(False)
        self.list.clear()
        for item in quotes_store.list_quotes():
            src = "默认" if item.get("source") == "default" else "自定义"
            on = "开" if item.get("enabled", True) else "关"
            text = item.get("text") or ""
            label = f"[{src}/{on}] {text}"
            row = QListWidgetItem(label)
            row.setData(Qt.UserRole, item.get("id"))
            row.setToolTip(text)
            self.list.addItem(row)

    def _on_toggle_enabled(self, state: int):
        quotes_store.set_enabled(state == Qt.Checked)
        self.settings_changed.emit()

    def _on_add(self):
        text = self.input.text().strip()
        if not text:
            return
        try:
            quotes_store.add_quote(text)
        except ValueError as e:
            warn(self, "无法添加", str(e))
            return
        self.input.clear()
        self.reload()
        self.settings_changed.emit()

    def _on_delete(self):
        row = self.list.currentItem()
        if not row:
            return
        qid = row.data(Qt.UserRole)
        if not qid:
            return
        if not confirm(self, "删除语录", "确定删除这条语录？", yes_text="删除", danger=True):
            return
        quotes_store.delete_quote(str(qid))
        self.reload()
        self.settings_changed.emit()

    def _on_reset(self):
        if not confirm(
            self,
            "恢复默认",
            "将清空当前列表并用 config/quotes.yaml 重新初始化，继续？",
            yes_text="恢复",
            danger=True,
        ):
            return
        quotes_store.reset_to_defaults()
        self.reload()
        self.settings_changed.emit()

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
