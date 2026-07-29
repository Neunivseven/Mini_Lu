"""
统一弹窗：与 Mini_Lu 工具面板同一套奶油底 + 衣着蓝。
替代系统默认 QInputDialog / 生硬 QMessageBox。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.ui_fonts import ui_font

CREAM = "#FFF8F0"
INK = "#2C2420"
MUTED = "#666666"
BORDER = "#3D3D3D"
CLOTH = "#8EB4D8"
CLOTH_DEEP = "#6A96C0"
GHOST = "#F0EBE3"
GHOST_HOVER = "#E0D8CC"
DANGER = "#C75B5B"
DANGER_HOVER = "#B04949"
FIELD_BORDER = "#D0C4B0"

_DIALOG_SS = f"""
QDialog#softDialog {{
    background: transparent;
}}
QWidget#softCard {{
    background: {CREAM};
    border: 2px solid {BORDER};
    border-radius: 14px;
}}
QLabel#softTitle {{
    color: {INK};
    font-weight: 700;
}}
QLabel#softBody {{
    color: {MUTED};
}}
QLineEdit#softField, QTextEdit#softField {{
    background: #FFFFFF;
    border: 1px solid {FIELD_BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    color: {INK};
    selection-background-color: #D7E8F4;
}}
QLineEdit#softField:focus, QTextEdit#softField:focus {{
    border: 1px solid {CLOTH};
}}
QPushButton#softPrimary {{
    background: {CLOTH};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 700;
    min-width: 72px;
}}
QPushButton#softPrimary:hover {{ background: {CLOTH_DEEP}; }}
QPushButton#softGhost {{
    background: {GHOST};
    color: #333;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
    min-width: 72px;
}}
QPushButton#softGhost:hover {{ background: {GHOST_HOVER}; }}
QPushButton#softDanger {{
    background: {DANGER};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 700;
    min-width: 72px;
}}
QPushButton#softDanger:hover {{ background: {DANGER_HOVER}; }}
"""


class SoftDialog(QDialog):
    """无边框卡片弹窗基类。"""

    def __init__(self, parent=None, *, width: int = 400):
        flags = Qt.Dialog | Qt.FramelessWindowHint
        super().__init__(parent, flags)
        self.setObjectName("softDialog")
        self.setModal(True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(_DIALOG_SS)
        self.setMinimumWidth(width)
        self._drag_off: QPoint | None = None
        self.installEventFilter(self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QWidget(self)
        self.card.setObjectName("softCard")
        self.card.setCursor(Qt.SizeAllCursor)
        self.card.setToolTip("按住空白处拖动")
        outer.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(10)
        # 卡片空白处也可拖（对齐工具面板顶栏体验）
        self.card.installEventFilter(self)

    def add_title(self, text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("softTitle")
        lab.setFont(ui_font(13, QFont.Bold))
        lab.setCursor(Qt.SizeAllCursor)
        lab.setToolTip("按住拖动")
        lab.installEventFilter(self)
        self.body.addWidget(lab)
        self._title_lab = lab
        return lab

    def add_body(self, text: str, *, rich: bool = False) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("softBody")
        lab.setWordWrap(True)
        lab.setFont(ui_font(10))
        if rich:
            lab.setTextFormat(Qt.RichText)
            lab.setOpenExternalLinks(True)
            lab.setStyleSheet(f"QLabel#softBody {{ color: {INK}; }}")
        self.body.addWidget(lab)
        return lab

    def add_buttons(self, *buttons: QPushButton) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.body.addLayout(row)

    def center_on_parent(self) -> None:
        self.adjustSize()
        parent = self.parentWidget()
        if parent is not None and parent.isVisible():
            pg = parent.frameGeometry()
            g = self.frameGeometry()
            g.moveCenter(pg.center())
            self.move(g.topLeft())
        else:
            from PySide6.QtGui import QGuiApplication

            screen = QGuiApplication.primaryScreen()
            if screen:
                ag = screen.availableGeometry()
                g = self.frameGeometry()
                g.moveCenter(ag.center())
                self.move(g.topLeft())

    def eventFilter(self, obj, event):
        title = getattr(self, "_title_lab", None)
        drag_objs = {title, getattr(self, "card", None)}

        if obj in drag_objs and isinstance(event, QMouseEvent):
            et = event.type()
            if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                if obj is self.card:
                    child = self.card.childAt(event.position().toPoint())
                    # 点到输入框/按钮不拖
                    from PySide6.QtWidgets import QAbstractButton, QLineEdit, QTextEdit

                    w = child
                    while w is not None and w is not self.card:
                        if isinstance(w, (QAbstractButton, QLineEdit, QTextEdit)):
                            return False
                        w = w.parentWidget()
                self._drag_off = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self.grabMouse()
                return True
            if (
                et == QEvent.Type.MouseMove
                and self._drag_off is not None
                and event.buttons() & Qt.LeftButton
            ):
                self.move(event.globalPosition().toPoint() - self._drag_off)
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag_off = None
                try:
                    self.releaseMouse()
                except RuntimeError:
                    pass
        # grabMouse 后移动/释放落在 dialog 自身
        if self._drag_off is not None and obj is self and isinstance(event, QMouseEvent):
            et = event.type()
            if et == QEvent.Type.MouseMove and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_off)
                return True
            if et == QEvent.Type.MouseButtonRelease:
                self._drag_off = None
                try:
                    self.releaseMouse()
                except RuntimeError:
                    pass
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


def _btn(text: str, role: str = "primary") -> QPushButton:
    b = QPushButton(text)
    b.setObjectName(
        {
            "primary": "softPrimary",
            "ghost": "softGhost",
            "danger": "softDanger",
        }.get(role, "softPrimary")
    )
    b.setCursor(Qt.PointingHandCursor)
    b.setFont(ui_font(10, QFont.Bold))
    return b


def ask_text(
    parent,
    title: str,
    label: str = "",
    *,
    text: str = "",
    placeholder: str = "",
    ok_text: str = "确定",
    cancel_text: str = "取消",
    width: int = 420,
) -> tuple[str, bool]:
    """单行输入。返回 (text, ok)。"""
    dlg = SoftDialog(parent, width=width)
    dlg.add_title(title)
    if (label or "").strip():
        dlg.add_body(label.strip())

    field = QLineEdit()
    field.setObjectName("softField")
    field.setFont(ui_font(11))
    field.setText(text or "")
    if placeholder:
        field.setPlaceholderText(placeholder)
    field.selectAll()
    dlg.body.addWidget(field)

    ok_btn = _btn(ok_text, "primary")
    cancel_btn = _btn(cancel_text, "ghost")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    field.returnPressed.connect(dlg.accept)
    dlg.add_buttons(cancel_btn, ok_btn)

    dlg.center_on_parent()
    field.setFocus()
    if dlg.exec() == QDialog.Accepted:
        return field.text(), True
    return text, False


def ask_multiline(
    parent,
    title: str,
    label: str = "",
    *,
    text: str = "",
    ok_text: str = "确定",
    cancel_text: str = "取消",
    width: int = 460,
    height: int = 160,
) -> tuple[str, bool]:
    dlg = SoftDialog(parent, width=width)
    dlg.add_title(title)
    if (label or "").strip():
        dlg.add_body(label.strip())

    field = QTextEdit()
    field.setObjectName("softField")
    field.setFont(ui_font(11))
    field.setPlainText(text or "")
    field.setMinimumHeight(height)
    dlg.body.addWidget(field)

    ok_btn = _btn(ok_text, "primary")
    cancel_btn = _btn(cancel_text, "ghost")
    ok_btn.clicked.connect(dlg.accept)
    cancel_btn.clicked.connect(dlg.reject)
    dlg.add_buttons(cancel_btn, ok_btn)

    dlg.center_on_parent()
    field.setFocus()
    if dlg.exec() == QDialog.Accepted:
        return field.toPlainText(), True
    return text, False


def inform(
    parent,
    title: str,
    message: str,
    *,
    ok_text: str = "好的",
    rich: bool = False,
    width: int = 400,
) -> None:
    dlg = SoftDialog(parent, width=width)
    dlg.add_title(title)
    dlg.add_body(message, rich=rich)
    ok_btn = _btn(ok_text, "primary")
    ok_btn.clicked.connect(dlg.accept)
    dlg.add_buttons(ok_btn)
    dlg.center_on_parent()
    dlg.exec()


def warn(
    parent,
    title: str,
    message: str,
    *,
    ok_text: str = "知道了",
    width: int = 400,
) -> None:
    inform(parent, title, message, ok_text=ok_text, width=width)


def confirm(
    parent,
    title: str,
    message: str,
    *,
    yes_text: str = "确定",
    no_text: str = "取消",
    danger: bool = False,
) -> bool:
    dlg = SoftDialog(parent, width=400)
    dlg.add_title(title)
    dlg.add_body(message)
    yes_btn = _btn(yes_text, "danger" if danger else "primary")
    no_btn = _btn(no_text, "ghost")
    yes_btn.clicked.connect(dlg.accept)
    no_btn.clicked.connect(dlg.reject)
    dlg.add_buttons(no_btn, yes_btn)
    dlg.center_on_parent()
    return dlg.exec() == QDialog.Accepted


def ask_choice(
    parent,
    title: str,
    message: str,
    *,
    choices: list[tuple[str, str]],
    detail: str = "",
    width: int = 460,
) -> str:
    """
    choices: [(id, label), ...] 如 [("allow","运行"), ("always","总是允许"), ("deny","取消")]
    返回选中的 id；取消窗口则返回最后一个（通常 deny）。
    """
    dlg = SoftDialog(parent, width=width)
    dlg.add_title(title)
    if (message or "").strip():
        dlg.add_body(message.strip())
    if (detail or "").strip():
        field = QTextEdit()
        field.setObjectName("softField")
        field.setReadOnly(True)
        field.setPlainText(detail)
        field.setMinimumHeight(90)
        dlg.body.addWidget(field)

    picked = {"id": choices[-1][0] if choices else "deny"}

    def _pick(cid: str):
        picked["id"] = cid
        dlg.accept()

    buttons = []
    for i, (cid, label) in enumerate(choices):
        role = "ghost" if i == 0 and len(choices) > 1 else "primary"
        if cid in ("deny", "cancel", "no"):
            role = "ghost"
        if cid in ("always",):
            role = "primary"
        if cid in ("deny",) and len(choices) > 2:
            role = "ghost"
        b = _btn(label, role)
        b.clicked.connect(lambda _=False, c=cid: _pick(c))
        buttons.append(b)
    dlg.add_buttons(*buttons)
    dlg.center_on_parent()
    dlg.exec()
    return str(picked["id"])


def show_choice(
    parent,
    title: str,
    message: str,
    *,
    choices: list[tuple[str, str]],
    on_pick,
    detail: str = "",
    width: int = 460,
) -> SoftDialog:
    """
    非阻塞版 ask_choice。选中或关闭时回调 on_pick(choice_id)。
    关闭/Esc 时回调最后一个 choice（通常 deny）。
    """
    dlg = SoftDialog(parent, width=width)
    dlg.add_title(title)
    if (message or "").strip():
        dlg.add_body(message.strip())
    if (detail or "").strip():
        field = QTextEdit()
        field.setObjectName("softField")
        field.setReadOnly(True)
        field.setPlainText(detail)
        field.setMinimumHeight(90)
        dlg.body.addWidget(field)

    default_id = choices[-1][0] if choices else "deny"
    settled = {"done": False}

    def _finish(cid: str):
        if settled["done"]:
            return
        settled["done"] = True
        try:
            on_pick(cid)
        except Exception:
            pass
        dlg.accept()

    buttons = []
    for i, (cid, label) in enumerate(choices):
        role = "ghost" if i == 0 and len(choices) > 1 else "primary"
        if cid in ("deny", "cancel", "no"):
            role = "ghost"
        if cid in ("always",):
            role = "primary"
        if cid in ("deny",) and len(choices) > 2:
            role = "ghost"
        b = _btn(label, role)
        b.clicked.connect(lambda _=False, c=cid: _finish(c))
        buttons.append(b)
    dlg.add_buttons(*buttons)

    def _on_reject():
        if not settled["done"]:
            settled["done"] = True
            try:
                on_pick(default_id)
            except Exception:
                pass

    dlg.rejected.connect(_on_reject)
    dlg.center_on_parent()
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg
