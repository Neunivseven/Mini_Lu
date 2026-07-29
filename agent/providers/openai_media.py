"""
OpenAI 官方 Whisper / Vision 实现（需配置 OPENAI_API_KEY 等）。

其他厂商可另写 driver，在 factory 中注册即可。
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise ImportError("请先安装 openai：pip install openai") from e

from agent.providers.base import ASRProvider, ProviderError, VisionProvider
from agent.providers.config import ProviderSpec


def _client_from_spec(spec: ProviderSpec) -> OpenAI:
    api_key = spec.resolve_api_key()
    if not api_key:
        env = spec.get("api_key_env") or "OPENAI_API_KEY"
        raise ProviderError(
            f"未配置 [{spec.id}] API Key。请设置 {env} 或在 models.local.yaml 填写。"
        )
    base_url = str(spec.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    timeout = float(spec.get("timeout_seconds") or 120)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


class OpenAIWhisperASR(ASRProvider):
    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        self._client = _client_from_spec(spec)
        self._model = str(spec.get("model") or "whisper-1")

    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        **kwargs: Any,
    ) -> str:
        path = Path(audio_path)
        if not path.is_file():
            raise ProviderError(f"音频文件不存在: {path}")
        try:
            with path.open("rb") as f:
                kw: dict[str, Any] = {"model": self._model, "file": f, **kwargs}
                if language:
                    kw["language"] = language
                resp = self._client.audio.transcriptions.create(**kw)
        except Exception as e:
            raise ProviderError(f"[{self.name}] 语音识别失败: {e}") from e
        text = getattr(resp, "text", None) or str(resp)
        return str(text).strip()


class OpenAIVisionProvider(VisionProvider):
    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        self._client = _client_from_spec(spec)
        self._model = str(spec.get("model") or "gpt-4o")

    def describe(
        self,
        image_path: Path | str,
        *,
        prompt: str = "请描述这张图片的主要内容。",
        **kwargs: Any,
    ) -> str:
        path = Path(image_path)
        if not path.is_file():
            raise ProviderError(f"图片不存在: {path}")
        suffix = path.suffix.lower().lstrip(".") or "png"
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
        }.get(suffix, "image/png")
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        data_url = f"data:{mime};base64,{b64}"
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                **kwargs,
            )
        except Exception as e:
            raise ProviderError(f"[{self.name}] 图像识别失败: {e}") from e
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ProviderError(f"[{self.name}] 模型返回空内容")
        return content
