"""未接入厂商时的占位实现：接口就绪，调用时给出明确提示。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.providers.base import (
    ASRProvider,
    ImageProvider,
    ProviderNotConfigured,
    VisionProvider,
)
from agent.providers.config import ProviderSpec


class StubASR(ASRProvider):
    def __init__(self, spec: ProviderSpec | None = None, reason: str = ""):
        self.spec = spec
        self.name = spec.id if spec else "asr_stub"
        self._reason = reason or "语音识别 provider 尚未实现或未在 active.asr 中启用"

    def transcribe(self, audio_path: Path | str, *, language: str | None = None, **kwargs: Any) -> str:
        raise ProviderNotConfigured(
            f"{self._reason}。配置示例见 config/models.yaml（active.asr / providers）。"
        )


class StubVision(VisionProvider):
    def __init__(self, spec: ProviderSpec | None = None, reason: str = ""):
        self.spec = spec
        self.name = spec.id if spec else "vision_stub"
        self._reason = reason or "图像识别 provider 尚未实现或未在 active.vision 中启用"

    def describe(
        self,
        image_path: Path | str,
        *,
        prompt: str = "请描述这张图片的主要内容。",
        **kwargs: Any,
    ) -> str:
        raise ProviderNotConfigured(
            f"{self._reason}。配置示例见 config/models.yaml（active.vision / providers）。"
        )


class StubImage(ImageProvider):
    def __init__(self, spec: ProviderSpec | None = None, reason: str = ""):
        self.spec = spec
        self.name = spec.id if spec else "image_stub"
        self._reason = reason or "图像处理 provider 尚未实现或未在 active.image 中启用"

    def process(
        self,
        *,
        task: str,
        image_path: Path | str | None = None,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> Path | bytes:
        raise ProviderNotConfigured(
            f"{self._reason}（task={task}）。配置示例见 config/models.yaml（active.image）。"
        )
