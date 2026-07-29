"""内嵌终端会话：pty + pyte VT100 仿真，支持 Tab 补全/方向键/完整交互。"""
from __future__ import annotations

import os
import signal
import struct
import fcntl
import termios
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class TerminalSession(QObject):
    """管理 pty 子进程，向外暴露 raw bytes 输出信号。"""
    output = Signal(bytes)
    started = Signal()
    exited = Signal(int)
    errored = Signal(str)

    def __init__(self, cwd: str = "", shell: str = "", rows: int = 24, cols: int = 80):
        super().__init__()
        self.cwd = str(cwd or "")
        self.shell = str(shell or "")
        self._rows = rows
        self._cols = cols
        self._pid: int | None = None
        self._fd: int | None = None
        self._reader: threading.Thread | None = None
        self._alive = False

    def is_running(self) -> bool:
        return bool(self._alive and self._pid)

    def start(self) -> None:
        if self.is_running():
            return
        try:
            self._start_pty()
        except Exception as e:
            self.errored.emit(str(e))

    def _start_pty(self) -> None:
        cwd = self.cwd or str(Path.cwd())
        cwd = str(Path(cwd).expanduser().resolve())
        shell = self.shell or os.environ.get("SHELL") or "/bin/bash"
        pid, fd = os.forkpty()
        if pid == 0:
            try:
                os.chdir(cwd)
            except Exception:
                pass
            os.environ["TERM"] = "xterm-256color"
            os.environ.pop("COLORTERM", None)
            os.execvp(shell, [shell, "-i"])
            os._exit(127)
        self._pid = pid
        self._fd = fd
        self._alive = True
        self.resize(self._rows, self._cols)
        self.started.emit()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        fd = self._fd
        if fd is None:
            return
        try:
            while self._alive:
                try:
                    buf = os.read(fd, 8192)
                except OSError:
                    break
                if not buf:
                    break
                self.output.emit(buf)
        finally:
            self._alive = False
            code = 0
            if self._pid:
                try:
                    _p, st = os.waitpid(self._pid, 0)
                    if _p == self._pid:
                        if os.WIFEXITED(st):
                            code = os.WEXITSTATUS(st)
                        elif os.WIFSIGNALED(st):
                            code = 128 + os.WTERMSIG(st)
                except Exception:
                    pass
            self.exited.emit(code)

    def write(self, data: bytes) -> None:
        if self._fd is not None and self._alive and data:
            try:
                os.write(self._fd, data)
            except Exception:
                pass

    def resize(self, rows: int, cols: int) -> None:
        self._rows = rows
        self._cols = cols
        if self._fd is not None and self._alive:
            try:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
            except Exception:
                pass

    def terminate(self) -> None:
        self._alive = False
        if self._pid:
            try:
                os.kill(self._pid, signal.SIGTERM)
            except Exception:
                pass

    def close(self) -> None:
        self.terminate()
        if self._fd is not None:
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None
