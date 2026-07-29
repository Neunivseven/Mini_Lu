"""
火山方舟 Doubao（Responses API）多模态 Provider。

官方示例用 client.responses.create + input_image / input_audio。
配置 api_key_env: ARK_API_KEY，base_url: https://ark.cn-beijing.volces.com/api/v3
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise ImportError("请先安装 openai：pip install openai") from e

from agent.providers.base import ASRProvider, ChatProvider, ProviderError, VisionProvider
from agent.providers.config import ProviderSpec

_IMAGE_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}
_AUDIO_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "ogg": "audio/ogg",
    "webm": "audio/webm",
}


def _client_from_spec(spec: ProviderSpec) -> OpenAI:
    api_key = spec.resolve_api_key()
    if not api_key:
        env = spec.get("api_key_env") or "ARK_API_KEY"
        raise ProviderError(
            f"未配置 [{spec.id}] API Key。请设置环境变量 {env}，"
            f"或在 config/models.local.yaml 的 providers.{spec.id}.api_key 填写。"
        )
    base_url = str(
        spec.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"
    ).rstrip("/")
    timeout = float(spec.get("timeout_seconds") or 120)
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)


def _data_url(path: Path, mime_map: dict[str, str], default_mime: str) -> str:
    if not path.is_file():
        raise ProviderError(f"文件不存在: {path}")
    suffix = path.suffix.lower().lstrip(".")
    mime = mime_map.get(suffix, default_mime)
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def _extract_output_text(resp: Any) -> str:
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    # 兜底：遍历 output 块
    chunks: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for part in getattr(item, "content", None) or []:
            t = getattr(part, "text", None)
            if t:
                chunks.append(str(t))
            elif isinstance(part, dict) and part.get("text"):
                chunks.append(str(part["text"]))
    out = "\n".join(chunks).strip()
    if out:
        return out
    raise ProviderError(f"Responses 返回空文本: {resp!r}")


def responses_create(
    client: OpenAI,
    *,
    model: str,
    content: list[dict[str, Any]],
    **kwargs: Any,
) -> str:
    try:
        resp = client.responses.create(
            model=model,
            input=[{"role": "user", "content": content}],
            **kwargs,
        )
    except Exception as e:
        raise ProviderError(f"方舟 Responses API 调用失败: {e}") from e
    return _extract_output_text(resp)


class DoubaoArkChat(ChatProvider):
    """豆包多模态 Chat：优先 Chat Completions（可带 image_url），失败则 Responses API。"""

    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        self._client = _client_from_spec(spec)
        self._model = str(spec.get("model") or "doubao-seed-2-0-lite-260428")
        self._prefer_completions = bool(spec.get("prefer_chat_completions", True))

    def langchain_kwargs(self) -> dict[str, Any]:
        """供 LangChain ChatOpenAI（走方舟兼容 Chat Completions）。"""
        return {
            "model": self._model,
            "api_key": self.spec.resolve_api_key(),
            "base_url": str(
                self.spec.get("base_url") or "https://ark.cn-beijing.volces.com/api/v3"
            ).rstrip("/"),
            "timeout": float(self.spec.get("timeout_seconds") or 120),
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        # 规范化 content：允许 list parts
        norm = []
        for m in messages:
            role = m.get("role") or "user"
            content = m.get("content")
            norm.append({"role": role, "content": content})

        if self._prefer_completions:
            try:
                return self._chat_completions(
                    norm, temperature=temperature, max_tokens=max_tokens, **kwargs
                )
            except Exception:
                pass
        return self._chat_responses(norm, **kwargs)

    def _chat_completions(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        body.update(kwargs)
        try:
            resp = self._client.chat.completions.create(**body)
        except Exception as e:
            raise ProviderError(f"[{self.name}] Chat Completions 失败: {e}") from e
        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ProviderError(f"[{self.name}] 模型返回空内容")
        return content

    def _chat_responses(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        # 合并为单轮 user content（Responses 简化路径）
        parts: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role") or "user"
            content = m.get("content")
            prefix = ""
            if role == "system":
                prefix = "[系统] "
            elif role == "assistant":
                prefix = "[助手] "
            if isinstance(content, str):
                if content.strip():
                    parts.append({"type": "input_text", "text": prefix + content})
            elif isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text":
                        t = str(block.get("text") or "")
                        if t:
                            parts.append({"type": "input_text", "text": prefix + t})
                    elif btype == "image_url":
                        url = block.get("image_url")
                        if isinstance(url, dict):
                            url = url.get("url")
                        if url:
                            parts.append({"type": "input_image", "image_url": str(url)})
        if not parts:
            raise ProviderError(f"[{self.name}] 空消息")
        try:
            return responses_create(
                self._client, model=self._model, content=parts, **kwargs
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"[{self.name}] Responses 调用失败: {e}") from e


class DoubaoArkVision(VisionProvider):
    """豆包 Seed：看图问答（input_image）。"""

    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        self._client = _client_from_spec(spec)
        self._model = str(spec.get("model") or "doubao-seed-2-0-lite-260428")

    def describe(
        self,
        image_path: Path | str,
        *,
        prompt: str = "请描述这张图片的主要内容。",
        **kwargs: Any,
    ) -> str:
        src = str(image_path).strip()
        if src.startswith(("http://", "https://", "data:")):
            image_url = src
        else:
            image_url = _data_url(Path(src), _IMAGE_MIME, "image/png")
        content = [
            {"type": "input_image", "image_url": image_url},
            {"type": "input_text", "text": prompt},
        ]
        try:
            return responses_create(
                self._client, model=self._model, content=content, **kwargs
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"[{self.name}] 图像识别失败: {e}") from e


class DoubaoArkASR(ASRProvider):
    """豆包 Seed：音频理解 / 转写（input_audio + 提示词）。"""

    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        self._client = _client_from_spec(spec)
        self._model = str(spec.get("model") or "doubao-seed-2-0-lite-260428")
        self._prompt = str(
            spec.get("transcribe_prompt")
            or "请把这段音频准确转写成文字。只输出识别文本，不要解释。"
        )

    def transcribe(
        self,
        audio_path: Path | str,
        *,
        language: str | None = None,
        **kwargs: Any,
    ) -> str:
        src = str(audio_path).strip()
        if src.startswith(("http://", "https://", "data:")):
            audio_url = src
        else:
            audio_url = _data_url(Path(src), _AUDIO_MIME, "audio/wav")
        prompt = self._prompt
        if language:
            prompt = f"{prompt}（语言：{language}）"
        content = [
            {"type": "input_audio", "audio_url": audio_url},
            {"type": "input_text", "text": prompt},
        ]
        try:
            return responses_create(
                self._client, model=self._model, content=content, **kwargs
            )
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"[{self.name}] 语音识别失败: {e}") from e
