"""记事本 UI：记事/闹钟列表（简略）→ 详情；支持手动删除。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.notes_store import delete_note, get_note, list_notes
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import confirm
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at


class NotesPanel(QWidget):
    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setStyleSheet(
            """
            QWidget#notesRoot {
                background: #FFF8F0;
                border: 2px solid #3D3D3D;
                border-radius: 12px;
            }
            QLabel#title {
                color: #2A2A2A;
                font-weight: 700;
            }
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
            QListWidget::item:selected {
                background: #E8F0FE;
                color: #111;
            }
            QTextEdit {
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 8px;
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
            QPushButton#ghost:checked {
                background: #4A90D9;
                color: white;
            }
            QPushButton#danger {
                background: #C75B5B;
            }
            QPushButton#danger:hover { background: #B04949; }
            QPushButton#closeBtn {
                background: transparent;
                color: #666;
                font-size: 14px;
                padding: 2px 8px;
            }
            QPushButton#closeBtn:hover { color: #111; background: #EEE; }
            QLabel#meta { color: #666; font-size: 11px; }
            """
        )

        root = QWidget(self)
        root.setObjectName("notesRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)

        self.title = QLabel("记事本")
        self.title.setObjectName("title")
        self.title.setFont(QFont("Microsoft YaHei UI", 11))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setFixedWidth(28)
        close_btn.clicked.connect(self.hide_panel)
        header = build_panel_header(self.title, close_btn)
        layout.addWidget(header)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack, stretch=1)

        # --- 列表页 ---
        list_page = QWidget()
        list_lay = QVBoxLayout(list_page)
        list_lay.setContentsMargins(0, 0, 0, 0)

        filter_row = QHBoxLayout()
        self._filter = "all"  # all | note | alarm
        self.btn_all = QPushButton("全部")
        self.btn_note = QPushButton("记事")
        self.btn_alarm = QPushButton("闹钟")
        for b, key in (
            (self.btn_all, "all"),
            (self.btn_note, "note"),
            (self.btn_alarm, "alarm"),
        ):
            b.setObjectName("ghost")
            b.setCheckable(True)
            b.clicked.connect(lambda checked=False, k=key: self._set_filter(k))
            filter_row.addWidget(b)
        filter_row.addStretch()
        list_lay.addLayout(filter_row)

        hint = QLabel("点击查看全文 · Delete 删除选中")
        hint.setObjectName("meta")
        hint.setFont(QFont("Microsoft YaHei UI", 9))
        list_lay.addWidget(hint)

        self.list = QListWidget()
        self.list.setFont(QFont("Microsoft YaHei UI", 9))
        self.list.itemClicked.connect(self._on_item_clicked)
        list_lay.addWidget(self.list, stretch=1)

        list_btns = QHBoxLayout()
        refresh_btn = QPushButton("刷新")
        refresh_btn.setObjectName("ghost")
        refresh_btn.clicked.connect(self.reload)
        del_btn = QPushButton("删除选中")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._delete_selected)
        list_btns.addWidget(refresh_btn)
        list_btns.addWidget(del_btn)
        list_lay.addLayout(list_btns)
        self.stack.addWidget(list_page)

        # --- 详情页 ---
        detail_page = QWidget()
        detail_lay = QVBoxLayout(detail_page)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        self.detail_summary = QLabel()
        self.detail_summary.setFont(QFont("Microsoft YaHei UI", 10, QFont.Bold))
        self.detail_summary.setWordWrap(True)
        detail_lay.addWidget(self.detail_summary)
        self.detail_meta = QLabel()
        self.detail_meta.setObjectName("meta")
        self.detail_meta.setWordWrap(True)
        detail_lay.addWidget(self.detail_meta)
        self.detail_body = QTextEdit()
        self.detail_body.setReadOnly(True)
        self.detail_body.setFont(QFont("Microsoft YaHei UI", 9))
        detail_lay.addWidget(self.detail_body, stretch=1)
        detail_btns = QHBoxLayout()
        back_btn = QPushButton("← 返回")
        back_btn.clicked.connect(self._back_to_list)
        detail_del = QPushButton("删除此条")
        detail_del.setObjectName("danger")
        detail_del.clicked.connect(self._delete_current)
        detail_btns.addWidget(back_btn)
        detail_btns.addWidget(detail_del)
        detail_lay.addLayout(detail_btns)
        self.stack.addWidget(detail_page)

        self._current_id: str | None = None
        QShortcut(QKeySequence.Delete, self.list, activated=self._delete_selected)
        self._set_filter("all")
        attach_move_resize(
            self,
            header,
            width=360,
            height=420,
            min_width=300,
            min_height=320,
        )
        seal_hidden_toplevel(self)

    def _set_filter(self, key: str):
        self._filter = key
        self.btn_all.setChecked(key == "all")
        self.btn_note.setChecked(key == "note")
        self.btn_alarm.setChecked(key == "alarm")
        self.reload()

    def _item_line(self, n: dict) -> str:
        if n.get("kind") == "alarm" and n.get("alarm_enabled"):
            prefix = "[闹钟] "
            at = n.get("remind_at") or ""
            short = at[5:16] if len(at) >= 16 else at
            if n.get("alarm_mode") == "repeat":
                rule = {
                    "daily": "每天",
                    "weekly": "每周",
                    "weekdays": "工作日",
                    "monthly": "每月",
                }.get(n.get("repeat") or "", "重复")
                suffix = f"  · {rule} {short}"
            else:
                suffix = f"  · 一次 {short}"
        else:
            prefix = "[记事] "
            suffix = "  · 已响过" if n.get("reminded") else ""
        return f"{prefix}{n.get('summary', '')}{suffix}"

    def reload(self):
        self.list.clear()
        kind = None if self._filter == "all" else self._filter
        items = list_notes(100, kind=kind)
        if not items:
            empty = QListWidgetItem("（暂无内容）")
            empty.setFlags(Qt.NoItemFlags)
            self.list.addItem(empty)
            return
        for n in items:
            item = QListWidgetItem(self._item_line(n))
            item.setData(Qt.UserRole, n.get("id"))
            item.setToolTip((n.get("content") or "")[:200])
            self.list.addItem(item)

    def open_note(self, note_id: str):
        note = get_note(note_id)
        if not note:
            return
        self._current_id = note_id
        self.detail_summary.setText(note.get("summary") or "记事")
        if note.get("kind") == "alarm" and note.get("alarm_enabled"):
            if note.get("alarm_mode") == "repeat":
                rem = (
                    f"长期闹钟 · {note.get('repeat')} · 下次 {note.get('remind_at')}"
                )
            else:
                rem = f"一次性闹钟 · {note.get('remind_at')}"
        elif note.get("reminded"):
            rem = f"纯记事（闹钟已响过 {note.get('reminded_at', '')}）"
        else:
            rem = "纯记事（无闹钟）"
        self.detail_meta.setText(f"创建 {note.get('created', '')}\n{rem}")
        self.detail_body.setPlainText(note.get("content") or "")
        self.title.setText("详情")
        self.stack.setCurrentIndex(1)

    def _on_item_clicked(self, item: QListWidgetItem):
        nid = item.data(Qt.UserRole)
        if nid:
            self.open_note(str(nid))

    def _confirm_delete(self, note_id: str) -> bool:
        note = get_note(note_id)
        name = (note or {}).get("summary") or note_id
        return confirm(
            self,
            "删除确认",
            f"确定删除「{name}」？\n此操作不可恢复。",
            yes_text="删除",
            danger=True,
        )

    def _delete_selected(self):
        item = self.list.currentItem()
        if not item:
            return
        nid = item.data(Qt.UserRole)
        if not nid:
            return
        if not self._confirm_delete(str(nid)):
            return
        if delete_note(str(nid)):
            self.reload()

    def _delete_current(self):
        if not self._current_id:
            return
        if not self._confirm_delete(self._current_id):
            return
        if delete_note(self._current_id):
            self._back_to_list()

    def _back_to_list(self):
        self._current_id = None
        self.title.setText("记事本")
        self.stack.setCurrentIndex(0)
        self.reload()

    def show_panel(self):
        prepare_toplevel_show(self, activate=True)
        self._back_to_list()
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
        y = global_y + pet_h // 2 - self.height() // 2
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        if x < screen.left() + 8:
            x = global_x + pet_w + 8
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
