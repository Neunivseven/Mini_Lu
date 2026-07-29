"""Mini_Lu 应用图标与工具栏矢量图标（QPainter，无外部依赖）。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# 与工作台 slate 蓝灰一致
ACCENT = QColor("#3D7EA6")
INK = QColor("#1E293B")
MUTED = QColor("#64748B")
DANGER = QColor("#C45C5C")
OK = QColor("#2F8F6B")
PANEL = QColor("#F8FBFE")


def _assets_icons_dir() -> Path:
    # 开发：仓库 assets/icons；打包：exe 旁 assets/icons
    import sys

    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "assets" / "icons"


@lru_cache(maxsize=1)
def app_icon() -> QIcon:
    """窗口 / 任务栏图标：优先高清 PNG，避免只用糊掉的 16px ICO。"""
    d = _assets_icons_dir()
    png = d / "app_icon.png"
    png512 = d / "app_icon_512.png"
    png256 = d / "app_icon_256.png"
    ico = d / "app_icon.ico"

    ic = QIcon()
    # 从大到小挂入，HiDPI / 任务栏会选合适档
    for path in (png, png512, png256):
        if path.is_file():
            pm = QPixmap(str(path))
            if not pm.isNull():
                ic.addPixmap(pm)
                # 预生成常用尺寸，强制平滑缩放
                for s in (16, 20, 24, 32, 40, 48, 64, 128, 256):
                    if pm.width() >= s:
                        ic.addPixmap(
                            pm.scaled(
                                s,
                                s,
                                Qt.AspectRatioMode.KeepAspectRatio,
                                Qt.TransformationMode.SmoothTransformation,
                            )
                        )
                break
    if ico.is_file():
        # exe / 部分 shell 仍读 ICO；作为补充，不要盖住高清 PNG
        ic.addFile(str(ico))
    if not ic.isNull():
        return ic
    return icon("app", 64)


def clear_app_icon_cache() -> None:
    app_icon.cache_clear()


def app_pixmap(size: int = 32) -> QPixmap:
    ic = app_icon()
    return ic.pixmap(size, size)


def icon(name: str, size: int = 18, color: QColor | None = None) -> QIcon:
    """按名称生成工具栏图标。"""
    pm = pixmap(name, size, color)
    return QIcon(pm)


def pixmap(name: str, size: int = 18, color: QColor | None = None) -> QPixmap:
    c = QColor(color) if color is not None else ACCENT
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.SmoothPixmapTransform, True)
    drawer = _DRAWERS.get(name, _draw_dot)
    drawer(p, float(size), c)
    p.end()
    return pm


def _pen(color: QColor, width: float, size: float) -> QPen:
    pen = QPen(color)
    pen.setWidthF(max(1.2, size * width))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw_app(p: QPainter, s: float, c: QColor) -> None:
    # 圆角窗 + 左侧色块（呼应用户示意的窗口图标）
    m = s * 0.12
    r = QRectF(m, m, s - 2 * m, s - 2 * m)
    p.setPen(_pen(INK, 0.06, s))
    p.setBrush(PANEL)
    p.drawRoundedRect(r, s * 0.12, s * 0.12)
    left = QRectF(m + s * 0.06, m + s * 0.22, s * 0.28, s * 0.52)
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(left, s * 0.04, s * 0.04)
    p.setPen(_pen(MUTED, 0.05, s))
    y0 = m + s * 0.28
    for i in range(3):
        y = y0 + i * s * 0.14
        p.drawLine(QPointF(m + s * 0.42, y), QPointF(s - m - s * 0.14, y))


def _draw_send(p: QPainter, s: float, c: QColor) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.18, s * 0.22)
    path.lineTo(s * 0.82, s * 0.50)
    path.lineTo(s * 0.18, s * 0.78)
    path.lineTo(s * 0.18, s * 0.58)
    path.lineTo(s * 0.52, s * 0.50)
    path.lineTo(s * 0.18, s * 0.42)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawPath(path)


def _draw_mic(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.08, s))
    p.setBrush(Qt.NoBrush)
    body = QRectF(s * 0.36, s * 0.14, s * 0.28, s * 0.42)
    p.drawRoundedRect(body, s * 0.14, s * 0.14)
    p.drawArc(QRectF(s * 0.26, s * 0.34, s * 0.48, s * 0.40), 0, -180 * 16)
    p.drawLine(QPointF(s * 0.50, s * 0.74), QPointF(s * 0.50, s * 0.86))
    p.drawLine(QPointF(s * 0.34, s * 0.86), QPointF(s * 0.66, s * 0.86))


def _draw_mic_stop(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(DANGER)
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.44), s * 0.06, s * 0.06)


def _draw_image(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.07, s))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.20, s * 0.72, s * 0.58), s * 0.06, s * 0.06)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(QRectF(s * 0.26, s * 0.30, s * 0.16, s * 0.16))
    path = QPainterPath()
    path.moveTo(s * 0.22, s * 0.70)
    path.lineTo(s * 0.42, s * 0.48)
    path.lineTo(s * 0.56, s * 0.60)
    path.lineTo(s * 0.70, s * 0.44)
    path.lineTo(s * 0.86, s * 0.70)
    path.closeSubpath()
    p.setBrush(QColor(c.red(), c.green(), c.blue(), 160))
    p.drawPath(path)


def _draw_attach(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.09, s))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.62, s * 0.28)
    path.lineTo(s * 0.38, s * 0.52)
    path.cubicTo(s * 0.28, s * 0.62, s * 0.28, s * 0.78, s * 0.42, s * 0.78)
    path.cubicTo(s * 0.56, s * 0.78, s * 0.56, s * 0.62, s * 0.46, s * 0.52)
    path.lineTo(s * 0.64, s * 0.34)
    p.drawPath(path)


def _draw_folder(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.34, s * 0.72, s * 0.46), s * 0.06, s * 0.06)
    p.setBrush(QColor(c.red(), c.green(), c.blue(), 200))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.22, s * 0.34, s * 0.18), s * 0.04, s * 0.04)


def _draw_expand(p: QPainter, s: float, c: QColor) -> None:
    pen = _pen(c, 0.08, s)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    m = s * 0.22
    # 四角向外
    for ax, ay, dx, dy in (
        (m, m, s * 0.18, 0),
        (m, m, 0, s * 0.18),
        (s - m, m, -s * 0.18, 0),
        (s - m, m, 0, s * 0.18),
        (m, s - m, s * 0.18, 0),
        (m, s - m, 0, -s * 0.18),
        (s - m, s - m, -s * 0.18, 0),
        (s - m, s - m, 0, -s * 0.18),
    ):
        p.drawLine(QPointF(ax, ay), QPointF(ax + dx, ay + dy))


def _draw_chat(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.16, s * 0.68, s * 0.48), s * 0.10, s * 0.10)
    tail = QPainterPath()
    tail.moveTo(s * 0.28, s * 0.60)
    tail.lineTo(s * 0.28, s * 0.82)
    tail.lineTo(s * 0.48, s * 0.60)
    tail.closeSubpath()
    p.drawPath(tail)


def _draw_plus(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.10, s))
    p.drawLine(QPointF(s * 0.50, s * 0.22), QPointF(s * 0.50, s * 0.78))
    p.drawLine(QPointF(s * 0.22, s * 0.50), QPointF(s * 0.78, s * 0.50))


def _draw_refresh(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.09, s))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 40 * 16, 260 * 16)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    arrow = QPainterPath()
    arrow.moveTo(s * 0.68, s * 0.18)
    arrow.lineTo(s * 0.86, s * 0.30)
    arrow.lineTo(s * 0.64, s * 0.38)
    arrow.closeSubpath()
    p.drawPath(arrow)


def _draw_diff(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(DANGER)
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.22, s * 0.28, s * 0.56), s * 0.04, s * 0.04)
    p.setBrush(OK)
    p.drawRoundedRect(QRectF(s * 0.54, s * 0.22, s * 0.28, s * 0.56), s * 0.04, s * 0.04)


def _draw_files(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(c, 0.07, s))
    p.setBrush(PANEL)
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.14, s * 0.50, s * 0.66), s * 0.04, s * 0.04)
    p.setPen(_pen(c, 0.06, s))
    for i in range(3):
        y = s * (0.32 + i * 0.14)
        p.drawLine(QPointF(s * 0.32, y), QPointF(s * 0.62, y))


def _draw_keep(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(_pen(OK if color_is_accent(c) else c, 0.10, s))
    p.setBrush(Qt.NoBrush)
    path = QPainterPath()
    path.moveTo(s * 0.22, s * 0.52)
    path.lineTo(s * 0.42, s * 0.72)
    path.lineTo(s * 0.78, s * 0.28)
    p.drawPath(path)


def color_is_accent(c: QColor) -> bool:
    return c == ACCENT


def _draw_discard(p: QPainter, s: float, c: QColor) -> None:
    col = DANGER if color_is_accent(c) else c
    p.setPen(_pen(col, 0.10, s))
    p.drawLine(QPointF(s * 0.28, s * 0.28), QPointF(s * 0.72, s * 0.72))
    p.drawLine(QPointF(s * 0.72, s * 0.28), QPointF(s * 0.28, s * 0.72))


def _draw_dot(p: QPainter, s: float, c: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawEllipse(QRectF(s * 0.30, s * 0.30, s * 0.40, s * 0.40))


_DRAWERS = {
    "app": _draw_app,
    "send": _draw_send,
    "mic": _draw_mic,
    "mic_stop": _draw_mic_stop,
    "image": _draw_image,
    "attach": _draw_attach,
    "folder": _draw_folder,
    "expand": _draw_expand,
    "chat": _draw_chat,
    "plus": _draw_plus,
    "refresh": _draw_refresh,
    "diff": _draw_diff,
    "files": _draw_files,
    "keep": _draw_keep,
    "discard": _draw_discard,
}


def decorate_button(btn, name: str, *, size: int = 16, text: str | None = None) -> None:
    """给按钮挂图标；text=None 保留原文字，text='' 仅图标。"""
    from PySide6.QtCore import QSize

    btn.setIcon(icon(name, size))
    btn.setIconSize(QSize(size, size))
    if text is not None:
        btn.setText(text)
