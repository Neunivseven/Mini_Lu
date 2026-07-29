"""
对话消息视图：预览可展开；正文与代码块分框展示（类 Markdown）。
"""
from __future__ import annotations

import re
from typing import Literal

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent.md_render import markdown_to_html, normalize_markdown, strip_md

INK = "#2C2420"
MUTED = "#7A6F66"
CREAM = "#FFF8F0"
CODE_BG = "#1E2430"
CODE_FG = "#E8ECF4"
TEXT_BG = "#FFFFFF"
USER_BG = "#E8F0FE"
ASSIST_BG = "#FFFFFF"
BORDER = "#D0C4B0"

# 字号基准；显示值由 refresh_font_sizes / 首次 import 写入 FS_*
_BASE_HEAD = 11
_BASE_TIME = 9
_BASE_BODY = 11
_BASE_META = 10
_BASE_CODE = 11
_BASE_BTN = 10
_BASE_HINT = 9

FS_HEAD = _BASE_HEAD
FS_TIME = _BASE_TIME
FS_BODY = _BASE_BODY
FS_META = _BASE_META
FS_CODE = _BASE_CODE
FS_BTN = _BASE_BTN
FS_HINT = _BASE_HINT


def refresh_font_sizes() -> None:
    """缩放变化后刷新模块字号常量。"""
    global FS_HEAD, FS_TIME, FS_BODY, FS_META, FS_CODE, FS_BTN, FS_HINT
    try:
        from agent.ui_zoom import pt
    except Exception:
        return
    FS_HEAD = pt(_BASE_HEAD)
    FS_TIME = pt(_BASE_TIME)
    FS_BODY = pt(_BASE_BODY)
    FS_META = pt(_BASE_META)
    FS_CODE = pt(_BASE_CODE)
    FS_BTN = pt(_BASE_BTN)
    FS_HINT = pt(_BASE_HINT)


try:
    refresh_font_sizes()
except Exception:
    pass

_FENCE_RE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```", re.MULTILINE)


SegmentKind = Literal["text", "code"]


def split_markdown_segments(text: str) -> list[tuple[SegmentKind, str, str]]:
    """返回 [(kind, body, lang)]；code 的 lang 可能为空。"""
    text = text or ""
    parts: list[tuple[SegmentKind, str, str]] = []
    pos = 0
    for m in _FENCE_RE.finditer(text):
        if m.start() > pos:
            chunk = text[pos : m.start()].strip("\n")
            if chunk.strip():
                parts.append(("text", chunk, ""))
        lang = (m.group(1) or "").strip()
        code = m.group(2).rstrip("\n")
        parts.append(("code", code, lang))
        pos = m.end()
    tail = text[pos:].strip("\n")
    if tail.strip():
        parts.append(("text", tail, ""))
    if not parts and text.strip():
        parts.append(("text", text.strip(), ""))
    return parts


def preview_text(text: str, max_chars: int = 160, max_lines: int = 3) -> str:
    raw = (text or "").strip()
    if not raw:
        return "（空）"
    lines = raw.splitlines()
    clipped = "\n".join(lines[:max_lines])
    if len(lines) > max_lines:
        clipped += "…"
    if len(clipped) > max_chars:
        clipped = clipped[: max_chars - 1] + "…"
    return clipped


def needs_collapse(text: str, max_chars: int = 160, max_lines: int = 3) -> bool:
    """内容是否需要折叠（超长或含代码块）。"""
    raw = (text or "").strip()
    if not raw:
        return False
    segs = split_markdown_segments(raw)
    if any(k == "code" for k, _, __ in segs):
        return True
    lines = raw.splitlines()
    if len(lines) > max_lines:
        return True
    if len(raw) > max_chars:
        return True
    return False


class _CodeBlock(QFrame):
    apply_requested = Signal(str, str)  # code, lang
    send_requested = Signal(str)  # code

    def __init__(self, code: str, lang: str = "", parent=None):
        super().__init__(parent)
        self._lang = str(lang or "")
        self.setObjectName("codeBlock")
        self.setStyleSheet(
            f"""
            QFrame#codeBlock {{
                background: {CODE_BG};
                border: 1px solid #3A4558;
                border-radius: 8px;
            }}
            QLabel#codeLang {{
                color: #9AA7BD;
                font-weight: 600;
                padding: 4px 8px 0 8px;
                font-size: {FS_HINT}px;
            }}
            QPushButton#codeBtn {{
                background: #2A3446;
                color: #D8E1EF;
                border: 1px solid #3A4558;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: {FS_HINT}px;
                font-weight: 600;
            }}
            QPushButton#codeBtn:hover {{
                background: #3D7EA6;
                border-color: #3D7EA6;
                color: #fff;
            }}
            QTextEdit#codeBody {{
                background: {CODE_BG};
                color: {CODE_FG};
                border: none;
                padding: 6px 10px 10px 10px;
                font-family: "Cascadia Code", "JetBrains Mono", Consolas, monospace;
                font-size: {FS_CODE}px;
                font-weight: 400;
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(6, 4, 6, 0)
        top.setSpacing(6)
        self.lang_lab = QLabel(lang or "code", self)
        self.lang_lab.setObjectName("codeLang")
        self.lang_lab.setFont(QFont("Segoe UI", FS_HINT, QFont.DemiBold))
        top.addWidget(self.lang_lab)
        top.addStretch(1)
        self.edit_btn = QPushButton("编辑", self)
        self.edit_btn.setObjectName("codeBtn")
        self.copy_btn = QPushButton("复制", self)
        self.copy_btn.setObjectName("codeBtn")
        self.send_btn = QPushButton("发到输入框", self)
        self.send_btn.setObjectName("codeBtn")
        self.apply_btn = QPushButton("应用到文件", self)
        self.apply_btn.setObjectName("codeBtn")
        for b in (self.edit_btn, self.copy_btn, self.send_btn, self.apply_btn):
            b.setCursor(Qt.PointingHandCursor)
            top.addWidget(b)
        lay.addLayout(top)

        self.body = QTextEdit(self)
        body = self.body
        body.setObjectName("codeBody")
        body.setReadOnly(True)
        # 目录树等宽对齐依赖不换行；过长可横向滚动
        body.setLineWrapMode(QTextEdit.NoWrap)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        body.setPlainText(code)
        body.setFont(QFont("Cascadia Code", FS_CODE))
        lines = max(1, code.count("\n") + 1)
        # 目录树等可能很长，适当放宽高度上限
        h = min(560, max(52, lines * 17 + 22))
        body.setFixedHeight(h)
        lay.addWidget(body)

        self._editing = False
        self.edit_btn.clicked.connect(self._toggle_edit)
        self.copy_btn.clicked.connect(self._copy_code)
        self.send_btn.clicked.connect(self._emit_send)
        self.apply_btn.clicked.connect(self._emit_apply)

    def _toggle_edit(self) -> None:
        self._editing = not self._editing
        self.body.setReadOnly(not self._editing)
        self.edit_btn.setText("完成" if self._editing else "编辑")

    def _copy_code(self) -> None:
        try:
            QGuiApplication.clipboard().setText(self.body.toPlainText())
        except Exception:
            pass

    def _emit_send(self) -> None:
        self.send_requested.emit(self.body.toPlainText())

    def _emit_apply(self) -> None:
        self.apply_requested.emit(self.body.toPlainText(), self._lang)


class _MdBody(QTextBrowser):
    """渲染 Markdown 主体（无额外描边，外层卡片已是容器）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("mdBody")
        self.setOpenExternalLinks(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet(
            f"""
            QTextBrowser#mdBody {{
                background: transparent;
                color: {INK};
                border: none;
                padding: 0;
                font-size: {FS_BODY}px;
            }}
            """
        )
        html_body = markdown_to_html(text)
        self.setHtml(html_body)
        self.document().setDocumentMargin(2)
        self._fit_height()

    def _fit_height(self) -> None:
        doc = self.document()
        doc.setTextWidth(max(120, self.viewport().width() or 360))
        h = int(doc.size().height()) + 8
        self.setFixedHeight(max(28, min(h, 720)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()


class _BodyText(QLabel):
    """纯文本主体（用户短句等）。"""

    def __init__(self, text: str, parent=None, *, bold: bool = False):
        super().__init__(text, parent)
        self.setObjectName("bodyText")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextSelectableByMouse)
        weight = QFont.DemiBold if bold else QFont.Normal
        self.setFont(QFont("Microsoft YaHei UI", FS_BODY, weight))
        self.setStyleSheet(
            f"QLabel#bodyText {{ color: {INK}; padding: 2px 0; background: transparent; }}"
        )


class _SectionTag(QLabel):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setObjectName("sectionTag")
        self.setFont(QFont("Microsoft YaHei UI", FS_HINT, QFont.DemiBold))
        self.setStyleSheet(
            """
            QLabel#sectionTag {
                color: #5B677A;
                background: #EEF3F9;
                border: 1px solid #D6E1EC;
                border-radius: 6px;
                padding: 2px 8px;
            }
            """
        )


class _MetaSection(QWidget):
    """次要信息（思考/工具）：无框体；默认折叠，点标题展开。"""

    def __init__(self, title: str = "过程详情", parent=None):
        super().__init__(parent)
        self._lines: list[str] = []
        self._expanded = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(2)

        self.toggle_btn = QPushButton(f"▸ {title}", self)
        self.toggle_btn.setObjectName("metaToggle")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.setFlat(True)
        self.toggle_btn.setStyleSheet(
            f"""
            QPushButton#metaToggle {{
                color: {MUTED};
                background: transparent;
                border: none;
                text-align: left;
                padding: 2px 0;
                font-size: {FS_META}px;
                font-weight: 500;
            }}
            QPushButton#metaToggle:hover {{ color: #5A524C; }}
            """
        )
        self.toggle_btn.setFont(QFont("Microsoft YaHei UI", FS_META))
        self.toggle_btn.clicked.connect(self._toggle)
        lay.addWidget(self.toggle_btn)

        self.body = QLabel("", self)
        self.body.setObjectName("metaBody")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.body.setFont(QFont("Microsoft YaHei UI", FS_META))
        self.body.setStyleSheet(
            f"QLabel#metaBody {{ color: #9A9088; font-weight: 400; padding: 0 0 2px 12px; }}"
        )
        self.body.hide()
        lay.addWidget(self.body)
        self._title = title
        self.hide()

    def _toggle(self):
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        prefix = "▾" if self._expanded else "▸"
        n = len(self._lines)
        self.toggle_btn.setText(f"{prefix} {self._title}" + (f" · {n}" if n else ""))

    def append_line(self, text: str) -> None:
        t = (text or "").strip()
        if not t:
            return
        if self._lines and t in self._lines[-3:]:
            return
        self._lines.append(t)
        # 保留末尾
        if len(self._lines) > 24:
            self._lines = self._lines[-24:]
        self.body.setText("\n".join(self._lines))
        self.show()
        prefix = "▾" if self._expanded else "▸"
        self.toggle_btn.setText(f"{prefix} {self._title} · {len(self._lines)}")

    def set_lines(self, lines: list[str]) -> None:
        self._lines = [str(x).strip() for x in lines if str(x).strip()]
        if not self._lines:
            self.hide()
            return
        self.body.setText("\n".join(self._lines))
        self.show()
        prefix = "▾" if self._expanded else "▸"
        self.toggle_btn.setText(f"{prefix} {self._title} · {len(self._lines)}")


# 兼容旧名
class _TextBlock(_BodyText):
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent, bold=True)


class MessageCard(QFrame):
    """一条消息：短文直接全文；长文/代码块才默认摘要可展开。"""

    toggled = Signal(bool)
    feedback = Signal(str, str)  # message_id, rating up|down
    rewind_requested = Signal(str)  # message_id — 从此用户消息重开
    retry_requested = Signal(str)  # message_id — 失败助手消息重试
    code_apply_requested = Signal(str, str, str)  # code, lang, message_id
    code_send_requested = Signal(str)  # code

    def __init__(
        self,
        role: str,
        ts: str,
        text: str,
        parent=None,
        *,
        msg_id: str = "",
        show_feedback: bool = False,
        process: list[str] | None = None,
        terminals: list[dict] | None = None,
        meta: dict | None = None,
    ):
        super().__init__(parent)
        self._role = role
        self._ts = ts or ""
        self._full = normalize_markdown(text or "")
        self._msg_id = msg_id or ""
        self._meta = dict(meta or {})
        self._process = [str(x).strip() for x in (process or []) if str(x).strip()]
        self._terminals = list(terminals or [])
        self._collapsible = needs_collapse(self._full)
        self._expanded = not self._collapsible  # 短消息视为已展开
        self.setObjectName("msgCard")
        self.setCursor(Qt.PointingHandCursor if self._collapsible else Qt.ArrowCursor)
        bg = USER_BG if role == "user" else ASSIST_BG
        status = str(self._meta.get("status") or "")
        if status in ("failed", "interrupted", "cancelled"):
            bg = "#F8E8E8"
        self.setStyleSheet(
            f"""
            QFrame#msgCard {{
                background: {bg};
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
            QFrame#msgCard:hover {{ border-color: #8EB4D8; }}
            QLabel#msgName {{
                color: {INK};
                font-weight: 700;
                font-size: {FS_HEAD}px;
            }}
            QLabel#msgTime {{
                color: {MUTED};
                font-weight: 400;
                font-size: {FS_TIME}px;
            }}
            QLabel#msgHint {{
                color: {MUTED};
                font-weight: 400;
                font-size: {FS_HINT}px;
            }}
            QPushButton#expandBtn, QPushButton#fbBtn, QPushButton#actBtn {{
                background: transparent;
                color: #555;
                border: 1px solid #E0D8CC;
                border-radius: 6px;
                padding: 2px 8px;
                font-weight: 600;
                font-size: {FS_BTN}px;
            }}
            QPushButton#expandBtn:hover, QPushButton#fbBtn:hover, QPushButton#actBtn:hover {{
                background: #8EB4D8; color: white; border-color: #8EB4D8;
            }}
            """
        )

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(12, 8, 12, 8)
        self._root.setSpacing(4)

        head = QHBoxLayout()
        head.setSpacing(6)
        tag = {"user": "我", "assistant": "助手", "system": "系统", "alarm": "闹钟"}.get(
            role, role
        )
        if role == "assistant":
            try:
                from agent.identity import assistant_label

                tag = assistant_label()
            except Exception:
                tag = "Mini_Lu"
        self.name_lab = QLabel(tag, self)
        self.name_lab.setObjectName("msgName")
        self.name_lab.setFont(QFont("Microsoft YaHei UI", FS_HEAD, QFont.DemiBold))
        head.addWidget(self.name_lab)
        if self._ts:
            self.time_lab = QLabel(self._ts, self)
            self.time_lab.setObjectName("msgTime")
            self.time_lab.setFont(QFont("Microsoft YaHei UI", FS_TIME))
            head.addWidget(self.time_lab)
        head.addStretch()

        self.up_btn = QPushButton("👍", self)
        self.up_btn.setObjectName("fbBtn")
        self.up_btn.setFixedWidth(32)
        self.up_btn.setToolTip("这条回复有帮助")
        self.up_btn.clicked.connect(lambda: self._emit_fb("up"))
        self.down_btn = QPushButton("👎", self)
        self.down_btn.setObjectName("fbBtn")
        self.down_btn.setFixedWidth(32)
        self.down_btn.setToolTip("这条回复不好（可写原因，用于改写 prompt）")
        self.down_btn.clicked.connect(lambda: self._emit_fb("down"))
        show_fb = bool(show_feedback and role == "assistant" and self._msg_id)
        self.up_btn.setVisible(show_fb)
        self.down_btn.setVisible(show_fb)
        head.addWidget(self.up_btn)
        head.addWidget(self.down_btn)

        self.rewind_btn = QPushButton("从此重开", self)
        self.rewind_btn.setObjectName("actBtn")
        self.rewind_btn.setToolTip(
            "载入该条到输入框编辑；发送后才截断重跑，取消则原对话不变"
        )
        self.rewind_btn.clicked.connect(
            lambda: self.rewind_requested.emit(self._msg_id)
        )
        self.rewind_btn.setVisible(bool(role == "user" and self._msg_id))
        head.addWidget(self.rewind_btn)

        self.retry_btn = QPushButton("重试", self)
        self.retry_btn.setObjectName("actBtn")
        self.retry_btn.setToolTip("用同一请求重试（网络中断/失败后）")
        self.retry_btn.clicked.connect(lambda: self.retry_requested.emit(self._msg_id))
        retryable = bool(
            role == "assistant"
            and self._msg_id
            and (
                status in ("failed", "interrupted", "cancelled")
                or bool(self._meta.get("retryable"))
            )
        )
        self.retry_btn.setVisible(retryable)
        head.addWidget(self.retry_btn)

        self.expand_btn = QPushButton("展开", self)
        self.expand_btn.setObjectName("expandBtn")
        self.expand_btn.setFont(QFont("Microsoft YaHei UI", FS_BTN))
        self.expand_btn.clicked.connect(self.toggle)
        self.expand_btn.setVisible(self._collapsible)
        head.addWidget(self.expand_btn)
        self._root.addLayout(head)

        # 过程 / 终端：在正文上方，默认折叠，回答结束后仍保留
        self.meta_sec = _MetaSection("过程详情", self)
        if self._process:
            self.meta_sec.set_lines(self._process)
        else:
            self.meta_sec.hide()
        self._root.addWidget(self.meta_sec)

        self.term_host = QVBoxLayout()
        self.term_host.setSpacing(6)
        self._root.addLayout(self.term_host)
        for t in self._terminals:
            tb = TerminalBlock(self)
            tb.set_readonly_result(
                command=str(t.get("command") or ""),
                cwd=str(t.get("cwd") or ""),
                output=str(t.get("output") or ""),
                ok=bool(t.get("ok", True)),
            )
            self.term_host.addWidget(tb)

        self.body_host = QVBoxLayout()
        self.body_host.setSpacing(6)
        self._root.addLayout(self.body_host)

        self.hint = QLabel("单击卡片或「展开」查看全文", self)
        self.hint.setObjectName("msgHint")
        self.hint.setFont(QFont("Microsoft YaHei UI", FS_HINT))
        self.hint.setVisible(self._collapsible)
        self._root.addWidget(self.hint)

        if self._collapsible:
            self._render_collapsed()
        else:
            self._render_expanded()

    def _emit_fb(self, rating: str):
        if self._msg_id:
            self.feedback.emit(self._msg_id, rating)

    def mouseReleaseEvent(self, event):
        if not self._collapsible:
            super().mouseReleaseEvent(event)
            return
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            w = child
            while w is not None and w is not self:
                # 按钮 / 过程区 / 终端 / 可交互正文：不触发展开收起
                if w in (self.expand_btn, self.up_btn, self.down_btn, self.meta_sec):
                    super().mouseReleaseEvent(event)
                    return
                if isinstance(w, (TerminalBlock, _MdBody, QTextEdit, QPushButton)):
                    super().mouseReleaseEvent(event)
                    return
                w = w.parentWidget()
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def toggle(self):
        if not self._collapsible:
            return
        self._expanded = not self._expanded
        if self._expanded:
            self._render_expanded()
            self.expand_btn.setText("收起")
            self.hint.setText("单击收起")
        else:
            self._render_collapsed()
            self.expand_btn.setText("展开")
            self.hint.setText("单击卡片或「展开」查看全文")
        self.toggled.emit(self._expanded)

    def _clear_body(self):
        while self.body_host.count():
            item = self.body_host.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _render_collapsed(self):
        self._clear_body()
        plain = _FENCE_RE.sub(r"[代码块]", self._full)
        heading = ""
        for ln in self._full.splitlines():
            s = ln.strip()
            if s.startswith("#"):
                heading = re.sub(r"^#+\s*", "", s).strip()
                break
        prev = preview_text(strip_md(plain))
        if heading:
            prev = f"{heading}\n{prev}"
        box = _BodyText(prev, parent=self, bold=False)
        self.body_host.addWidget(box)
        segs = split_markdown_segments(self._full)
        n_code = sum(1 for k, _, __ in segs if k == "code")
        if n_code:
            tip = QLabel(f"含 {n_code} 个代码块（展开后分框显示）", self)
            tip.setObjectName("msgHint")
            tip.setFont(QFont("Microsoft YaHei UI", FS_HINT))
            self.body_host.addWidget(tip)

    def _render_expanded(self):
        self._clear_body()
        segs = split_markdown_segments(self._full)
        if not segs:
            self.body_host.addWidget(_BodyText("（空）", parent=self))
            return
        seen_text = False
        seen_code = False
        for kind, body, lang in segs:
            if kind == "code":
                if not seen_code:
                    self.body_host.addWidget(_SectionTag("代码块", parent=self))
                    seen_code = True
                lang_l = (lang or "").lower()
                if lang_l in ("shell", "bash", "powershell", "pwsh", "cmd", "terminal", "console"):
                    tb = TerminalBlock(parent=self)
                    tb.set_readonly_result(command="", cwd="", output=body, ok=True)
                    self.body_host.addWidget(tb)
                else:
                    cb = _CodeBlock(body, lang, parent=self)
                    cb.send_requested.connect(self.code_send_requested.emit)
                    cb.apply_requested.connect(
                        lambda code, lg, mid=self._msg_id: self.code_apply_requested.emit(
                            code, lg, mid
                        )
                    )
                    self.body_host.addWidget(cb)
            else:
                # 助手：Markdown 渲染；用户：纯文本即可
                if self._role == "assistant":
                    if not seen_text:
                        self.body_host.addWidget(_SectionTag("正文", parent=self))
                        seen_text = True
                    self.body_host.addWidget(_MdBody(body, parent=self))
                else:
                    self.body_host.addWidget(_BodyText(body, parent=self, bold=False))


class MessageList(QWidget):
    """可滚动消息列表。"""

    feedback = Signal(str, str)  # message_id, rating
    rewind_requested = Signal(str)  # message_id
    retry_requested = Signal(str)  # message_id
    code_apply_requested = Signal(str, str, str)  # code, lang, message_id
    code_send_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget(self.scroll)
        self.lay = QVBoxLayout(self.host)
        self.lay.setContentsMargins(4, 4, 4, 4)
        self.lay.setSpacing(10)
        self.lay.addStretch(1)
        self.scroll.setWidget(self.host)
        outer.addWidget(self.scroll)
        self._stream_card: StreamingAssistantCard | None = None

    def clear(self, *, keep_stream: bool = False):
        kept = None
        if keep_stream:
            kept = getattr(self, "_stream_card", None)
            self._stream_card = None
        else:
            self._stream_card = None
        # 保留最后的 stretch；保持父级直到 deleteLater，避免 setParent(None) 再闪顶层窗
        while self.lay.count() > 1:
            item = self.lay.takeAt(0)
            w = item.widget()
            if w is None:
                continue
            if kept is not None and w is kept:
                # 先摘下，稍后挂回
                continue
            w.hide()
            w.deleteLater()
        if keep_stream and kept is not None:
            self._stream_card = kept
            self.lay.insertWidget(self.lay.count() - 1, kept)
            kept.show()

    def set_messages(self, messages: list[dict], *, keep_stream: bool = False):
        # 流式进行中或仍有未确认终端时保留/恢复审批 UI，避免刷掉后工作线程卡死
        has_pending = False
        try:
            from agent.command_approval import list_pending_approvals

            has_pending = bool(list_pending_approvals())
        except Exception:
            pass
        if keep_stream or getattr(self, "_stream_card", None) is not None or has_pending:
            keep_stream = True
        self.clear(keep_stream=keep_stream)
        insert_at = self.lay.count() - 1
        if keep_stream and self._stream_card is not None:
            # stream 已在末尾 stretch 前；历史插在 stream 之前
            idx = self.lay.indexOf(self._stream_card)
            insert_at = idx if idx >= 0 else self.lay.count() - 1
        if not messages:
            if not keep_stream:
                empty = QLabel("（本对话暂无消息）", self.host)
                empty.setFont(QFont("Microsoft YaHei UI", FS_BODY))
                empty.setStyleSheet(f"color: {MUTED}; padding: 12px;")
                self.lay.insertWidget(0, empty)
            self._restore_pending_approvals()
            return
        for m in messages:
            meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
            card = MessageCard(
                role=str(m.get("role") or "assistant"),
                ts=str(m.get("ts") or ""),
                text=str(m.get("text") or ""),
                parent=self.host,
                msg_id=str(m.get("id") or ""),
                show_feedback=True,
                process=list(meta.get("process") or []),
                terminals=list(meta.get("terminals") or []),
                meta=meta,
            )
            card.feedback.connect(self.feedback.emit)
            card.rewind_requested.connect(self.rewind_requested.emit)
            card.retry_requested.connect(self.retry_requested.emit)
            card.code_apply_requested.connect(self.code_apply_requested.emit)
            card.code_send_requested.connect(self.code_send_requested.emit)
            self.lay.insertWidget(insert_at, card)
            insert_at += 1
        from PySide6.QtCore import QTimer

        QTimer.singleShot(30, self._scroll_bottom)
        self._restore_pending_approvals()

    def _restore_pending_approvals(self) -> None:
        """若仍有未确认终端请求，确保流式卡片上重新显示审批按钮。"""
        try:
            from agent.command_approval import list_pending_approvals

            pending = list_pending_approvals()
        except Exception:
            pending = []
        if not pending:
            return
        card = self.stream_card
        if card is None:
            card = self.begin_assistant_stream()
        for item in pending:
            card.upsert_terminal_pending(
                request_id=str(item.get("request_id") or ""),
                command=str(item.get("command") or ""),
                cwd=str(item.get("cwd") or ""),
            )
        self._scroll_bottom()

    def _scroll_bottom(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def begin_assistant_stream(self) -> "StreamingAssistantCard":
        """开始一条流式助手消息（先去掉空状态）。"""
        # 去掉「暂无消息」占位
        if self.lay.count() == 2:
            w0 = self.lay.itemAt(0).widget()
            if isinstance(w0, QLabel) and "暂无消息" in (w0.text() or ""):
                self.lay.takeAt(0)
                w0.hide()
                w0.deleteLater()
        if self._stream_card is not None:
            self.finalize_assistant_stream()
        card = StreamingAssistantCard(parent=self.host)
        card.terminal_decided.connect(self._on_terminal_decided)
        self._stream_card = card
        self.lay.insertWidget(self.lay.count() - 1, card)
        self._scroll_bottom()
        return card

    def _on_terminal_decided(self, request_id: str, action: str) -> None:
        try:
            from agent.command_approval import resolve_command_approval

            resolve_command_approval(request_id, action)
        except Exception:
            pass

    @property
    def stream_card(self) -> "StreamingAssistantCard | None":
        return getattr(self, "_stream_card", None)

    def handle_stream_event(self, ev: dict) -> None:
        if not isinstance(ev, dict):
            return
        kind = str(ev.get("kind") or "")
        card = self.stream_card
        if kind == "command_approval":
            if card is None:
                card = self.begin_assistant_stream()
            card.upsert_terminal_pending(
                request_id=str(ev.get("request_id") or ""),
                command=str(ev.get("command") or ""),
                cwd=str(ev.get("cwd") or ""),
            )
            self._scroll_bottom()
            return
        if kind == "command_auto":
            if card is None:
                card = self.begin_assistant_stream()
            card.upsert_terminal_running(
                request_id=str(ev.get("request_id") or ""),
                command=str(ev.get("command") or ""),
                cwd=str(ev.get("cwd") or ""),
                note="已信任 · 自动运行",
            )
            self._scroll_bottom()
            return
        if kind == "command_result":
            if card is not None:
                card.apply_terminal_result(ev)
            self._scroll_bottom()
            return
        if card is None and kind in ("thinking", "token", "status", "tool", "plan"):
            card = self.begin_assistant_stream()
        if card is None:
            return
        if kind == "plan":
            steps = ev.get("steps") or []
            if isinstance(steps, list) and steps:
                lines = ["【执行计划】"]
                for i, s in enumerate(steps):
                    lines.append(f"  {i+1}. {s}")
                card.append_thinking("\n".join(lines))
            else:
                card.append_thinking(str(ev.get("text") or "已生成计划"))
        elif kind in ("thinking", "status", "tool"):
            card.append_thinking(str(ev.get("text") or ""))
        elif kind == "token":
            card.append_answer(str(ev.get("text") or ""))
        self._scroll_bottom()

    def finalize_assistant_stream(self) -> dict:
        """结束流式卡片，返回 {text, process, terminals}。"""
        card = getattr(self, "_stream_card", None)
        self._stream_card = None
        if card is None:
            return {"text": "", "process": [], "terminals": []}
        snap = card.snapshot()
        card.mark_done()
        return snap


class TerminalBlock(QFrame):
    """对话内嵌终端：待确认 / 运行中 / 结果。"""

    decided = Signal(str, str)  # request_id, action allow|deny|always

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rid = ""
        self.setObjectName("termBlock")
        self.setStyleSheet(
            """
            QFrame#termBlock {
                background: #1A1F2A;
                border: 1px solid #3A4558;
                border-radius: 10px;
            }
            QLabel#termHead {
                color: #C5D0E0;
                font-weight: 600;
                padding: 2px 0;
                font-size: 11px;
            }
            QLabel#termMeta { color: #8B9BB0; font-size: 10px; }
            QTextEdit#termBody {
                background: #12161E;
                color: #E6EDF7;
                border: 1px solid #2C3545;
                border-radius: 6px;
                padding: 8px;
                font-family: Cascadia Code, Consolas, monospace;
                font-size: 11px;
            }
            QPushButton#termAllow {
                background: #3D7EA6; color: white; border: none;
                border-radius: 6px; padding: 5px 11px; font-weight: 600;
                font-size: 10px;
            }
            QPushButton#termAllow:hover { background: #2F6A8C; }
            QPushButton#termAlways {
                background: #2F6A4A; color: white; border: none;
                border-radius: 6px; padding: 5px 11px; font-weight: 600;
                font-size: 10px;
            }
            QPushButton#termAlways:hover { background: #25553A; }
            QPushButton#termDeny {
                background: #3A4558; color: #E8ECF4; border: none;
                border-radius: 6px; padding: 5px 11px; font-weight: 500;
                font-size: 10px;
            }
            QPushButton#termDeny:hover { background: #C45C5C; color: white; }
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)

        head = QHBoxLayout()
        self.head = QLabel("> 终端", self)
        self.head.setObjectName("termHead")
        self.head.setFont(QFont("Segoe UI", FS_HEAD, QFont.DemiBold))
        head.addWidget(self.head)
        head.addStretch()
        self.meta = QLabel("", self)
        self.meta.setObjectName("termMeta")
        self.meta.setFont(QFont("Segoe UI", FS_TIME))
        self.meta.hide()
        head.addWidget(self.meta)
        lay.addLayout(head)

        self.body = QTextEdit(self)
        self.body.setObjectName("termBody")
        self.body.setReadOnly(True)
        self.body.setMinimumHeight(72)
        self.body.setMaximumHeight(220)
        lay.addWidget(self.body)

        self.btn_row = QHBoxLayout()
        self.btn_row.addStretch(1)
        self.btn_allow = QPushButton("运行", self)
        self.btn_allow.setObjectName("termAllow")
        self.btn_always = QPushButton("总是允许", self)
        self.btn_always.setObjectName("termAlways")
        self.btn_deny = QPushButton("取消", self)
        self.btn_deny.setObjectName("termDeny")
        self.btn_allow.clicked.connect(lambda: self._decide("allow"))
        self.btn_always.clicked.connect(lambda: self._decide("always"))
        self.btn_deny.clicked.connect(lambda: self._decide("deny"))
        for b in (self.btn_deny, self.btn_always, self.btn_allow):
            b.setCursor(Qt.PointingHandCursor)
            self.btn_row.addWidget(b)
        lay.addLayout(self.btn_row)

    def _set_meta(self, text: str) -> None:
        t = (text or "").strip()
        if t:
            self.meta.setText(t)
            self.meta.show()
        else:
            self.meta.clear()
            self.meta.hide()

    def _decide(self, action: str):
        self.btn_allow.setEnabled(False)
        self.btn_always.setEnabled(False)
        self.btn_deny.setEnabled(False)
        if action == "allow":
            self._set_meta("运行中…")
        elif action == "always":
            self._set_meta("已信任 · 运行中…")
        else:
            self._set_meta("已取消")
        self.decided.emit(self._rid, action)

    def set_pending(self, request_id: str, command: str, cwd: str = "") -> None:
        self._rid = request_id
        self.head.setText("> 终端 · 等待确认")
        self._set_meta("")  # 不显示「需人工确认」
        lines = []
        if cwd:
            lines.append(f"# cwd: {cwd}")
        lines.append(f"$ {command}")
        self.body.setPlainText("\n".join(lines))
        for b in (self.btn_allow, self.btn_always, self.btn_deny):
            b.setVisible(True)
            b.setEnabled(True)

    def set_running(self, request_id: str, command: str, cwd: str = "", note: str = "") -> None:
        self._rid = request_id
        self.head.setText("> 终端 · 运行中")
        self._set_meta(note or "运行中…")
        lines = []
        if cwd:
            lines.append(f"# cwd: {cwd}")
        lines.append(f"$ {command}")
        self.body.setPlainText("\n".join(lines))
        for b in (self.btn_allow, self.btn_always, self.btn_deny):
            b.setVisible(False)

    def set_readonly_result(
        self,
        *,
        command: str,
        cwd: str,
        output: str,
        ok: bool,
    ) -> None:
        self.head.setText("> 终端" + (" · 完成" if ok else " · 失败"))
        self._set_meta("ok" if ok else "error")
        parts = []
        if cwd:
            parts.append(f"# cwd: {cwd}")
        if command:
            parts.append(f"$ {command}")
        if output:
            parts.append(output)
        self.body.setPlainText("\n".join(parts).strip() or "（无输出）")
        for b in (self.btn_allow, self.btn_always, self.btn_deny):
            b.setVisible(False)
        lines = max(3, min(14, (self.body.toPlainText().count("\n") + 1)))
        self.body.setFixedHeight(min(220, 24 + lines * 16))

    def apply_result(self, ev: dict) -> None:
        denied = bool(ev.get("denied"))
        ok = bool(ev.get("ok")) and not denied
        cmd = str(ev.get("command") or "")
        cwd = str(ev.get("cwd") or "")
        out = str(ev.get("output") or "")
        if denied:
            self.head.setText("> 终端 · 已取消")
            self._set_meta("")
        else:
            code = ev.get("exit_code")
            self.head.setText("> 终端 · 完成" if ok else "> 终端 · 失败")
            self._set_meta(
                f"exit {code}" if code is not None else ("ok" if ok else "error")
            )
        parts = []
        if cwd:
            parts.append(f"# cwd: {cwd}")
        if cmd:
            parts.append(f"$ {cmd}")
        if out:
            parts.append(out)
        self.body.setPlainText("\n".join(parts).strip())
        for b in (self.btn_allow, self.btn_always, self.btn_deny):
            b.setVisible(False)
        lines = max(3, min(14, (self.body.toPlainText().count("\n") + 1)))
        self.body.setFixedHeight(min(220, 24 + lines * 16))


class StreamingAssistantCard(QFrame):
    """流式助手卡：主体框内正文；思考/工具为无框可折叠元信息。"""

    terminal_decided = Signal(str, str)  # request_id, action

    def __init__(self, parent=None):
        super().__init__(parent)
        self._answer = ""
        self._terminals: dict[str, TerminalBlock] = {}
        self._term_records: dict[str, dict] = {}
        self.setObjectName("streamCard")
        self.setStyleSheet(
            f"""
            QFrame#streamCard {{
                background: {ASSIST_BG};
                border: 1px solid #8EB4D8;
                border-radius: 10px;
            }}
            QLabel#streamName {{
                color: {INK}; font-weight: 700; font-size: {FS_HEAD}px;
            }}
            QLabel#streamStatus {{
                color: {MUTED}; font-weight: 400; font-size: {FS_TIME}px;
            }}
            QLabel#answerBody {{
                color: {INK};
                font-weight: 600;
                font-size: {FS_BODY}px;
                padding: 2px 0;
            }}
            """
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(4)

        try:
            from agent.identity import assistant_label

            tag = assistant_label()
        except Exception:
            tag = "Mini_Lu"
        head = QHBoxLayout()
        head.setSpacing(6)
        self.name_lab = QLabel(tag, self)
        self.name_lab.setObjectName("streamName")
        self.name_lab.setFont(QFont("Microsoft YaHei UI", FS_HEAD, QFont.DemiBold))
        head.addWidget(self.name_lab)
        self.status_lab = QLabel("生成中…", self)
        self.status_lab.setObjectName("streamStatus")
        self.status_lab.setFont(QFont("Microsoft YaHei UI", FS_TIME))
        head.addWidget(self.status_lab)
        head.addStretch()
        lay.addLayout(head)

        # 思考/工具：无框，默认折叠
        self.meta = _MetaSection("过程详情", self)
        lay.addWidget(self.meta)

        self.term_host = QVBoxLayout()
        self.term_host.setSpacing(6)
        lay.addLayout(self.term_host)

        self.answer_lab = QLabel("", self)
        self.answer_lab.setObjectName("answerBody")
        self.answer_lab.setWordWrap(True)
        self.answer_lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.answer_lab.setFont(QFont("Microsoft YaHei UI", FS_BODY, QFont.DemiBold))
        lay.addWidget(self.answer_lab)

    def append_thinking(self, text: str) -> None:
        self.meta.append_line(text)

    def append_answer(self, text: str) -> None:
        if not text:
            return
        self._answer += text
        # 流式阶段先显示纯文本；定稿后由 MessageCard 做 Markdown 渲染
        self.answer_lab.setText(self._answer)

    def answer_text(self) -> str:
        return self._answer.strip()

    def snapshot(self) -> dict:
        """供写入 chat_history.meta。"""
        return {
            "text": self._answer.strip(),
            "process": list(getattr(self.meta, "_lines", []) or []),
            "terminals": list(self._term_records.values()),
        }

    def upsert_terminal_pending(self, *, request_id: str, command: str, cwd: str) -> None:
        rid = request_id or command
        block = self._terminals.get(rid)
        if block is None:
            block = TerminalBlock(self)
            block.decided.connect(self.terminal_decided.emit)
            self._terminals[rid] = block
            self.term_host.addWidget(block)
        self._term_records[rid] = {
            "command": command,
            "cwd": cwd or "",
            "output": "",
            "ok": False,
            "pending": True,
        }
        block.set_pending(rid, command, cwd)

    def upsert_terminal_running(
        self, *, request_id: str, command: str, cwd: str, note: str = ""
    ) -> None:
        rid = request_id or command
        block = self._terminals.get(rid)
        if block is None:
            block = TerminalBlock(self)
            block.decided.connect(self.terminal_decided.emit)
            self._terminals[rid] = block
            self.term_host.addWidget(block)
        self._term_records[rid] = {
            "command": command,
            "cwd": cwd or "",
            "output": "",
            "ok": True,
            "pending": False,
            "note": note,
        }
        block.set_running(rid, command, cwd, note=note)

    def apply_terminal_result(self, ev: dict) -> None:
        rid = str(ev.get("request_id") or "")
        block = self._terminals.get(rid)
        if block is None and rid:
            block = TerminalBlock(self)
            self._terminals[rid] = block
            self.term_host.addWidget(block)
        denied = bool(ev.get("denied"))
        ok = bool(ev.get("ok")) and not denied
        self._term_records[rid or str(len(self._term_records))] = {
            "command": str(ev.get("command") or ""),
            "cwd": str(ev.get("cwd") or ""),
            "output": str(ev.get("output") or ""),
            "ok": ok,
            "denied": denied,
            "exit_code": ev.get("exit_code"),
        }
        if block is not None:
            block.apply_result(ev)

    def mark_done(self) -> None:
        self.status_lab.setText("完成")
