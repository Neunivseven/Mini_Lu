"""统一入口：按 active 配置拿到 chat / asr / vision / image。"""
from __future__ import annotations

from dataclasses import dataclass

from agent.providers.base import (
    ASRProvider,
    ChatProvider,
    ImageProvider,
    ProviderNotConfigured,
    VisionProvider,
)
from agent.providers.config import ModelsConfig, load_models_config
from agent.providers.factory import build_asr, build_chat, build_image, build_vision
from agent.providers.stubs import StubASR, StubImage, StubVision


@dataclass
class ModelHub:
    config: ModelsConfig
    chat: ChatProvider
    asr: ASRProvider
    vision: VisionProvider
    image: ImageProvider

    def status(self) -> dict[str, str | None]:
        chat_id = self.config.active_id("chat")
        caps = []
        if chat_id:
            try:
                caps = sorted(self.config.spec(chat_id).capabilities())
            except Exception:
                caps = []
        return {
            "chat": chat_id,
            "asr": self.config.active_id("asr"),
            "vision": self.config.active_id("vision"),
            "image": self.config.active_id("image"),
            "chat_driver": getattr(self.chat, "name", None),
            "chat_capabilities": ",".join(caps) if caps else None,
        }

    def chat_supports(self, capability: str) -> bool:
        """当前 active.chat 是否声明支持某模态（如 image）。"""
        chat_id = self.config.active_id("chat")
        if not chat_id:
            return False
        try:
            return self.config.spec(chat_id).supports(capability)
        except Exception:
            return False


_hub: ModelHub | None = None


def get_hub(*, reload: bool = False, config_dir=None) -> ModelHub:
    global _hub
    if _hub is not None and not reload and config_dir is None:
        return _hub
    cfg = load_models_config(config_dir)

    chat_id = cfg.active_id("chat")
    if not chat_id:
        raise ProviderNotConfigured(
            "未指定 active.chat。请在 config/models.yaml 设置 active.chat。"
        )
    chat = build_chat(cfg.spec(chat_id))

    asr_id = cfg.active_id("asr")
    asr: ASRProvider = (
        build_asr(cfg.spec(asr_id))
        if asr_id
        else StubASR(reason="未在 config/models.yaml 的 active.asr 中启用语音识别")
    )

    vision_id = cfg.active_id("vision")
    vision: VisionProvider = (
        build_vision(cfg.spec(vision_id))
        if vision_id
        else StubVision(reason="未在 config/models.yaml 的 active.vision 中启用图像识别")
    )

    image_id = cfg.active_id("image")
    image: ImageProvider = (
        build_image(cfg.spec(image_id))
        if image_id
        else StubImage(reason="未在 config/models.yaml 的 active.image 中启用图像处理")
    )

    hub = ModelHub(config=cfg, chat=chat, asr=asr, vision=vision, image=image)
    if config_dir is None:
        _hub = hub
    return hub


def reset_hub() -> None:
    global _hub
    _hub = None
