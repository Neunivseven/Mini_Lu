"""多模态模型 Provider 抽象接口。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ProviderError(RuntimeError):
    """Provider 调用或配置错误。"""


class ProviderNotConfigured(ProviderError):
    """当前未启用或未实现该能力。"""


class ChatProvider(ABC):
    """文本对话 / Agent 用语言模型。"""

    name: str = "chat"

    @abstractmethod
    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        """messages: OpenAI 风格 [{role, content}, ...]。"""

    def chat_text(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        max_tokens: int = 400,
    ) -> str:
        return self.chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
        )


class ASRProvider(ABC):
    """语音识别（Speech-to-Text）。"""

    name: str = "asr"

    @abstractmethod
    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        **kwargs: Any,
    ) -> str:
        """返回识别文本。"""


class VisionProvider(ABC):
    """图像理解 / 识别（看图问答）。"""

    name: str = "vision"

    @abstractmethod
    def describe(
        self,
        image_path: Path | str,
        *,
        prompt: str = "请描述这张图片的主要内容。",
        **kwargs: Any,
    ) -> str:
        """返回对图像的文字描述或问答结果。"""


class ImageProvider(ABC):
    """图像处理 / 生成（编辑、抠图、超分、文生图等）。"""

    name: str = "image"

    @abstractmethod
    def process(
        self,
        *,
        task: str,
        image_path: Path | str | None = None,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> Path | bytes:
        """
        task 示例：generate / edit / remove_bg / upscale
        返回输出文件路径或原始字节。
        """
