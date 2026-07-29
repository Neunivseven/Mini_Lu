"""微信风格聊天气泡：配色取自 Q版卡通（暖肤色 + 衣着蓝）。"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from agent.ui_fonts import ui_font, ui_font_family

# 形象参考色
SKIN = "#DEB49E"
SKIN_DEEP = "#C8957A"
CLOTH = "#8EB4D8"
CLOTH_DEEP = "#6A96C0"
CREAM = "#FFF8F2"
INK = "#2C2420"
ALARM_BG = "#FFE4D4"
ALARM_BORDER = "#E8A070"
QUOTE_BG = "#FFF3E0"
QUOTE_BORDER = "#E09A5A"
QUOTE_INK = "#3A2418"

# 气泡预览上限（超出可点击看全文）
PREVIEW_CHARS = 360
PREVIEW_MAX_H = 280

# 待机语录：醒目字体候选（按偏好顺序，取系统已安装的第一个）
_QUOTE_FONT_CANDIDATES = (
    "华文琥珀",
    "STHupo",
    "幼圆",
    "YouYuan",
    "华文行楷",
    "STXingkai",
    "楷体",
    "KaiTi",
    "Noto Serif CJK SC",
    "Noto Sans CJK SC",
    "WenQuanYi Micro Hei",
    "Microsoft YaHei UI",
)
_quote_font_family: str | None = None


def _resolve_quote_font_family() -> str:
    global _quote_font_family
    if _quote_font_family:
        return _quote_font_family
    available = set(QFontDatabase.families())
    for name in _QUOTE_FONT_CANDIDATES:
        if name in available:
            _quote_font_family = name
            return name
    _quote_font_family = ui_font_family()
    return _quote_font_family


class ChatBubble(QWidget):
    """单条气泡窗口；role=assistant|user|alarm|thinking|quote。"""

    closed = Signal(object)
    open_full = Signal(str, str)  # text, role

    def __init__(self, text: str, role: str = "assistant", parent=None):
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        # thinking 不抢鼠标；其它气泡可点开全文
        if role == "thinking":
            flags |= Qt.WindowTransparentForInput
        # 构造时即带 flags，避免先出现带标题栏的默认 Window 再重建
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        if role != "thinking":
            self.setCursor(Qt.PointingHandCursor)

        self.role = role
        self._raw = (text or "").strip() or "…"
        if role == "quote":
            self._font = QFont(_resolve_quote_font_family(), 14)
            self._font.setBold(True)
            self._max_text_w = 280
            self._pad_x = 16
            self._pad_y = 12
        else:
            self._font = ui_font(10)
            self._max_text_w = 260
            self._pad_x = 14
            self._pad_y = 10
        self._radius = 14
        self._tail = 8
        self._truncated = False
        self._layout_size()

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._auto_close)
        self.hide()

    def _colors(self) -> tuple[QColor, QColor, QColor]:
        if self.role == "user":
            return QColor(CLOTH), QColor(CLOTH_DEEP), QColor("#1A2A38")
        if self.role == "alarm":
            return QColor(ALARM_BG), QColor(ALARM_BORDER), QColor(INK)
        if self.role == "thinking":
            return QColor(CREAM), QColor(SKIN), QColor("#8A7A70")
        if self.role == "quote":
            return QColor(QUOTE_BG), QColor(QUOTE_BORDER), QColor(QUOTE_INK)
        # assistant
        return QColor(CREAM), QColor(SKIN_DEEP), QColor(INK)

    def _display_text(self) -> str:
        t = self._raw
        if self.role == "alarm" and not t.startswith("⏰"):
            t = f"⏰ {t}"
        self._truncated = False
        if len(t) > PREVIEW_CHARS:
            t = t[: PREVIEW_CHARS - 1].rstrip() + "…"
            self._truncated = True
        # 按高度再截断一次，避免气泡过高挡住桌面
        fm = QFontMetrics(self._font)
        br = fm.boundingRect(0, 0, self._max_text_w, 8000, Qt.TextWordWrap, t)
        if br.height() > PREVIEW_MAX_H:
            # 二分逼近高度上限
            lo, hi = 40, len(t)
            best = t[:40] + "…"
            while lo <= hi:
                mid = (lo + hi) // 2
                cand = t[:mid].rstrip() + "…"
                h = fm.boundingRect(
                    0, 0, self._max_text_w, 8000, Qt.TextWordWrap, cand
                ).height()
                if h <= PREVIEW_MAX_H:
                    best = cand
                    lo = mid + 1
                else:
                    hi = mid - 1
            t = best
            self._truncated = True
        if self._truncated and self.role != "thinking":
            t = t.rstrip("…") + "…\n（点击查看全文）"
        return t

    def _layout_size(self):
        fm = QFontMetrics(self._font)
        text = self._display_text()
        br = fm.boundingRect(0, 0, self._max_text_w, 2000, Qt.TextWordWrap, text)
        tw = min(self._max_text_w, max(48, br.width()))
        th = max(fm.height(), min(br.height(), PREVIEW_MAX_H + 40))
        # 气泡本体 + 左侧/右侧小尾巴
        w = tw + self._pad_x * 2 + self._tail + 4
        h = th + self._pad_y * 2 + 4
        self._text_w = tw
        self._text_h = th
        self.setFixedSize(w, h)

    def show_timed(self, ms: int = 10000):
        from agent.hover_tip import prepare_toplevel_show

        prepare_toplevel_show(self)
        self._layout_size()
        self.show()
        self.raise_()
        if ms > 0:
            self._hide_timer.start(ms)
        else:
            self._hide_timer.stop()

    def refresh_text(self, text: str, role: str | None = None):
        self._raw = (text or "").strip() or "…"
        if role:
            self.role = role
        self._layout_size()
        self.update()

    def _auto_close(self):
        self.hide()
        self.closed.emit(self)

    def dismiss(self):
        self._hide_timer.stop()
        self.hide()
        self.closed.emit(self)

    def mousePressEvent(self, event):
        if self.role != "thinking" and event.button() == Qt.LeftButton:
            self.open_full.emit(self._raw, self.role)
            # 点开全文后暂停自动关闭，方便对照
            self._hide_timer.stop()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        bg, border, fg = self._colors()

        # 阴影
        shadow = QPainterPath()
        body = self._body_path(offset=QPoint(1, 2))
        shadow.addPath(body)
        painter.fillPath(shadow, QColor(0, 0, 0, 28))

        path = self._body_path()
        painter.fillPath(path, bg)
        painter.setPen(QPen(border, 1.6))
        painter.drawPath(path)

        painter.setPen(fg)
        painter.setFont(self._font)
        # 文本区：assistant/alarm 靠左（尾巴在左下），user 靠右（尾巴在右下）
        if self.role == "user":
            tx = 4
        else:
            tx = self._tail + 2
        rect = QRectF(tx + self._pad_x - 4, self._pad_y, self._text_w + 4, self._text_h + 2)
        painter.drawText(rect, Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop, self._display_text())

    def _body_path(self, offset: QPoint | None = None) -> QPainterPath:
        ox = offset.x() if offset else 0
        oy = offset.y() if offset else 0
        path = QPainterPath()
        # 圆角矩形主体
        if self.role == "user":
            left = 2 + ox
            right = self.width() - self._tail - 2 + ox
        else:
            left = self._tail + ox
            right = self.width() - 2 + ox
        top = 2 + oy
        bottom = self.height() - 2 + oy
        rect = QRectF(left, top, right - left, bottom - top)
        path.addRoundedRect(rect, self._radius, self._radius)

        # 小三角尾巴（朝向桌宠一侧）
        tail = QPainterPath()
        if self.role == "user":
            # 右下
            tip = QPoint(int(self.width() - 1 + ox), int(bottom - 10))
            a = QPoint(int(right - 1), int(bottom - 18))
            b = QPoint(int(right - 1), int(bottom - 4))
        else:
            # 左下，指向桌宠
            tip = QPoint(int(1 + ox), int(bottom - 10))
            a = QPoint(int(left + 1), int(bottom - 18))
            b = QPoint(int(left + 1), int(bottom - 4))
        tail.moveTo(tip)
        tail.lineTo(a)
        tail.lineTo(b)
        tail.closeSubpath()
        path = path.united(tail)
        return path


def display_ms_for_text(text: str, base: int = 10000) -> int:
    """按字数加长气泡停留时间。"""
    n = len((text or "").strip())
    extra = min(25000, max(0, (n - 80) * 40))
    return base + extra


class BubbleLane:
    """管理叠在桌宠旁的多条气泡，自动排布。"""

    def __init__(self):
        self._items: list[ChatBubble] = []
        self._thinking: ChatBubble | None = None
        self._pet_geo = (0, 0, 200, 260)
        self._avoid = None  # QRect：输入框等需避开的区域
        self.on_open_full = None  # Callable[[str, str], None]

    def set_pet_geo(self, x: int, y: int, w: int, h: int):
        self._pet_geo = (x, y, w, h)
        self.relayout()

    def set_avoid_rect(self, rect):
        """设置需避开的屏幕矩形（如聊天输入框）。"""
        self._avoid = rect
        self.relayout()

    def push(
        self,
        text: str,
        *,
        role: str = "assistant",
        ms: int = 10000,
        replace_thinking: bool = True,
    ) -> ChatBubble:
        if replace_thinking and self._thinking is not None:
            self._thinking.dismiss()
            self._thinking = None
        bubble = ChatBubble(text, role=role)
        bubble.closed.connect(self._on_closed)
        if role != "thinking":
            bubble.open_full.connect(self._emit_open_full)
        self._items.append(bubble)
        # 最多保留 4 条可见
        while len(self._items) > 4:
            old = self._items.pop(0)
            if old is not self._thinking:
                old.dismiss()
        # 先排布再显示，避免在 (0,0) 闪一下再跳到桌宠旁
        self.relayout()
        bubble.show_timed(ms)
        return bubble

    def _emit_open_full(self, text: str, role: str):
        if callable(self.on_open_full):
            self.on_open_full(text, role)

    def show_thinking(self, text: str | None = None):
        msg = (text or "").strip() or "正在想…"
        if self._thinking is not None and self._thinking.isVisible():
            try:
                self._thinking._raw = msg
                self._thinking._layout_size()
                self._thinking.update()
                self.relayout()
            except Exception:
                pass
            return
        self._thinking = self.push(msg, role="thinking", ms=0, replace_thinking=False)

    def clear_thinking(self):
        if self._thinking is not None:
            if self._thinking in self._items:
                self._items.remove(self._thinking)
            self._thinking.dismiss()
            self._thinking = None
            self.relayout()

    def hide_all(self):
        for b in list(self._items):
            b.dismiss()
        self._items.clear()
        self._thinking = None

    def _on_closed(self, bubble: ChatBubble):
        if bubble in self._items:
            self._items.remove(bubble)
        if bubble is self._thinking:
            self._thinking = None
        self.relayout()

    def relayout(self):
        from PySide6.QtCore import QPoint, QRect

        from agent.hover_tip import screen_geometry_at

        px, py, pw, ph = self._pet_geo
        screen = screen_geometry_at(QPoint(px + pw // 2, py + ph // 2))
        # 含尚未 show 的气泡：先 move 再显示，避免角落闪现
        targets = list(self._items)
        gap = 8
        # 气泡只占桌宠上方一条竖列，用户/助手错开左右但不共用同一水平带
        cursor_y = py - gap
        for bubble in reversed(targets):
            bw, bh = bubble.width(), bubble.height()
            if bubble.role == "user":
                x = px + pw - bw - 4
            else:
                x = px + 8
            y = cursor_y - bh

            # 上方空间不够：整列改到桌宠左侧或右侧（优先远离输入框）
            if y < screen.top() + 4:
                y = max(screen.top() + 4, py)
                prefer_right = True
                if self._avoid is not None:
                    # 输入框在右侧则气泡改左侧
                    if self._avoid.center().x() > px + pw // 2:
                        prefer_right = False
                if prefer_right:
                    x = px + pw + 12
                    if x + bw > screen.right() - 4:
                        x = px - bw - 12
                else:
                    x = px - bw - 12
                    if x < screen.left() + 4:
                        x = px + pw + 12

            x = max(screen.left() + 4, min(x, screen.right() - bw - 4))
            y = max(screen.top() + 4, min(y, screen.bottom() - bh - 4))

            # 与输入框矩形相交则上移或横移
            if self._avoid is not None and isinstance(self._avoid, QRect):
                br = QRect(x, y, bw, bh)
                if br.intersects(self._avoid):
                    # 先试挪到输入框上方
                    y2 = self._avoid.top() - bh - gap
                    if y2 >= screen.top() + 4:
                        y = y2
                        br = QRect(x, y, bw, bh)
                    if br.intersects(self._avoid):
                        # 再试挪到输入框左侧
                        x2 = self._avoid.left() - bw - gap
                        if x2 >= screen.left() + 4:
                            x = x2
                        else:
                            x = min(self._avoid.right() + gap, screen.right() - bw - 4)

            bubble.move(x, y)
            if bubble.isVisible():
                bubble.raise_()
            cursor_y = y - gap
