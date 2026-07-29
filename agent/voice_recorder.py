"""Windows 本地麦克风录音（MCI，无额外依赖）→ WAV。"""
from __future__ import annotations

import ctypes
import tempfile
import time
import uuid
from pathlib import Path


class VoiceRecorderError(RuntimeError):
    pass


class WindowsMciRecorder:
    """按开始/停止录制到临时 wav。仅 Windows。"""

    def __init__(self):
        self._alias = f"rec_{uuid.uuid4().hex[:8]}"
        self._recording = False
        self._winmm = ctypes.windll.winmm

    @property
    def is_recording(self) -> bool:
        return self._recording

    def _mci(self, cmd: str) -> None:
        buf = ctypes.create_unicode_buffer(256)
        rc = self._winmm.mciSendStringW(cmd, buf, 255, 0)
        if rc != 0:
            err = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(rc, err, 255)
            raise VoiceRecorderError(err.value or f"MCI error {rc}: {cmd}")

    def start(self) -> None:
        if self._recording:
            return
        try:
            self._mci(f"open new type waveaudio alias {self._alias}")
            self._mci(f"record {self._alias}")
        except VoiceRecorderError:
            try:
                self._mci(f"close {self._alias}")
            except Exception:
                pass
            raise
        self._recording = True

    def stop(self, out_path: Path | None = None) -> Path:
        if not self._recording:
            raise VoiceRecorderError("当前没有在录音")
        path = out_path or (
            Path(tempfile.gettempdir())
            / f"desktop_pet_asr_{int(time.time())}_{uuid.uuid4().hex[:6]}.wav"
        )
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._mci(f"stop {self._alias}")
            # 路径含空格时需引号
            self._mci(f'save {self._alias} "{path}"')
        finally:
            try:
                self._mci(f"close {self._alias}")
            except Exception:
                pass
            self._recording = False
            self._alias = f"rec_{uuid.uuid4().hex[:8]}"
        if not path.is_file() or path.stat().st_size < 44:
            raise VoiceRecorderError("录音文件无效（可能未授权麦克风）")
        return path

    def cancel(self) -> None:
        if not self._recording:
            return
        try:
            self._mci(f"stop {self._alias}")
        except Exception:
            pass
        try:
            self._mci(f"close {self._alias}")
        except Exception:
            pass
        self._recording = False
        self._alias = f"rec_{uuid.uuid4().hex[:8]}"
