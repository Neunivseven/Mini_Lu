"""工作台内嵌终端：基于 termqt 库的完整终端仿真，支持 Tab 补全、方向键、光标。"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QPushButton,
    QScrollBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from termqt import Terminal
from termqt.terminal_io_posix import TerminalPOSIXIO

from agent.ui_dialogs import warn
from agent.ui_fonts import mono_font_family


class _ExecIO(TerminalPOSIXIO):
    """TerminalPOSIXExecIO 的修正版：不强制覆盖 LANG/LC_CTYPE。"""

    def __init__(self, cols, rows, cmd, env=None, logger=None):
        super().__init__(cols, rows, logger)
        self.cmd = cmd
        self.env = env if env else dict(os.environ)

    def run_slave(self):
        import shlex
        cmd = shlex.split(self.cmd)
        env = self.env
        env["COLUMNS"] = str(self.cols)
        env["LINES"] = str(self.rows)
        env.setdefault("TERM", "xterm-256color")
        env.setdefault("PYTHONIOENCODING", "utf_8")
        os.execvpe(cmd[0], cmd, env)


class _Terminal(Terminal):
    """扩展 termqt.Terminal：Ctrl+Shift+C 复制、Ctrl+Shift+V 粘贴；Tab 不跳焦点。"""

    def focusNextPrevChild(self, _next: bool) -> bool:
        return False

    def event(self, ev):
        from PySide6.QtCore import QEvent
        if ev.type() == QEvent.ShortcutOverride:
            ev.accept()
            return True
        return super().event(ev)

    def keyPressEvent(self, event):
        mods = event.modifiers()
        key = event.key()
        has_ctrl = mods & Qt.ControlModifier
        has_shift = mods & Qt.ShiftModifier

        # Ctrl+Shift+C/V：复制粘贴（必须在 super 之前，因为 super 会 reset_selection）
        if has_ctrl and has_shift:
            if key == Qt.Key_C:
                self._copy_selection()
                event.accept()
                return
            if key == Qt.Key_V:
                text = QApplication.clipboard().text()
                if text:
                    payload = text.replace("\r\n", "\n").replace("\n", "\r")
                    self.input(payload.encode("utf-8"))
                event.accept()
                return

        # 纯修饰键（Ctrl、Shift 等）按下时不要传给 super，避免 reset_selection 清掉选区
        if key in (Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta):
            event.accept()
            return

        super().keyPressEvent(event)

    def wheelEvent(self, event):
        if self.scroll_bar:
            delta = event.angleDelta().y()
            steps = -delta // 40
            pos = self.scroll_bar.sliderPosition()
            self.scroll_bar.setSliderPosition(pos + steps)
            event.accept()
        else:
            super().wheelEvent(event)


class _TerminalTab(QWidget):
    """单个终端标签页：termqt Terminal + POSIX IO。"""
    title_changed = Signal(str)

    def __init__(self, title: str, cwd: str = "", parent=None):
        super().__init__(parent)
        self._title = title
        self._cwd = str(cwd or str(Path.cwd()))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # termqt Terminal widget
        _font = QFont(mono_font_family())
        _font.setStyleHint(QFont.Monospace)
        self.term = _Terminal(
            600, 400,
            font_size=11,
            font=_font,
        )
        self.term.setFocusPolicy(Qt.StrongFocus)

        # 滚动条
        term_row = QHBoxLayout()
        term_row.setContentsMargins(0, 0, 0, 0)
        term_row.setSpacing(0)
        term_row.addWidget(self.term, 1)
        self.scroll_bar = QScrollBar(Qt.Vertical, self)
        self.scroll_bar.setStyleSheet("""
            QScrollBar:vertical {
                background: #1A1E2E;
                width: 10px;
                border: none;
            }
            QScrollBar::handle:vertical {
                background: #3A4A60;
                border-radius: 4px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: #4A6080;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)
        self.term.connect_scroll_bar(self.scroll_bar)
        term_row.addWidget(self.scroll_bar)
        lay.addLayout(term_row)

        # POSIX IO（pty 子进程）
        shell = os.environ.get("SHELL", "/bin/bash")
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        env.pop("COLORTERM", None)
        # 保留系统原始 locale，避免 manpath 等工具报错
        for k in ("LANG", "LC_CTYPE", "LC_ALL"):
            if k in os.environ:
                env[k] = os.environ[k]

        self.io = _ExecIO(
            80, 24,
            cmd=shell,
            env=env,
        )

        # 连接 IO ↔ Terminal
        self.io.stdout_callback = self.term.stdout
        self.term.stdin_callback = self.io.write
        self.io.terminated_callback = self._on_terminated

    def start(self) -> None:
        # 切换工作目录后再 spawn
        try:
            os.chdir(self._cwd)
        except Exception:
            pass
        self.io.spawn()
        self.term.setFocus()

    def close(self) -> None:
        try:
            self.io.terminate()
        except Exception:
            pass

    def _on_terminated(self) -> None:
        self.term.stdout(b"\r\n[terminal] \xe8\xbf\x9b\xe7\xa8\x8b\xe5\xb7\xb2\xe9\x80\x80\xe5\x87\xba\r\n")

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.term.char_width and self.term.char_height:
            w = self.term.width()
            h = self.term.height()
            self.term.resize(w, h)
            cols = max(40, w // self.term.char_width)
            rows = max(8, h // self.term.char_height)
            try:
                self.io.resize(rows, cols)
            except Exception:
                pass


class TerminalPanel(QWidget):
    """多标签终端面板（带顶部导航栏）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── 顶部导航栏 ──
        nav = QWidget(self)
        nav.setObjectName("termNav")
        nav.setStyleSheet("""
            #termNav {
                background: #E8EEF5;
                border-bottom: 1px solid #C5D0DC;
                padding: 3px 8px;
            }
            #termNav QLabel {
                color: #64748B;
                font-weight: 600;
                font-size: 11px;
            }
            #termNav QPushButton {
                background: transparent;
                color: #64748B;
                border: 1px solid #C5D0DC;
                border-radius: 4px;
                padding: 2px 10px;
                font-size: 10px;
                font-weight: 500;
            }
            #termNav QPushButton:hover {
                background: #F0F4F8;
                color: #1E293B;
                border-color: #94A3B8;
            }
            #termNav QPushButton:pressed {
                background: #DDE4ED;
            }
            #termNav QPushButton[objectName="navDanger"]:hover {
                background: #FEE2E2;
                border-color: #F87171;
                color: #DC2626;
            }
        """)
        nlay = QHBoxLayout(nav)
        nlay.setContentsMargins(6, 3, 6, 3)
        nlay.setSpacing(8)

        icon_label = QLabel("\u2b24", nav)
        icon_label.setStyleSheet("color: #56B6C2; font-size: 8px;")
        nlay.addWidget(icon_label)
        title_label = QLabel("\u7ec8\u7aef", nav)
        nlay.addWidget(title_label)
        nlay.addStretch(1)

        self.btn_clear = QPushButton("\u6e05\u5c4f", nav)
        self.btn_clear.clicked.connect(self._clear_current)
        nlay.addWidget(self.btn_clear)

        self.btn_rename = QPushButton("\u91cd\u547d\u540d", nav)
        self.btn_rename.clicked.connect(self._rename_current)
        nlay.addWidget(self.btn_rename)

        self.btn_new = QPushButton("\uff0b \u65b0\u5efa", nav)
        self.btn_new.clicked.connect(self.new_terminal)
        nlay.addWidget(self.btn_new)

        self.btn_close = QPushButton("\u2715 \u5173\u95ed", nav)
        self.btn_close.setObjectName("navDanger")
        self.btn_close.clicked.connect(self.close_current_terminal)
        nlay.addWidget(self.btn_close)

        lay.addWidget(nav)

        # ── 标签栏 + 终端区 ──
        self.tabs = QTabWidget(self)
        self.tabs.setTabsClosable(False)
        self.tabs.setDocumentMode(True)
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background: #1A1E2E;
            }
            QTabBar::tab {
                background: #E8EEF5;
                color: #64748B;
                border: none;
                border-right: 1px solid #C5D0DC;
                padding: 5px 16px;
                font-size: 10px;
                font-weight: 500;
                min-width: 60px;
            }
            QTabBar::tab:selected {
                background: #FFFFFF;
                color: #1E293B;
                border-bottom: 2px solid #3D7EA6;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected {
                background: #F0F4F8;
                color: #334155;
            }
        """)
        lay.addWidget(self.tabs, 1)

    def new_terminal(self, cwd: str = "") -> None:
        if not cwd:
            try:
                from agent.file_workspace import get_active_root
                r = get_active_root()
                if r:
                    cwd = str(r)
            except Exception:
                pass
        idx = self.tabs.count() + 1
        title = f"bash-{idx}"
        tab = _TerminalTab(title=title, cwd=cwd, parent=self)
        tab.title_changed.connect(lambda t, w=tab: self._on_tab_title_change(w, t))
        self.tabs.addTab(tab, title)
        self.tabs.setCurrentWidget(tab)
        tab.start()

    def close_current_terminal(self) -> None:
        w = self.tabs.currentWidget()
        if w is None:
            return
        try:
            w.close()
        except Exception:
            pass
        self.tabs.removeTab(self.tabs.currentIndex())

    def _clear_current(self) -> None:
        w = self.tabs.currentWidget()
        if isinstance(w, _TerminalTab):
            w.term.erase_display(2)
            w.term.set_cursor_position(0, 0)
            w.io.write(b"\x0c")

    def _rename_current(self) -> None:
        w = self.tabs.currentWidget()
        if not isinstance(w, _TerminalTab):
            return
        text, ok = QInputDialog.getText(
            self, "\u91cd\u547d\u540d\u7ec8\u7aef", "\u6807\u7b7e\u540d\uff1a", text=w._title
        )
        if ok and (text or "").strip():
            w._title = text.strip()
            self._on_tab_title_change(w, w._title)

    def _on_tab_title_change(self, tab: _TerminalTab, title: str) -> None:
        i = self.tabs.indexOf(tab)
        if i >= 0:
            self.tabs.setTabText(i, title)

    def open_in_new_tab(self, cwd: str = "") -> None:
        self.new_terminal(cwd=cwd)

    def closeEvent(self, event):
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            try:
                if isinstance(w, _TerminalTab):
                    w.close()
            except Exception:
                pass
        super().closeEvent(event)
