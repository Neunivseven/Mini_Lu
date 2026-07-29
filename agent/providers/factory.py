"""根据 ProviderSpec.driver 构造具体实现。"""
from __future__ import annotations

from agent.providers.base import (
    ASRProvider,
    ChatProvider,
    ImageProvider,
    ProviderError,
    VisionProvider,
)
from agent.providers.config import ProviderSpec
from agent.providers.openai_compat import OpenAICompatChatProvider
from agent.providers.stubs import StubASR, StubImage, StubVision


def build_chat(spec: ProviderSpec) -> ChatProvider:
    driver = (spec.driver or "openai_compat").lower()
    if driver in ("openai_compat", "openai", "deepseek", "compatible", "openai_compatible"):
        return OpenAICompatChatProvider(spec)
    if driver in ("doubao_ark", "doubao", "ark_chat", "volcengine_ark"):
        from agent.providers.doubao_ark import DoubaoArkChat

        return DoubaoArkChat(spec)
    raise ProviderError(f"未知 chat driver: {spec.driver}（provider={spec.id}）")


def build_asr(spec: ProviderSpec) -> ASRProvider:
    driver = (spec.driver or "").lower()
    if driver in ("openai_whisper", "whisper"):
        from agent.providers.openai_media import OpenAIWhisperASR

        return OpenAIWhisperASR(spec)
    if driver in ("doubao_ark", "doubao", "ark_asr", "volcengine_ark"):
        from agent.providers.doubao_ark import DoubaoArkASR

        return DoubaoArkASR(spec)
    if driver in ("stub", "placeholder", ""):
        return StubASR(spec, reason=f"[{spec.id}] driver={spec.driver or 'stub'}")
    return StubASR(spec, reason=f"未实现的 asr driver: {spec.driver}")


def build_vision(spec: ProviderSpec) -> VisionProvider:
    driver = (spec.driver or "").lower()
    if driver in ("openai_vision", "openai_compat_vision", "vision"):
        from agent.providers.openai_media import OpenAIVisionProvider

        return OpenAIVisionProvider(spec)
    if driver in ("doubao_ark", "doubao", "ark_vision", "volcengine_ark"):
        from agent.providers.doubao_ark import DoubaoArkVision

        return DoubaoArkVision(spec)
    if driver in ("stub", "placeholder", ""):
        return StubVision(spec, reason=f"[{spec.id}] driver={spec.driver or 'stub'}")
    return StubVision(spec, reason=f"未实现的 vision driver: {spec.driver}")


def build_image(spec: ProviderSpec) -> ImageProvider:
    driver = (spec.driver or "").lower()
    # 预留：日后接 dalle / flux / 本地 sd 等
    if driver in ("stub", "placeholder", "", "openai_image"):
        return StubImage(
            spec,
            reason=f"[{spec.id}] 图像处理 driver「{spec.driver or 'stub'}」尚未接入",
        )
    return StubImage(spec, reason=f"未实现的 image driver: {spec.driver}")
