"""桌宠悬停帮助卡片：圆角、暖色，替代系统灰框 ToolTip。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

SKIN = "#DEB49E"
SKIN_DEEP = "#C8957A"
CLOTH = "#8EB4D8"
CREAM = "#FFF8F2"
INK = "#2C2420"
MUTED = "#7A6A60"


def screen_geometry_at(global_pos: QPoint):
    """返回 global_pos 所在显示器的可用区域（双屏时勿用 primaryScreen）。"""
    from PySide6.QtWidgets import QApplication

    screen = QApplication.screenAt(global_pos)
    if screen is None:
        screen = QApplication.primaryScreen()
    return screen.availableGeometry()


def seal_hidden_toplevel(widget) -> None:
    """顶层 Tool/Window 构建完成后调用，避免 Windows 上短暂闪现。"""
    from PySide6.QtCore import Qt

    widget.setAttribute(Qt.WA_ShowWithoutActivating, True)
    widget.setAttribute(Qt.WA_DontShowOnScreen, True)
    widget.hide()


def prepare_toplevel_show(widget, *, activate: bool = False) -> None:
    """show / show_panel 前解除 seal。

    activate=True 时允许激活窗口（工具面板需要，便于拖拽/输入）；
    HoverTip 等保持 False，避免抢焦点。
    """
    from PySide6.QtCore import Qt

    widget.setAttribute(Qt.WA_DontShowOnScreen, False)
    if activate:
        widget.setAttribute(Qt.WA_ShowWithoutActivating, False)


# (短标签, 说明)
TIP_ROWS = (
    ("拖拽", "左键拖动，踱步中心会跟着变"),
    ("单击", "开心一下，再点屏幕 → 横向走过去"),
    ("双击", "打开聊天；回复在旁侧气泡或工作台"),
    ("取名", "右键「给它取名…」自定义称呼"),
    ("记事", "右键「查看记事内容」"),
    ("记忆", "短期对话 + 长期压缩；可提纯/改写"),
    ("语录", "空闲冒泡；右键「待机语录」可增删"),
    ("闹钟", "到期时暖色气泡提醒"),
    ("菜单", "右键：聊天 / 工作台 / 记事 / 记忆 / 语录"),
    ("Esc", "取消点选，或退出"),
)


class HoverTip(QWidget):
    """圆角帮助卡片，跟随光标旁显示。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._radius = 14
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(0)

        card = QWidget()
        card.setObjectName("tipCard")
        # 实际绘制在 paintEvent；这里用透明子布局放文字
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)

        title = QLabel("Mini_Lu 操作")
        title.setFont(QFont("Microsoft YaHei UI", 11, QFont.Bold))
        title.setStyleSheet(f"color: {INK}; background: transparent;")
        lay.addWidget(title)
        self._title_lab = title
        self._refresh_title()

        for tag, desc in TIP_ROWS:
            row = QHBoxLayout()
            row.setSpacing(8)
            badge = QLabel(tag)
            badge.setFont(QFont("Microsoft YaHei UI", 8, QFont.Bold))
            badge.setAlignment(Qt.AlignCenter)
            badge.setFixedHeight(22)
            badge.setMinimumWidth(40)
            badge.setStyleSheet(
                f"""
                color: white;
                background: {CLOTH};
                border-radius: 8px;
                padding: 0 8px;
                """
            )
            text = QLabel(desc)
            text.setFont(QFont("Microsoft YaHei UI", 9))
            text.setWordWrap(True)
            text.setStyleSheet(f"color: {INK}; background: transparent;")
            row.addWidget(badge, 0, Qt.AlignTop)
            row.addWidget(text, 1)
            lay.addLayout(row)

        outer.addWidget(card)
        self._card = card
        self.adjustSize()
        self.setFixedWidth(300)
        self.adjustSize()

        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self._do_show)
        self._anchor = QPoint()
        from agent.hover_tip import seal_hidden_toplevel

        seal_hidden_toplevel(self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 阴影
        shadow = QPainterPath()
        r = QRectF(4, 5, self.width() - 8, self.height() - 8)
        shadow.addRoundedRect(r, self._radius, self._radius)
        painter.fillPath(shadow, QColor(0, 0, 0, 36))

        # 卡片底
        body = QPainterPath()
        br = QRectF(2, 2, self.width() - 6, self.height() - 6)
        body.addRoundedRect(br, self._radius, self._radius)
        painter.fillPath(body, QColor(CREAM))
        painter.setPen(QPen(QColor(SKIN_DEEP), 1.8))
        painter.drawPath(body)

        # 顶部暖色细条
        bar = QRectF(18, 10, self.width() - 40, 3)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(SKIN))
        painter.drawRoundedRect(bar, 2, 2)

    def _refresh_title(self):
        lab = getattr(self, "_title_lab", None)
        if lab is None:
            return
        try:
            from agent.identity import display_name, product_name

            name = display_name()
            pname = product_name()
            lab.setText(f"{pname} · {name}" if name != pname else f"{pname} 操作")
        except Exception:
            lab.setText("Mini_Lu 操作")

    def schedule_show(self, global_pos: QPoint, delay_ms: int = 450):
        self._anchor = QPoint(global_pos)
        self._show_timer.start(delay_ms)

    def cancel(self):
        self._show_timer.stop()
        self.hide()

    def _do_show(self):
        self._refresh_title()
        prepare_toplevel_show(self)
        self.adjustSize()
        screen = screen_geometry_at(self._anchor)
        x = self._anchor.x() + 16
        y = self._anchor.y() + 20
        if x + self.width() > screen.right() - 8:
            x = self._anchor.x() - self.width() - 12
        if y + self.height() > screen.bottom() - 8:
            y = self._anchor.y() - self.height() - 12
        x = max(screen.left() + 6, min(x, screen.right() - self.width() - 6))
        y = max(screen.top() + 6, min(y, screen.bottom() - self.height() - 6))
        self.move(x, y)
        self.show()
        self.raise_()
