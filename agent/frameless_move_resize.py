"""无边框工具面板：顶栏拖动 + 右下角缩放（对齐 SoftDialog / 工作台交互）。"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QSize
from PySide6.QtGui import QCursor, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizeGrip, QToolButton, QWidget

_EDGE = 10  # 边缘缩放感应宽度（px）


def _is_clickable(w: QWidget | None) -> bool:
    while w is not None:
        if isinstance(w, (QPushButton, QToolButton, QSizeGrip)):
            return True
        w = w.parentWidget()
    return False


class MoveResizeController(QObject):
    """挂到 frameless QWidget：标题区拖动 + 右下角/边缘缩放。"""

    def __init__(
        self,
        host: QWidget,
        *,
        width: int,
        height: int,
        min_width: int = 320,
        min_height: int = 280,
    ):
        super().__init__(host)
        self.host = host
        self._drag_set: set[QObject] = set()
        self._drag_off: QPoint | None = None
        self._resizing = False
        self._resize_origin: QPoint | None = None
        self._resize_geom: QRect | None = None
        self._min = QSize(min_width, min_height)

        host.setMinimumSize(min_width, min_height)
        host.setMaximumSize(16777215, 16777215)
        host.resize(width, height)
        host.setMouseTracking(True)

        # 缩放手柄：挂到卡片内容层，避免被铺满的 root 盖住
        self.grip = QSizeGrip(host)
        self.grip.setObjectName("panelSizeGrip")
        self.grip.setFixedSize(18, 18)
        self.grip.setToolTip("拖动缩放窗口")
        self.grip.setStyleSheet(
            """
            QSizeGrip#panelSizeGrip {
                background: transparent;
                width: 18px;
                height: 18px;
            }
            """
        )
        self._card: QWidget | None = None
        host.installEventFilter(self)
        self._mount_grip()
        self._place_grip()

    def _find_card(self) -> QWidget:
        host = self.host
        for name in (
            "notesRoot",
            "root",
            "memRoot",
            "promptRoot",
            "quotesRoot",
            "modelsRoot",
            "wsRoot",
            "historyRoot",
            "extRoot",
            "studioRoot",
            "softCard",
            "panelCard",
        ):
            card = host.findChild(QWidget, name)
            if card is not None:
                return card
        # 无命名卡片时：布局里第一个铺满的子控件
        if host.layout() is not None and host.layout().count():
            item = host.layout().itemAt(0)
            w = item.widget() if item else None
            if w is not None:
                return w
        return host

    def _mount_grip(self) -> None:
        card = self._find_card()
        self._card = card
        self.grip.setParent(card)
        card.setMouseTracking(True)
        if card is not self.host:
            card.installEventFilter(self)
        self.grip.raise_()

    def bind_drag_widgets(self, *widgets: QWidget) -> None:
        for w in widgets:
            if w is None:
                continue
            w.setCursor(Qt.SizeAllCursor)
            if hasattr(w, "setToolTip") and not (w.toolTip() or "").strip():
                w.setToolTip("按住拖动窗口")
            w.setMouseTracking(True)
            w.installEventFilter(self)
            self._drag_set.add(w)
            # 标题文字不抢鼠标，整块顶栏可拖（对齐工作台）
            for child in w.findChildren(QWidget):
                if _is_clickable(child):
                    continue
                if child.objectName() in ("title", "softTitle", "sec"):
                    child.setAttribute(Qt.WA_TransparentForMouseEvents, True)

    def _place_grip(self) -> None:
        g = self.grip
        card = self._card or self.host
        g.move(
            max(0, card.width() - g.width() - 4),
            max(0, card.height() - g.height() - 4),
        )
        g.raise_()
        g.show()

    def _in_se_corner(self, host_pos: QPoint) -> bool:
        h = self.host
        return (
            host_pos.x() >= h.width() - _EDGE - 4
            and host_pos.y() >= h.height() - _EDGE - 4
        )

    def _map_to_host(self, obj: QObject, event: QMouseEvent) -> QPoint:
        if obj is self.host:
            return event.position().toPoint()
        if isinstance(obj, QWidget):
            return obj.mapTo(self.host, event.position().toPoint())
        return event.position().toPoint()

    def _begin_drag(self, global_pos: QPoint) -> None:
        self._resizing = False
        self._drag_off = global_pos - self.host.frameGeometry().topLeft()
        self.host.grabMouse()

    def _begin_resize(self, global_pos: QPoint) -> None:
        self._drag_off = None
        self._resizing = True
        self._resize_origin = global_pos
        self._resize_geom = QRect(self.host.geometry())
        self.host.grabMouse()
        self.host.setCursor(Qt.SizeFDiagCursor)

    def _end_pointer(self) -> None:
        self._drag_off = None
        self._resizing = False
        self._resize_origin = None
        self._resize_geom = None
        try:
            self.host.releaseMouse()
        except RuntimeError:
            pass
        self.host.unsetCursor()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        host = self.host
        et = event.type()

        if obj in (host, self._card) and et == QEvent.Type.Resize:
            self._place_grip()
            return False

        if not isinstance(event, QMouseEvent):
            return False

        # 已在拖动/缩放：事件可能落在 host（grabMouse）
        if self._drag_off is not None or self._resizing:
            if obj is not host and obj not in self._drag_set and obj is not self._card:
                # grabMouse 后仍以 host 为主
                pass
            if et == QEvent.Type.MouseMove:
                gpos = event.globalPosition().toPoint()
                if self._drag_off is not None and event.buttons() & Qt.LeftButton:
                    host.move(gpos - self._drag_off)
                    return True
                if (
                    self._resizing
                    and self._resize_origin is not None
                    and self._resize_geom is not None
                    and event.buttons() & Qt.LeftButton
                ):
                    delta = gpos - self._resize_origin
                    geo = QRect(self._resize_geom)
                    nw = max(self._min.width(), geo.width() + delta.x())
                    nh = max(self._min.height(), geo.height() + delta.y())
                    host.resize(nw, nh)
                    self._place_grip()
                    return True
            if et == QEvent.Type.MouseButtonRelease:
                self._end_pointer()
                return True
            return False

        # 右下角缩放（宿主或卡片）
        if obj in (host, self._card) and et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                local = self._map_to_host(obj, event)
                if self._in_se_corner(local):
                    self._begin_resize(event.globalPosition().toPoint())
                    return True
        if obj in (host, self._card) and et == QEvent.Type.MouseMove:
            local = self._map_to_host(obj, event)
            if self._in_se_corner(local):
                host.setCursor(Qt.SizeFDiagCursor)
            elif host.cursor().shape() == Qt.SizeFDiagCursor:
                host.unsetCursor()
            return False

        # 顶栏拖动
        if obj not in self._drag_set:
            return False

        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            # 点到关闭/按钮不拖
            if isinstance(obj, QWidget):
                child = obj.childAt(event.position().toPoint())
                if _is_clickable(child):
                    return False
            self._begin_drag(event.globalPosition().toPoint())
            return True

        return False


def build_panel_header(title: QLabel, close_btn: QWidget) -> QWidget:
    """标准顶栏：整条可拖，关闭按钮仍可点（对齐命名弹窗 / 工作台）。"""
    header = QWidget()
    header.setObjectName("panelHeader")
    header.setMinimumHeight(36)
    header.setStyleSheet(
        """
        QWidget#panelHeader {
            background: #F3EDE4;
            border-radius: 8px;
        }
        """
    )
    lay = QHBoxLayout(header)
    lay.setContentsMargins(8, 4, 4, 4)
    lay.setSpacing(4)
    if not title.objectName():
        title.setObjectName("title")
    lay.addWidget(title, 1)
    lay.addWidget(close_btn, 0, Qt.AlignTop)
    return header


def attach_move_resize(
    host: QWidget,
    *drag_widgets: QWidget,
    width: int,
    height: int,
    min_width: int = 320,
    min_height: int = 280,
) -> MoveResizeController:
    ctrl = MoveResizeController(
        host,
        width=width,
        height=height,
        min_width=min_width,
        min_height=min_height,
    )
    if drag_widgets:
        ctrl.bind_drag_widgets(*drag_widgets)
    host._move_resize = ctrl  # type: ignore[attr-defined]
    return ctrl
