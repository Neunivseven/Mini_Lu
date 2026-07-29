"""聊天 / Agent 列表面板：多会话切换 + 消息回看（类 Cursor New Agent）。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent import chat_history
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import ask_text, confirm
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at

CLOTH = "#8EB4D8"
CREAM = "#FFF8F0"
INK = "#2C2420"


class ChatHistoryPanel(QWidget):
    closed = Signal()
    session_changed = Signal(str)  # session_id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self._view_sid: str | None = None
        self.setStyleSheet(
            f"""
            QWidget#root {{
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
                outline: none;
            }}
            QListWidget::item {{
                padding: 8px 6px;
                border-bottom: 1px solid #F0E6D8;
                color: #222;
            }}
            QListWidget::item:selected {{ background: #E8F0FE; }}
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
                background: #F0EBE3; color: #444; font-weight: 500;
            }}
            QPushButton#danger {{ background: #C75B5B; }}
            QPushButton#danger:hover {{ background: #B04949; }}
            QPushButton#closeBtn {{
                background: transparent; color: #666;
                font-size: 14px; padding: 2px 8px;
            }}
            QPushButton#closeBtn:hover {{ color: #111; background: #EEE; }}
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

        title = QLabel("对话 / Agent")
        title.setObjectName("title")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.hide_panel)
        header = build_panel_header(title, close_btn)
        lay.addWidget(header)

        meta = QLabel("左侧切换对话（互不串上下文）；＋新建类似 Cursor New Agent。")
        meta.setObjectName("meta")
        meta.setWordWrap(True)
        lay.addWidget(meta)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)
        self.session_list = QListWidget()
        self.session_list.currentItemChanged.connect(self._on_session_select)
        left_lay.addWidget(self.session_list, 1)
        srow = QHBoxLayout()
        btn_new = QPushButton("＋ 新对话")
        btn_new.clicked.connect(self._on_new)
        btn_rename = QPushButton("重命名")
        btn_rename.setObjectName("ghost")
        btn_rename.clicked.connect(self._on_rename)
        btn_del = QPushButton("删除")
        btn_del.setObjectName("danger")
        btn_del.clicked.connect(self._on_delete)
        srow.addWidget(btn_new)
        srow.addWidget(btn_rename)
        srow.addWidget(btn_del)
        left_lay.addLayout(srow)
        split.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(6)
        self.msg_list = QListWidget()
        self.msg_list.currentItemChanged.connect(self._on_msg_select)
        right_lay.addWidget(self.msg_list, 1)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFont(QFont("Microsoft YaHei UI", 10))
        right_lay.addWidget(self.detail, 1)
        mrow = QHBoxLayout()
        refresh = QPushButton("刷新")
        refresh.setObjectName("ghost")
        refresh.clicked.connect(self.reload)
        clear_btn = QPushButton("清空本对话")
        clear_btn.setObjectName("danger")
        clear_btn.clicked.connect(self._on_clear)
        mrow.addWidget(refresh)
        mrow.addWidget(clear_btn)
        mrow.addStretch()
        right_lay.addLayout(mrow)
        split.addWidget(right)

        split.setSizes([200, 300])
        lay.addWidget(split, 1)
        attach_move_resize(
            self,
            header,
            width=520,
            height=560,
            min_width=400,
            min_height=420,
        )
        seal_hidden_toplevel(self)

    def _selected_session_id(self) -> str | None:
        it = self.session_list.currentItem()
        return it.data(Qt.UserRole) if it else self._view_sid

    def reload(self):
        active = chat_history.get_active_id()
        prefer = self._view_sid or active
        self.session_list.blockSignals(True)
        self.session_list.clear()
        select_row = 0
        for i, s in enumerate(chat_history.list_sessions()):
            mark = "★ " if s.get("active") else ""
            label = f"{mark}{s.get('title')}  ({s.get('message_count')}条)"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, s["id"])
            item.setToolTip(f"{s['id']}\n更新 {s.get('updated_at')}")
            self.session_list.addItem(item)
            if s["id"] == prefer:
                select_row = i
        self.session_list.blockSignals(False)
        if self.session_list.count():
            self.session_list.setCurrentRow(select_row)
            self._load_messages(self._selected_session_id())

    def _load_messages(self, sid: str | None):
        self._view_sid = sid
        self.msg_list.clear()
        self.detail.clear()
        if not sid:
            return
        for m in reversed(chat_history.list_messages(200, session_id=sid)):
            role = m.get("role") or "assistant"
            tag = {
                "user": "我",
                "assistant": "Mini_Lu",
                "alarm": "闹钟",
                "system": "系统",
            }.get(role, role)
            if role == "assistant":
                try:
                    from agent.identity import assistant_label

                    tag = assistant_label()
                except Exception:
                    pass
            text = (m.get("text") or "").replace("\n", " ")
            if len(text) > 42:
                text = text[:41] + "…"
            ts = m.get("ts") or ""
            item = QListWidgetItem(f"[{ts}] {tag}：{text}")
            item.setData(Qt.UserRole, m.get("id"))
            item.setToolTip(m.get("text") or "")
            self.msg_list.addItem(item)

    def _on_session_select(self, current: QListWidgetItem | None, _prev):
        if not current:
            return
        sid = current.data(Qt.UserRole)
        self._load_messages(sid)
        # 点击即切换为当前 Agent 会话
        if sid and sid != chat_history.get_active_id():
            chat_history.switch_session(str(sid))
            self.session_changed.emit(str(sid))
            self.reload()

    def _on_msg_select(self, current: QListWidgetItem | None, _prev):
        if not current:
            return
        mid = current.data(Qt.UserRole)
        m = chat_history.get_message(str(mid), session_id=self._view_sid)
        if m:
            self._show_detail(m)

    def _show_detail(self, m: dict):
        role = m.get("role") or ""
        tag = {
            "user": "我",
            "assistant": "Mini_Lu",
            "alarm": "闹钟",
            "system": "系统",
        }.get(role, role)
        if role == "assistant":
            try:
                from agent.identity import assistant_label

                tag = assistant_label()
            except Exception:
                pass
        self.detail.setPlainText(
            f"{tag} · {m.get('ts') or ''}\n\n{m.get('text') or ''}"
        )

    def show_message_id(self, msg_id: str):
        self.reload()
        for i in range(self.msg_list.count()):
            it = self.msg_list.item(i)
            if it and it.data(Qt.UserRole) == msg_id:
                self.msg_list.setCurrentItem(it)
                return
        m = chat_history.get_message(msg_id)
        if m:
            self._show_detail(m)

    def show_plain_text(self, title: str, text: str):
        self.reload()
        self.detail.setPlainText(f"{title}\n\n{text}")

    def _on_new(self):
        text, ok = ask_text(
            self,
            "新对话",
            "给这次对话起个名字：",
            text="新对话",
            placeholder="例如：修登录页 / 整理笔记",
            ok_text="创建",
        )
        if not ok:
            return
        title = (text or "").strip() or "新对话"
        s = chat_history.create_session(title, activate=True)
        self._view_sid = s["id"]
        self.session_changed.emit(s["id"])
        self.reload()

    def _on_rename(self):
        sid = self._selected_session_id()
        if not sid:
            return
        cur = next((x for x in chat_history.list_sessions() if x["id"] == sid), None)
        text, ok = ask_text(
            self,
            "重命名对话",
            "新的对话标题：",
            text=(cur or {}).get("title") or "",
            ok_text="保存",
        )
        if not ok:
            return
        chat_history.rename_session(sid, text)
        self.reload()

    def _on_delete(self):
        sid = self._selected_session_id()
        if not sid:
            return
        if not confirm(
            self,
            "删除对话",
            "删除该对话及其消息？（Goal 一并丢弃）",
            yes_text="删除",
            danger=True,
        ):
            return
        try:
            from agent.goal_store import clear_goal

            clear_goal(session_id=sid)
        except Exception:
            pass
        chat_history.delete_session(sid)
        self._view_sid = chat_history.get_active_id()
        self.session_changed.emit(self._view_sid)
        self.reload()

    def _on_clear(self):
        sid = self._selected_session_id() or chat_history.get_active_id()
        if not confirm(
            self,
            "清空消息",
            "清空当前对话的全部消息？",
            yes_text="清空",
            danger=True,
        ):
            return
        chat_history.clear_history(session_id=sid)
        self.reload()

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

        x = global_x + pet_w + 10
        y = global_y
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        if x + self.width() > screen.right() - 8:
            x = global_x - self.width() - 10
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
