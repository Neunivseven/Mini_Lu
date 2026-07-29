"""聊天输入条：附件芯片（可取消）+ 识别结果压入请求；输入区动态扩容可滚动。"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRectF, QStandardPaths, QThread, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.file_extract import SUPPORTED_SUFFIXES, is_supported

SKIN = "#DEB49E"
SKIN_DEEP = "#C8957A"
CLOTH = "#8EB4D8"
CLOTH_DEEP = "#6A96C0"
CREAM = "#FFF8F2"
INK = "#2C2420"
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".webm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_ATTACH_SUFFIXES = SUPPORTED_SUFFIXES | AUDIO_SUFFIXES | IMAGE_SUFFIXES | {".doc"}

INPUT_MIN_H = 44
INPUT_MAX_H = 200
PANEL_WIDTH = 560
MAX_ATTACH = 5


def _paste_cache_dir() -> Path:
    root = Path(QStandardPaths.writableLocation(QStandardPaths.TempLocation)) / "mini_lu_paste"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _qimage_from_mime(source: QMimeData) -> QImage | None:
    """从剪贴板 mime 取出位图（截图 / 复制图片）。"""
    if source is None:
        return None
    if source.hasImage():
        raw = source.imageData()
        if isinstance(raw, QImage) and not raw.isNull():
            return raw
        if isinstance(raw, QPixmap) and not raw.isNull():
            return raw.toImage()
        try:
            img = QImage(raw)
            if not img.isNull():
                return img
        except Exception:
            pass
    cb = QApplication.clipboard()
    if cb is None:
        return None
    pix = cb.pixmap()
    if not pix.isNull():
        return pix.toImage()
    img = cb.image()
    if not img.isNull():
        return img
    return None


def save_clipboard_image(source: QMimeData | None = None) -> str | None:
    """将剪贴板图片保存为临时 PNG，返回路径。"""
    mime = source
    if mime is None:
        cb = QApplication.clipboard()
        mime = cb.mimeData() if cb else None
    img = _qimage_from_mime(mime) if mime else None
    if img is None or img.isNull():
        return None
    path = _paste_cache_dir() / f"clipboard_{int(time.time() * 1000)}.png"
    if not img.save(str(path), "PNG"):
        return None
    return str(path)


def _is_attachable_path(path: str) -> bool:
    p = Path(path)
    return p.is_file() and p.suffix.lower() in _ATTACH_SUFFIXES


class ComposerInput(QTextEdit):
    """输入框：粘贴图片/文件时发附件，而不是把路径当文字插入。"""

    paste_paths = Signal(list)  # list[str]

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        if source is None:
            return False
        if source.hasImage():
            return True
        if source.hasUrls() and any(
            u.isLocalFile() and _is_attachable_path(u.toLocalFile()) for u in source.urls()
        ):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if source is None:
            return

        # 1) 本地文件 URL（资源管理器复制、部分应用）→ 附件
        local_files: list[str] = []
        if source.hasUrls():
            for u in source.urls():
                if u.isLocalFile():
                    local_files.append(u.toLocalFile())
        attachable = [p for p in local_files if _is_attachable_path(p)]
        if attachable:
            self.paste_paths.emit(attachable)
            return

        # 2) 位图（截图、网页/微信复制的图）→ 落盘后当图片附件
        if source.hasImage():
            saved = save_clipboard_image(source)
            if saved:
                self.paste_paths.emit([saved])
                return

        # 3) 纯文本若是单个本地附件路径（有的应用只给路径字符串）
        if source.hasText():
            text = (source.text() or "").strip().strip('"').strip("'")
            # 单行路径才拦截，避免误伤普通多行文本
            if text and "\n" not in text and _is_attachable_path(text):
                self.paste_paths.emit([text])
                return

        super().insertFromMimeData(source)


@dataclass
class PendingAttachment:
    id: str
    kind: str  # doc | audio | image
    path: str
    name: str
    status: str = "ready"  # analyzing | ready | error
    analysis: str = ""
    error: str = ""


class _AsrWorker(QThread):
    finished_ok = Signal(str, str)  # attach_id, text
    finished_err = Signal(str, str)  # attach_id, err

    def __init__(self, attach_id: str, audio_path: str, parent=None):
        super().__init__(parent)
        self._id = attach_id
        self._path = audio_path

    def run(self):
        try:
            from agent.providers import get_hub, reset_hub

            reset_hub()
            text = get_hub().asr.transcribe(self._path)
            self.finished_ok.emit(self._id, (text or "").strip())
        except Exception as e:
            self.finished_err.emit(self._id, str(e))


class _VisionWorker(QThread):
    finished_ok = Signal(str, str)
    finished_err = Signal(str, str)

    def __init__(self, attach_id: str, image_path: str, prompt: str = "", parent=None):
        super().__init__(parent)
        self._id = attach_id
        self._path = image_path
        self._prompt = prompt or "请描述这张图片的主要内容。"

    def run(self):
        try:
            from agent.providers import get_hub, reset_hub

            reset_hub()
            text = get_hub().vision.describe(self._path, prompt=self._prompt)
            self.finished_ok.emit(self._id, (text or "").strip())
        except Exception as e:
            self.finished_err.emit(self._id, str(e))


class AnalysisHover(QWidget):
    """悬停查看识别/解析全文（类似 Cursor 附件预览）。"""

    def __init__(self, parent=None):
        flags = (
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowTransparentForInput
        )
        super().__init__(parent, flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.setFixedWidth(320)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 12)
        self._title = QLabel(self)
        self._title.setFont(QFont("Microsoft YaHei UI", 9, QFont.Bold))
        self._title.setStyleSheet(f"color: {INK}; background: transparent;")
        self._title.setWordWrap(True)
        lay.addWidget(self._title)

        self._body = QLabel(self)
        self._body.setFont(QFont("Microsoft YaHei UI", 9))
        self._body.setStyleSheet(f"color: {INK}; background: transparent;")
        self._body.setWordWrap(True)
        self._body.setMaximumHeight(220)
        lay.addWidget(self._body)
        self.hide()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        shadow = QPainterPath()
        shadow.addRoundedRect(QRectF(3, 4, self.width() - 6, self.height() - 6), 12, 12)
        p.fillPath(shadow, QColor(0, 0, 0, 32))
        body = QPainterPath()
        body.addRoundedRect(QRectF(1, 1, self.width() - 4, self.height() - 4), 12, 12)
        p.fillPath(body, QColor(CREAM))
        p.setPen(QPen(QColor(SKIN_DEEP), 1.5))
        p.drawPath(body)

    def show_near(self, global_pos: QPoint, title: str, body: str):
        from agent.hover_tip import prepare_toplevel_show, screen_geometry_at

        prepare_toplevel_show(self)
        self._title.setText(title)
        text = (body or "（尚无解析内容）").strip()
        if len(text) > 900:
            text = text[:897] + "…"
        self._body.setText(text)
        self.adjustSize()
        screen = screen_geometry_at(global_pos)
        x = global_pos.x() + 12
        y = global_pos.y() + 14
        if x + self.width() > screen.right() - 8:
            x = global_pos.x() - self.width() - 10
        if y + self.height() > screen.bottom() - 8:
            y = global_pos.y() - self.height() - 10
        x = max(screen.left() + 6, min(x, screen.right() - self.width() - 6))
        y = max(screen.top() + 6, min(y, screen.bottom() - self.height() - 6))
        self.move(x, y)
        self.show()
        self.raise_()


class AttachmentChip(QWidget):
    """缩略图/文件名芯片，右上角 × 可取消；悬停看解析。"""

    remove_clicked = Signal(str)
    hover_analysis = Signal(str, QPoint)  # id, global pos
    hover_leave = Signal()

    def __init__(self, att: PendingAttachment, parent=None):
        super().__init__(parent)
        self.att_id = att.id
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(48)
        self.setMinimumWidth(120)
        self.setMaximumWidth(200)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 26, 4)  # 右侧给 × 留空
        row.setSpacing(6)

        self.thumb = QLabel(self)
        self.thumb.setFixedSize(36, 36)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(
            f"background: #E8F2FA; border-radius: 8px; border: 1px solid {CLOTH};"
        )
        row.addWidget(self.thumb)

        mid = QVBoxLayout()
        mid.setSpacing(0)
        self.name_lb = QLabel(self)
        self.name_lb.setFont(QFont("Microsoft YaHei UI", 8))
        self.name_lb.setStyleSheet(f"color: {INK}; background: transparent;")
        self.status_lb = QLabel(self)
        self.status_lb.setFont(QFont("Microsoft YaHei UI", 7))
        self.status_lb.setStyleSheet(f"color: {SKIN_DEEP}; background: transparent;")
        mid.addWidget(self.name_lb)
        mid.addWidget(self.status_lb)
        row.addLayout(mid, 1)

        # 叠在右上角，避免被布局挤没
        self.x_btn = QPushButton("×", self)
        self.x_btn.setFixedSize(22, 22)
        self.x_btn.setCursor(Qt.PointingHandCursor)
        self.x_btn.setToolTip("取消附件")
        self.x_btn.setStyleSheet(
            f"""
            QPushButton {{
                background: #F5E6DC; color: {INK};
                border: 1px solid {SKIN_DEEP}; border-radius: 11px;
                font-size: 14px; font-weight: 700; padding: 0;
            }}
            QPushButton:hover {{ background: {SKIN}; color: white; border-color: {SKIN}; }}
            """
        )
        self.x_btn.clicked.connect(lambda: self.remove_clicked.emit(self.att_id))
        self.x_btn.raise_()

        self.setStyleSheet(
            f"""
            AttachmentChip {{
                background: #E8F2FA;
                border: 1px solid {CLOTH};
                border-radius: 10px;
            }}
            """
        )
        self.apply(att)
        # 初始摆放 ×
        self.x_btn.move(max(0, self.width() - 24) if self.width() > 24 else 96, 2)
        self.x_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "x_btn"):
            self.x_btn.move(max(0, self.width() - 24), 2)
            self.x_btn.raise_()

    def apply(self, att: PendingAttachment):
        name = att.name
        if len(name) > 14:
            name = name[:8] + "…" + name[-4:]
        self.name_lb.setText(name)
        if att.status == "analyzing":
            self.status_lb.setText("识别中…")
        elif att.status == "error":
            self.status_lb.setText("失败")
        elif att.kind == "image":
            self.status_lb.setText("待发送识别" if not (att.analysis or "").strip() else "已识别")
        elif att.kind == "audio":
            self.status_lb.setText("语音")
        else:
            self.status_lb.setText("文档")

        if att.kind == "image" and Path(att.path).is_file():
            pm = QPixmap(att.path)
            if not pm.isNull():
                self.thumb.setPixmap(
                    pm.scaled(36, 36, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                )
            else:
                self.thumb.setText("🖼")
        elif att.kind == "audio":
            self.thumb.setText("🎤")
        else:
            self.thumb.setText("📄")

    def enterEvent(self, event):
        self.hover_analysis.emit(self.att_id, self.mapToGlobal(QPoint(self.width() // 2, 0)))
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hover_leave.emit()
        super().leaveEvent(event)


class ChatPanel(QWidget):
    """附件芯片（悬停看解析、可取消）+ 多行输入（动态高度/滚动）。"""

    # text, attachments: list[dict]
    send_requested = Signal(str, list)
    closed = Signal()
    moved_by_user = Signal()
    workspace_requested = Signal()
    new_agent_requested = Signal()
    agents_requested = Signal()
    expand_requested = Signal()  # 打开大窗口（编码工作台）
    stop_requested = Signal()  # 停止本轮 Agent
    rewind_cancel_requested = Signal()  # 取消「从此重开」编辑

    def __init__(self, parent=None, *, embedded: bool = False):
        super().__init__(parent)
        self._embedded = embedded
        if not embedded:
            self.setWindowFlags(
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            )
            self.setAttribute(Qt.WA_TranslucentBackground, True)
            self.setFixedWidth(PANEL_WIDTH)
        else:
            self.setAttribute(Qt.WA_TranslucentBackground, False)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setAcceptDrops(True)

        self._pinned = False
        self._dragging = False
        self._drag_offset = QPoint()
        self._busy = False
        self._attachments: list[PendingAttachment] = []
        self._chips: dict[str, AttachmentChip] = {}
        self._send_after_vision = False
        self._cached_send_text = ""
        self._drop_hover = False
        self._recorder = None
        self._workers: list[QThread] = []
        self._preview: AnalysisHover | None = None

        if not embedded:
            try:
                from agent.ui_icons import app_icon

                self.setWindowIcon(app_icon())
            except Exception:
                pass

        self.setStyleSheet(
            f"""
            QWidget#inputRoot {{
                background: {CREAM};
                border: 2px solid {SKIN_DEEP};
                border-radius: 18px;
            }}
            QWidget#inputRoot[embedded="true"] {{
                border: 1px solid #D0C4B0;
                border-radius: 10px;
            }}
            QWidget#inputRoot[dropHover="true"] {{
                border: 2px dashed {CLOTH_DEEP};
                background: #F0F7FC;
            }}
            QLabel#grip {{
                color: {SKIN_DEEP}; font-size: 14px; padding: 0 2px;
                background: transparent;
            }}
            QTextEdit#input {{
                background: transparent; border: none;
                padding: 4px 6px; color: {INK};
                selection-background-color: {CLOTH};
            }}
            QPushButton#sendBtn, QPushButton#fileBtn, QPushButton#micBtn,
            QPushButton#imgBtn, QPushButton#wsBtn, QPushButton#newAgentBtn,
            QPushButton#agentsBtn, QPushButton#expandBtn {{
                background: {CLOTH}; color: white; border: none;
                border-radius: 14px; padding: 6px 12px; font-weight: 600;
            }}
            QPushButton#sendBtn:hover, QPushButton#fileBtn:hover,
            QPushButton#micBtn:hover, QPushButton#imgBtn:hover,
            QPushButton#wsBtn:hover, QPushButton#newAgentBtn:hover,
            QPushButton#agentsBtn:hover, QPushButton#expandBtn:hover {{
                background: {CLOTH_DEEP};
            }}
            QPushButton#micBtn[recording="true"] {{ background: #D4726A; }}
            QPushButton#sendBtn:disabled, QPushButton#fileBtn:disabled,
            QPushButton#micBtn:disabled, QPushButton#imgBtn:disabled,
            QPushButton#wsBtn:disabled, QPushButton#newAgentBtn:disabled,
            QPushButton#agentsBtn:disabled, QPushButton#expandBtn:disabled {{
                background: #C5C0BA;
            }}
            QPushButton#closeBtn {{
                background: transparent; color: {SKIN_DEEP};
                border: none; font-size: 14px; padding: 0 4px; min-width: 20px;
            }}
            QPushButton#closeBtn:hover {{ color: {INK}; }}
            QLabel#hint {{
                color: {SKIN_DEEP}; font-size: 10px;
                background: transparent; padding: 0 4px 2px 8px;
            }}
            QScrollArea#chipScroll {{
                background: transparent; border: none;
            }}
            """
        )

        self.root = QWidget(self)
        self.root.setObjectName("inputRoot")
        self.root.setProperty("dropHover", False)
        self.root.setProperty("embedded", embedded)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.root)

        col = QVBoxLayout(self.root)
        col.setContentsMargins(8, 6, 8, 8)
        col.setSpacing(4)

        # 附件行（可横向滚动）
        self.chip_scroll = QScrollArea(self)
        self.chip_scroll.setObjectName("chipScroll")
        self.chip_scroll.setWidgetResizable(True)
        self.chip_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.chip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chip_scroll.setFixedHeight(56)
        self.chip_scroll.setVisible(False)
        self.chip_host = QWidget(self.chip_scroll)
        self.attach_row = QHBoxLayout(self.chip_host)
        self.attach_row.setContentsMargins(0, 0, 0, 0)
        self.attach_row.setSpacing(6)
        self.attach_row.addStretch()
        self.chip_scroll.setWidget(self.chip_host)
        col.addWidget(self.chip_scroll)

        hint = QLabel(
            "拖入文件/图片/音频 · 🎤语音 · 📎附件 · Ctrl+Enter 发送"
            if embedded
            else "附件可悬停查看解析、点 × 取消；Ctrl+Enter 发送",
            self,
        )
        hint.setObjectName("hint")
        hint.setFont(QFont("Microsoft YaHei UI", 8))
        hint_row = QHBoxLayout()
        hint_row.setContentsMargins(0, 0, 0, 0)
        hint_row.setSpacing(6)
        hint_row.addWidget(hint, 1)
        self.rewind_cancel_btn = QPushButton("取消重开")
        self.rewind_cancel_btn.setObjectName("ghost")
        self.rewind_cancel_btn.setToolTip("放弃从此重开，保留原对话不变")
        self.rewind_cancel_btn.setVisible(False)
        self.rewind_cancel_btn.clicked.connect(self.rewind_cancel_requested.emit)
        hint_row.addWidget(self.rewind_cancel_btn)
        col.addLayout(hint_row)
        self.hint = hint
        self._rewind_mode = False
        self.refresh_session_hint()

        row = QHBoxLayout()
        row.setSpacing(4)

        self.grip = QLabel("⋮⋮")
        self.grip.setObjectName("grip")
        self.grip.setToolTip("按住拖动对话框")
        self.grip.setCursor(Qt.SizeAllCursor)
        self.grip.setFixedWidth(22)
        self.grip.setAlignment(Qt.AlignCenter)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("closeBtn")
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.clicked.connect(self.hide_panel)

        self.new_agent_btn = QPushButton()
        self.new_agent_btn.setObjectName("newAgentBtn")
        self.new_agent_btn.setFixedWidth(40)
        self.new_agent_btn.setToolTip("新对话（New Agent）— 独立上下文")
        self.new_agent_btn.setCursor(Qt.PointingHandCursor)
        self.new_agent_btn.clicked.connect(self.new_agent_requested.emit)

        self.agents_btn = QPushButton()
        self.agents_btn.setObjectName("agentsBtn")
        self.agents_btn.setFixedWidth(40)
        self.agents_btn.setToolTip("对话列表 / 切换 Agent")
        self.agents_btn.setCursor(Qt.PointingHandCursor)
        self.agents_btn.clicked.connect(self.agents_requested.emit)

        self.input = ComposerInput()
        self.input.setObjectName("input")
        self.input.setPlaceholderText(
            "在工作台继续对话…" if embedded else "说点什么…"
        )
        self.input.setFont(QFont("Microsoft YaHei UI", 10))
        self.input.setAcceptRichText(False)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.input.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        self.input.paste_paths.connect(self._on_paste_paths)
        if embedded:
            self.input.setMinimumHeight(72)
            self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            self.input.setFixedHeight(INPUT_MIN_H)
            self.input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self.input.textChanged.connect(self._adjust_input_height)
        self.input.installEventFilter(self)

        self.mic_btn = QPushButton()
        self.mic_btn.setObjectName("micBtn")
        self.mic_btn.setFixedWidth(36)
        self.mic_btn.setToolTip("录音识别（结果作为附件，不写入输入框）")
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.setProperty("recording", False)
        self.mic_btn.clicked.connect(self._toggle_voice)

        self.img_btn = QPushButton()
        self.img_btn.setObjectName("imgBtn")
        self.img_btn.setFixedWidth(36)
        self.img_btn.setToolTip("添加图片（发送时再识别）")
        self.img_btn.setCursor(Qt.PointingHandCursor)
        self.img_btn.clicked.connect(self._pick_image)

        self.file_btn = QPushButton()
        self.file_btn.setObjectName("fileBtn")
        self.file_btn.setFixedWidth(36)
        self.file_btn.setToolTip("选择文档 / 音频 / 图片")
        self.file_btn.setCursor(Qt.PointingHandCursor)
        self.file_btn.clicked.connect(self._pick_files)

        self.ws_btn = QPushButton()
        self.ws_btn.setObjectName("wsBtn")
        self.ws_btn.setFixedWidth(40)
        self.ws_btn.setToolTip("工作区：打开/切换代码项目文件夹")
        self.ws_btn.setCursor(Qt.PointingHandCursor)
        self.ws_btn.clicked.connect(self.workspace_requested.emit)

        self.expand_btn = QPushButton()
        self.expand_btn.setObjectName("expandBtn")
        self.expand_btn.setFixedWidth(40)
        self.expand_btn.setToolTip("展开大窗口（聊天 + 代码改动对比）")
        self.expand_btn.setCursor(Qt.PointingHandCursor)
        self.expand_btn.clicked.connect(self.expand_requested.emit)

        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setMinimumWidth(64)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setFont(QFont("Microsoft YaHei UI", 10))
        self.send_btn.clicked.connect(self._on_send)

        self._apply_toolbar_icons()

        if embedded:
            # 输入区纵向撑满；操作键固定贴底右侧
            col.addWidget(self.input, stretch=1)
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 2, 0, 0)
            actions.setSpacing(4)
            actions.addStretch(1)
            actions.addWidget(self.mic_btn)
            actions.addWidget(self.img_btn)
            actions.addWidget(self.file_btn)
            actions.addWidget(self.send_btn)
            col.addLayout(actions)
            for w in (
                self.grip,
                self.close_btn,
                self.new_agent_btn,
                self.agents_btn,
                self.ws_btn,
                self.expand_btn,
            ):
                w.hide()
            self.root.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        else:
            row.addWidget(self.grip)
            row.addWidget(self.close_btn)
            row.addWidget(self.new_agent_btn)
            row.addWidget(self.agents_btn)
            row.addWidget(self.input, stretch=1)
            row.addWidget(self.mic_btn)
            row.addWidget(self.img_btn)
            row.addWidget(self.file_btn)
            row.addWidget(self.ws_btn)
            row.addWidget(self.expand_btn)
            row.addWidget(self.send_btn)
            col.addLayout(row)
            self._relayout_height()
            from agent.hover_tip import seal_hidden_toplevel

            seal_hidden_toplevel(self)

        self.grip.installEventFilter(self)
        self.root.installEventFilter(self)

    def is_pinned(self) -> bool:
        return self._pinned

    def clear_pin(self):
        self._pinned = False

    def set_busy(self, busy: bool):
        self._busy = busy
        self.file_btn.setEnabled(not busy)
        self.ws_btn.setEnabled(not busy)
        self.expand_btn.setEnabled(not busy)
        self.new_agent_btn.setEnabled(not busy)
        self.agents_btn.setEnabled(not busy)
        self.mic_btn.setEnabled(not busy)
        self.img_btn.setEnabled(not busy)
        self.input.setEnabled(not busy)
        # 忙碌时发送钮变为「停止」
        try:
            self.send_btn.clicked.disconnect()
        except Exception:
            pass
        if busy:
            self.send_btn.setEnabled(True)
            self.send_btn.setText("停止")
            self.send_btn.setToolTip("停止本轮 Agent 任务")
            self.send_btn.clicked.connect(self._on_stop_clicked)
        else:
            self.send_btn.setEnabled(True)
            try:
                from agent.ui_icons import decorate_button
                from agent.ui_zoom import pt

                decorate_button(self.send_btn, "send", size=max(14, pt(16)), text="发送")
            except Exception:
                self.send_btn.setText("发送")
            self.send_btn.setToolTip("发送（Ctrl+Enter）")
            self.send_btn.clicked.connect(self._on_send)

    def _on_stop_clicked(self):
        self.stop_requested.emit()

    def _adjust_input_height(self):
        if self._embedded:
            return
        doc = self.input.document()
        doc.setTextWidth(max(40, self.input.viewport().width()))
        h = int(doc.size().height()) + 14
        h = max(INPUT_MIN_H, min(INPUT_MAX_H, h))
        if self.input.height() != h:
            self.input.setFixedHeight(h)
            self._relayout_height()

    def _relayout_height(self):
        if self._embedded:
            self.adjustSize()
            return
        self.adjustSize()
        h = self.root.sizeHint().height()
        self.setFixedHeight(max(80, h + 2))
        self.moved_by_user.emit()

    def _ensure_preview(self) -> AnalysisHover:
        if self._preview is None:
            self._preview = AnalysisHover()
        return self._preview

    def _hide_preview(self):
        if self._preview is not None:
            self._preview.hide()

    def _find_att(self, att_id: str) -> PendingAttachment | None:
        for a in self._attachments:
            if a.id == att_id:
                return a
        return None

    def _refresh_chips(self):
        while self.attach_row.count():
            item = self.attach_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._chips.clear()
        if not self._attachments:
            self.chip_scroll.setVisible(False)
            self._relayout_height()
            return
        self.chip_scroll.setVisible(True)
        for att in self._attachments:
            chip = AttachmentChip(att, parent=self.chip_host)
            chip.remove_clicked.connect(self.remove_attachment)
            chip.hover_analysis.connect(self._on_chip_hover)
            chip.hover_leave.connect(self._hide_preview)
            self._chips[att.id] = chip
            self.attach_row.addWidget(chip)
        self.attach_row.addStretch()
        self._relayout_height()

    def _update_chip(self, att: PendingAttachment):
        chip = self._chips.get(att.id)
        if chip:
            chip.apply(att)

    def remove_attachment(self, att_id: str):
        self._attachments = [a for a in self._attachments if a.id != att_id]
        self._hide_preview()
        self._refresh_chips()
        self.hint.setText("已取消该附件")
        # 若正在「发送前识别」，取消后尝试继续/结束
        if self._send_after_vision:
            self._try_finish_send_after_vision()

    def clear_attachments(self):
        self._attachments.clear()
        self._hide_preview()
        self._refresh_chips()

    def _on_chip_hover(self, att_id: str, global_pos: QPoint):
        att = self._find_att(att_id)
        if not att:
            return
        if att.status == "analyzing":
            body = "正在识别图片，完成后会自动发送给模型…"
        elif att.status == "error":
            body = att.error or "识别失败"
        elif att.kind == "image" and not (att.analysis or "").strip():
            body = "发送时再识别；点右上角 × 可取消。"
        else:
            body = att.analysis or ("文档将在发送时提取正文" if att.kind == "doc" else "（无解析）")
        if att.kind == "image":
            title = f"🖼 {att.name}"
        elif att.kind == "audio":
            title = f"🎤 {att.name}"
        else:
            title = f"📄 {att.name}"
        self._ensure_preview().show_near(global_pos, title, body)

    def _add_attachment(self, kind: str, path: str, *, analyze: bool = False) -> str | None:
        if len(self._attachments) >= MAX_ATTACH:
            self.hint.setText(f"最多 {MAX_ATTACH} 个附件")
            return None
        p = Path(path)
        # 图片默认不立即识别：发送时再跑 vision
        if kind == "image":
            analyze = False
        att = PendingAttachment(
            id=uuid.uuid4().hex[:10],
            kind=kind,
            path=str(p.resolve()) if p.is_file() else str(p),
            name=p.name,
            status="analyzing" if analyze else "ready",
        )
        self._attachments.append(att)
        self._refresh_chips()
        if analyze:
            if kind == "audio":
                self._start_asr(att)
            elif kind == "image":
                self._start_vision(att)
        if kind == "image":
            self.hint.setText("已添加图片 · 点 × 可取消 · 发送时再识别")
        return att.id

    def attach_files(self, paths: list[str]) -> list[str]:
        rejected: list[str] = []
        for raw in paths:
            p = Path(raw)
            if not p.is_file():
                rejected.append(f"{p.name}: 不是文件")
                continue
            suf = p.suffix.lower()
            if suf in AUDIO_SUFFIXES:
                if not self._add_attachment("audio", str(p), analyze=True):
                    rejected.append(f"{p.name}: 附件已满")
                continue
            if suf in IMAGE_SUFFIXES:
                if not self._add_attachment("image", str(p), analyze=False):
                    rejected.append(f"{p.name}: 附件已满")
                continue
            if suf == ".doc":
                rejected.append(f"{p.name}: 请改用 .docx 或 PDF")
                continue
            if not is_supported(p):
                rejected.append(f"{p.name}: 不支持")
                continue
            if not self._add_attachment("doc", str(p), analyze=False):
                rejected.append(f"{p.name}: 附件已满")
        if self._attachments:
            self.hint.setText("悬停附件可预览；点 × 取消；图片在发送时识别")
        return rejected

    def _apply_toolbar_icons(self) -> None:
        try:
            from agent.ui_icons import decorate_button
            from agent.ui_zoom import pt

            sz = max(14, pt(16))
            decorate_button(self.new_agent_btn, "plus", size=sz, text="")
            decorate_button(self.agents_btn, "chat", size=sz, text="")
            decorate_button(self.mic_btn, "mic", size=sz, text="")
            decorate_button(self.img_btn, "image", size=sz, text="")
            decorate_button(self.file_btn, "attach", size=sz, text="")
            decorate_button(self.ws_btn, "folder", size=sz, text="")
            if hasattr(self, "expand_btn") and self.expand_btn is not None:
                decorate_button(self.expand_btn, "expand", size=sz, text="")
            decorate_button(self.send_btn, "send", size=sz, text="发送")
        except Exception:
            # 回退文字，避免无按钮
            if not self.new_agent_btn.text():
                self.new_agent_btn.setText("＋")
            if not self.agents_btn.text():
                self.agents_btn.setText("对话")
            if not self.mic_btn.text() and self.mic_btn.icon().isNull():
                self.mic_btn.setText("麦")
            if not self.img_btn.text() and self.img_btn.icon().isNull():
                self.img_btn.setText("图")
            if not self.file_btn.text() and self.file_btn.icon().isNull():
                self.file_btn.setText("件")
            if not self.ws_btn.text() and self.ws_btn.icon().isNull():
                self.ws_btn.setText("夹")
            if hasattr(self, "expand_btn") and self.expand_btn and not self.expand_btn.text():
                self.expand_btn.setText("开")

    def _set_mic_recording(self, on: bool):
        self.mic_btn.setProperty("recording", on)
        self.mic_btn.style().unpolish(self.mic_btn)
        self.mic_btn.style().polish(self.mic_btn)
        try:
            from agent.ui_icons import decorate_button
            from agent.ui_zoom import pt

            sz = max(14, pt(16))
            decorate_button(
                self.mic_btn, "mic_stop" if on else "mic", size=sz, text=""
            )
        except Exception:
            self.mic_btn.setText("停" if on else "麦")

    def _toggle_voice(self):
        if self._busy:
            return
        if self._recorder is None:
            from agent.voice_recorder import WindowsMciRecorder

            self._recorder = WindowsMciRecorder()
        if not self._recorder.is_recording:
            try:
                self._recorder.start()
            except Exception as e:
                self.hint.setText(f"无法录音: {e}"[:80])
                return
            self._set_mic_recording(True)
            self.hint.setText("录音中…再点结束")
            return
        try:
            path = self._recorder.stop()
        except Exception as e:
            self._set_mic_recording(False)
            self.hint.setText(f"停止录音失败: {e}"[:80])
            return
        self._set_mic_recording(False)
        self._add_attachment("audio", str(path), analyze=True)

    def _on_paste_paths(self, paths: list):
        """Ctrl+V / 右键粘贴：图片与文件进附件区。"""
        if self._busy:
            return
        bad = self.attach_files([str(p) for p in (paths or []) if p])
        if bad:
            self.hint.setText(" / ".join(bad)[:80])
        elif self._attachments:
            self.hint.setText("已从剪贴板添加附件 · 悬停可看解析 · 点 × 取消")

    def _pick_image(self):
        if self._busy:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif *.bmp)"
        )
        if path:
            self._add_attachment("image", path, analyze=False)

    def _pick_files(self):
        if self._busy:
            return
        filt = (
            "全部 (*.pdf *.docx *.xlsx *.txt *.md *.csv "
            "*.wav *.mp3 *.m4a *.png *.jpg *.jpeg *.webp);;"
            "所有 (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, "选择附件", "", filt)
        if files:
            bad = self.attach_files(files)
            if bad:
                self.hint.setText(" / ".join(bad)[:80])

    def _start_asr(self, att: PendingAttachment):
        w = _AsrWorker(att.id, att.path, self)
        self._workers.append(w)
        w.finished_ok.connect(self._on_media_ok)
        w.finished_err.connect(self._on_media_err)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        w.start()
        self.hint.setText("语音识别中…")

    def _start_vision(self, att: PendingAttachment):
        # 发送前识别：优先用已缓存的用户问题；否则用输入框
        prompt = (
            (self._cached_send_text or "").strip()
            or self.input.toPlainText().strip()
            or "请描述这张图片的主要内容。"
        )
        att.status = "analyzing"
        self._update_chip(att)
        w = _VisionWorker(att.id, att.path, prompt=prompt, parent=self)
        self._workers.append(w)
        w.finished_ok.connect(self._on_media_ok)
        w.finished_err.connect(self._on_media_err)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        w.start()
        self.hint.setText("发送前识别图片中…")

    def _on_media_ok(self, att_id: str, text: str):
        att = self._find_att(att_id)
        if not att:
            # 附件已取消，仍检查是否该继续发送
            self._try_finish_send_after_vision()
            return
        att.status = "ready"
        att.analysis = text or ""
        self._update_chip(att)
        if self._send_after_vision:
            self._try_finish_send_after_vision()
        else:
            self.hint.setText("识别完成：悬停查看，点 × 可取消")

    def _on_media_err(self, att_id: str, err: str):
        att = self._find_att(att_id)
        if not att:
            self._try_finish_send_after_vision()
            return
        att.status = "error"
        att.error = err
        # 仍带一段说明进模型，避免「图丢了但用户以为发了」
        att.analysis = f"（图片识别失败：{err}）"
        self._update_chip(att)
        if self._send_after_vision:
            self._try_finish_send_after_vision()
        else:
            self.hint.setText(f"识别失败（可点 × 移除）: {err}"[:90])

    def _payload_attachments(self) -> list[dict]:
        out = []
        for a in self._attachments:
            if a.status == "analyzing":
                continue
            out.append(
                {
                    "kind": a.kind,
                    "path": a.path,
                    "name": a.name,
                    "analysis": a.analysis,
                }
            )
        return out

    def _chat_handles_images_natively(self) -> bool:
        try:
            from agent.providers.hub import get_hub

            return bool(get_hub().chat_supports("image"))
        except Exception:
            return False

    def _images_needing_vision(self) -> list[PendingAttachment]:
        # Chat 本身支持看图时，跳过独立 vision 预处理
        if self._chat_handles_images_natively():
            return []
        return [
            a
            for a in self._attachments
            if a.kind == "image" and not (a.analysis or "").strip() and a.status != "analyzing"
        ]

    def _try_finish_send_after_vision(self):
        if not self._send_after_vision:
            return
        if any(a.status == "analyzing" for a in self._attachments):
            return
        text = self._cached_send_text
        self._send_after_vision = False
        self._cached_send_text = ""
        self.input.setEnabled(True)
        self.send_btn.setEnabled(True)
        self._emit_send(text)

    def _emit_send(self, text: str):
        attachments = self._payload_attachments()
        if not (text or "").strip() and not attachments:
            self.hint.setText("没有可发送的内容")
            return
        self.input.clear()
        self.clear_attachments()
        self.refresh_session_hint()
        self._adjust_input_height()
        self.send_requested.emit(text or "", attachments)

    def _on_send(self):
        if self._busy:
            return
        if self._send_after_vision:
            self.hint.setText("正在识别图片，请稍候…")
            return
        if self._recorder and self._recorder.is_recording:
            self.hint.setText("请先结束录音")
            return
        if any(a.status == "analyzing" for a in self._attachments):
            self.hint.setText("还有附件识别中，请稍候或先取消")
            return
        text = self.input.toPlainText().strip()
        if not text and not self._attachments:
            return

        need = self._images_needing_vision()
        if need:
            # 发送后先识图，再交给语言模型
            self._send_after_vision = True
            self._cached_send_text = text
            self.input.setEnabled(False)
            self.send_btn.setEnabled(False)
            for att in need:
                self._start_vision(att)
            self.hint.setText(f"发送前识别图片（{len(need)}）…")
            return

        self._emit_send(text)

    def refresh_session_hint(self):
        """显示当前对话标题（嵌入模式只保留附件操作提示）。"""
        if getattr(self, "_embedded", False):
            if hasattr(self, "hint") and self.hint and not self._attachments:
                self.hint.setText(
                    "拖入或 Ctrl+V 粘贴图片 · 点 × 取消 · "
                    + (
                        "当前 Chat 可直接看图"
                        if self._chat_handles_images_natively()
                        else "发送时再识别"
                    )
                    + " · Ctrl+Enter 发送"
                )
            return
        try:
            from agent.chat_history import get_active_session

            s = get_active_session()
            title = (s.get("title") or "对话").strip()
            self.hint.setText(f"当前：{title}  ·  ＋新对话  ·  ⛶大窗口  ·  Ctrl+Enter")
        except Exception:
            self.hint.setText("＋新对话 · ⛶大窗口 · Ctrl+Enter 发送")

    def set_rewind_mode(self, on: bool, preview: str = "") -> None:
        """从此重开：仅编辑输入框；发送才截断，取消则原对话不变。"""
        self._rewind_mode = bool(on)
        self.rewind_cancel_btn.setVisible(self._rewind_mode)
        if self._rewind_mode:
            tip = (preview or "").strip().replace("\n", " ")
            if len(tip) > 36:
                tip = tip[:35] + "…"
            self.hint.setText(
                f"从此重开编辑中{('：' + tip) if tip else ''} · 发送后才截断重跑 · 可点「取消重开」"
            )
        else:
            self.refresh_session_hint()

    def is_rewind_mode(self) -> bool:
        return bool(getattr(self, "_rewind_mode", False))

    def get_draft_text(self) -> str:
        return self.input.toPlainText()

    def set_draft_text(self, text: str) -> None:
        from PySide6.QtGui import QTextCursor

        self.input.setPlainText(text or "")
        cur = self.input.textCursor()
        cur.movePosition(QTextCursor.End)
        self.input.setTextCursor(cur)

    def apply_font_zoom(self) -> None:
        """Ctrl+滚轮缩放后刷新输入框字号。"""
        try:
            from agent.ui_zoom import pt
            from agent.ui_fonts import ui_font

            self.input.setFont(ui_font(pt(10)))
        except Exception:
            pass
        try:
            self._apply_toolbar_icons()
        except Exception:
            pass
        self._adjust_input_height()

    def hide_panel(self):
        if self._recorder and self._recorder.is_recording:
            try:
                self._recorder.cancel()
            except Exception:
                pass
            self._set_mic_recording(False)
        self._hide_preview()
        self.hide()
        self.closed.emit()

    def _set_drop_hover(self, on: bool):
        self._drop_hover = on
        self.root.setProperty("dropHover", on)
        self.root.style().unpolish(self.root)
        self.root.style().polish(self.root)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            urls = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            ok = any(
                Path(u).suffix.lower()
                in (SUPPORTED_SUFFIXES | AUDIO_SUFFIXES | IMAGE_SUFFIXES | {".doc"})
                for u in urls
            )
            if ok:
                event.acceptProposedAction()
                self._set_drop_hover(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent):
        self._set_drop_hover(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        bad = self.attach_files(paths)
        if bad:
            self.hint.setText(" / ".join(bad)[:80])
        event.acceptProposedAction()

    def _begin_drag(self, global_pos: QPoint):
        self._dragging = True
        self._drag_offset = global_pos - self.frameGeometry().topLeft()

    def _end_drag(self):
        if not self._dragging:
            return
        self._dragging = False
        self._pinned = True
        self.moved_by_user.emit()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (
                event.modifiers() & Qt.ControlModifier
            ):
                self._on_send()
                return True
        blocked = (
            self.input,
            self.send_btn,
            self.close_btn,
            self.new_agent_btn,
            self.agents_btn,
            self.file_btn,
            self.ws_btn,
            self.expand_btn,
            self.mic_btn,
            self.img_btn,
            self.chip_scroll,
        )
        if obj is self.grip:
            et = event.type()
            if et == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                self._begin_drag(event.globalPosition().toPoint())
                return True
            if et == event.Type.MouseMove and self._dragging:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._end_drag()
                return True
        if obj is self.root:
            et = event.type()
            if et == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
                child = self.root.childAt(event.position().toPoint())
                if child in blocked:
                    return False
                p = child
                while p is not None and p is not self.root:
                    if p in blocked or isinstance(p, AttachmentChip):
                        return False
                    p = p.parentWidget()
                self._begin_drag(event.globalPosition().toPoint())
                return True
            if et == event.Type.MouseMove and self._dragging:
                self.move(event.globalPosition().toPoint() - self._drag_offset)
                return True
            if et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
                self._end_drag()
                return True
        return super().eventFilter(obj, event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragging and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self._dragging:
            self._end_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def place_near(self, global_x: int, global_y: int, pet_w: int = 200, pet_h: int = 260):
        from agent.hover_tip import screen_geometry_at
        from PySide6.QtCore import QPoint

        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        x = global_x + pet_w // 2 - self.width() // 2
        y = global_y + pet_h + 6
        if y + self.height() > screen.bottom() - 8:
            x = global_x + pet_w + 10
            y = global_y + pet_h - self.height() - 4
            if x + self.width() > screen.right() - 8:
                x = global_x - self.width() - 10
            if y < screen.top() + 8:
                y = screen.top() + 8
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)

    def show_near(self, global_x: int, global_y: int, pet_w: int = 200, pet_h: int = 260):
        from agent.hover_tip import prepare_toplevel_show

        prepare_toplevel_show(self)
        self.clear_pin()
        self.place_near(global_x, global_y, pet_w, pet_h)
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self._adjust_input_height()
