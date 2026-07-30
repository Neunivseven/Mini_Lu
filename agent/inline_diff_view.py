"""
内联红绿 diff 视图：按 hunk 高亮；当前段更醒目。
已保留段只显示新代码；已放弃段只显示旧代码。
每个 pending hunk 在修改处内联显示"保留 / 放弃"按钮。
"""
from __future__ import annotations

import difflib
from xml.sax.saxutils import escape

from PySide6.QtCore import Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout, QWidget

from agent.ui_fonts import mono_font, mono_font_family, ui_font
from agent.ui_zoom import pt, px

DEL_BG = "#F8D7DA"
DEL_FG = "#842029"
INS_BG = "#D1E7DD"
INS_FG = "#0F5132"
EQ_FG = "#1E293B"
MUTED = "#64748B"
PANEL_BG = "#F8FBFE"
FOCUS_OUTLINE = "#3D7EA6"
FOCUS_MARK = "#1E4A66"
KEPT_BG = "#D8F3E7"
DISC_BG = "#EEF2F6"

_BASE_CODE_PX = 12.5
_BASE_MONO_PT = 12
_BASE_HEAD_PT = 9


def _code_font_css() -> str:
    fam = mono_font_family().replace('"', "")
    return f"'{fam}','JetBrains Mono','DejaVu Sans Mono',Consolas,monospace"


def _esc(s: str) -> str:
    return escape(s.rstrip("\r\n"), {"\"": "&quot;"})


def _lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines(keepends=True)


_BTN_CSS = (
    "display:inline-block;padding:2px 10px;margin:0 4px;border-radius:4px;"
    "font-size:11px;font-weight:600;cursor:pointer;text-decoration:none;"
)
_KEEP_BTN = (
    f'<a href="keep:{{hid}}" style="{_BTN_CSS}'
    'background:#D1E7DD;color:#0F5132;border:1px solid #A3CFBB;">✓ 保留</a>'
)
_DISC_BTN = (
    f'<a href="discard:{{hid}}" style="{_BTN_CSS}'
    'background:#F8D7DA;color:#842029;border:1px solid #F1AEB5;">✗ 放弃</a>'
)
_KEEP_ALL_BTN = (
    f'<a href="keep_all:{{hid}}" style="{_BTN_CSS}'
    'background:#E8F5E9;color:#2E7D32;border:1px solid #A5D6A7;">全部保留</a>'
)
_DISC_ALL_BTN = (
    f'<a href="discard_all:{{hid}}" style="{_BTN_CSS}'
    'background:#FFF3E0;color:#E65100;border:1px solid #FFCC80;">全部放弃</a>'
)


def build_inline_diff_html(
    before: str,
    after: str,
    hunks: list[dict] | None = None,
    focus_hunk_id: int | None = None,
) -> str:
    """行级 diff HTML；hunks 带 status 时按段渲染，pending 段内联操作按钮。"""
    a = _lines(before)
    b = _lines(after)
    code_fs = px(_BASE_CODE_PX)
    if not a and not b:
        return (
            f'<div style="margin:0;font-family:{_code_font_css()};'
            f'font-size:{code_fs};font-weight:400;color:{MUTED};padding:8px;">（空）</div>'
        )

    by_span = {}
    for h in hunks or []:
        by_span[(h["i1"], h["i2"], h["j1"], h["j2"])] = h

    rows: list[str] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for line in a[i1:i2]:
                rows.append(
                    f'<div style="color:{EQ_FG};padding:1px 8px;font-weight:400;">'
                    f"  {_esc(line) or '&nbsp;'}</div>"
                )
            continue

        h = by_span.get((i1, i2, j1, j2))
        hid = int(h["id"]) if h else -1
        status = (h or {}).get("status", "pending")
        focused = focus_hunk_id is not None and hid == int(focus_hunk_id)

        wrap_style = "margin:2px 0;border-radius:4px;"
        if focused and status == "pending":
            wrap_style += (
                f"border:2px solid {FOCUS_OUTLINE};"
                f"box-shadow:0 0 0 1px {FOCUS_OUTLINE};"
            )

        block: list[str] = []

        # pending 段：在顶部内联显示操作按钮
        if status == "pending":
            label = f"改动段 #{hid + 1}"
            btn_row = (
                f'<div style="padding:4px 8px;background:#F0F4F8;border-radius:4px 4px 0 0;'
                f'display:flex;align-items:center;">'
                f'<span style="color:{FOCUS_MARK};font-weight:600;font-size:11px;'
                f'font-family:{_code_font_css()};margin-right:8px;">{label}</span>'
                f'{_KEEP_BTN.format(hid=hid)}'
                f'{_DISC_BTN.format(hid=hid)}'
                f'{_KEEP_ALL_BTN.format(hid=hid)}'
                f'{_DISC_ALL_BTN.format(hid=hid)}'
                f'</div>'
            )
            block.append(btn_row)

        if status == "kept":
            for line in b[j1:j2]:
                block.append(
                    f'<div style="background:{KEPT_BG};color:{INS_FG};'
                    f'padding:1px 8px;font-weight:400;">  {_esc(line) or "&nbsp;"}</div>'
                )
        elif status == "discarded":
            for line in a[i1:i2]:
                block.append(
                    f'<div style="background:{DISC_BG};color:{MUTED};'
                    f'padding:1px 8px;font-weight:400;">  {_esc(line) or "&nbsp;"}</div>'
                )
        else:
            if tag in ("delete", "replace"):
                for line in a[i1:i2]:
                    block.append(
                        f'<div style="background:{DEL_BG};color:{DEL_FG};'
                        f'padding:1px 8px;font-weight:400;">− {_esc(line) or "&nbsp;"}</div>'
                    )
            if tag in ("insert", "replace"):
                for line in b[j1:j2]:
                    block.append(
                        f'<div style="background:{INS_BG};color:{INS_FG};'
                        f'padding:1px 8px;font-weight:400;">+ {_esc(line) or "&nbsp;"}</div>'
                    )

        rows.append(f'<div style="{wrap_style}">{"".join(block)}</div>')

    body = "".join(rows)
    return (
        f'<div style="background:{PANEL_BG};font-family:{_code_font_css()};'
        f'font-size:{code_fs};line-height:1.55;font-weight:400;letter-spacing:0.01em;">'
        f"{body}</div>"
    )


class InlineDiffView(QWidget):
    """内联红绿 diff；支持当前 hunk 高亮和内联操作按钮。"""

    hunk_keep = Signal(int)
    hunk_discard = Signal(int)
    hunk_keep_all = Signal()
    hunk_discard_all = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.head = QLabel("改动预览（红=修改前 · 绿=修改后 · 点击按钮确认）", self)
        lay.addWidget(self.head)

        self.diff_view = QTextBrowser(self)
        self.diff_view.setReadOnly(True)
        self.diff_view.setOpenLinks(False)
        self.diff_view.anchorClicked.connect(self._on_link)
        lay.addWidget(self.diff_view, 1)

        self._last: tuple[str, str, list[dict] | None, int | None] | None = None
        self._apply_fonts()

    def _on_link(self, url):
        scheme = url.scheme()
        hid_str = url.path() or url.host() or ""
        # QUrl parses "keep:3" as scheme="keep", path="3"
        if not hid_str:
            full = url.toString()
            if ":" in full:
                hid_str = full.split(":", 1)[1]
        try:
            hid = int(hid_str)
        except (ValueError, TypeError):
            hid = -1

        if scheme == "keep":
            self.hunk_keep.emit(hid)
        elif scheme == "discard":
            self.hunk_discard.emit(hid)
        elif scheme == "keep_all":
            self.hunk_keep_all.emit()
        elif scheme == "discard_all":
            self.hunk_discard_all.emit()

    def _diff_stylesheet(self) -> str:
        return f"""
            QTextBrowser {{
                background: {PANEL_BG};
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 6px;
                font-family: "{mono_font_family()}", Consolas, monospace;
                font-size: {px(_BASE_CODE_PX)};
                font-weight: 400;
            }}
            """

    def _apply_fonts(self) -> None:
        self.head.setFont(ui_font(pt(_BASE_HEAD_PT), QFont.DemiBold))
        self.diff_view.setFont(mono_font(pt(_BASE_MONO_PT)))
        self.diff_view.setStyleSheet(self._diff_stylesheet())

    def apply_font_zoom(self) -> None:
        self._apply_fonts()
        if self._last is not None:
            before, after, hunks, focus = self._last
            self.diff_view.setHtml(
                build_inline_diff_html(before, after, hunks, focus)
            )

    def clear(self):
        self._last = None
        self.diff_view.clear()

    def set_diff(
        self,
        before: str,
        after: str,
        hunks: list[dict] | None = None,
        focus_hunk_id: int | None = None,
    ):
        self._last = (before, after, hunks, focus_hunk_id)
        self.diff_view.setHtml(
            build_inline_diff_html(before, after, hunks, focus_hunk_id)
        )
        if focus_hunk_id is not None:
            found = self.diff_view.find(f"改动段 #{int(focus_hunk_id) + 1}")
            if not found:
                self.diff_view.moveCursor(QTextCursor.Start)
        else:
            self.diff_view.moveCursor(QTextCursor.Start)
