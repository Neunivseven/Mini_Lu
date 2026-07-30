"""统一多模态消息协议（内部标准，由各 Chat 适配器翻译成具体 API）。"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PartType = Literal["text", "image"]  # video 预留，本期不实现


_IMAGE_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass
class ContentPart:
    type: PartType
    text: str = ""
    path: str = ""  # 本地文件路径（image）
    url: str = ""  # http(s) 或 data: URL

    def is_image(self) -> bool:
        return self.type == "image"


@dataclass
class UnifiedMessage:
    role: str  # system | user | assistant
    parts: list[ContentPart] = field(default_factory=list)

    @classmethod
    def text(cls, role: str, text: str) -> "UnifiedMessage":
        return cls(role=role, parts=[ContentPart(type="text", text=text or "")])


def image_data_url(path: str | Path) -> str:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"图片不存在: {p}")
    mime = _IMAGE_MIME.get(p.suffix.lower(), "image/png")
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


def resolve_image_url(part: ContentPart, *, force_data_url: bool = False) -> str:
    """解析图片为 API 可用 URL。

    force_data_url=True（Kimi 等）：禁止公网 http(s)，仅允许 data: / ms:// / 本地转 base64。
    """
    if part.path:
        src = str(part.path).strip()
        if src.startswith("data:") or src.startswith("ms://"):
            return src
        if src.startswith(("http://", "https://")):
            if force_data_url:
                raise ValueError(
                    "当前模型不支持公网图片 URL，请使用本地文件（将转 base64）或 ms://file-id"
                )
            return src
        return image_data_url(src)
    if part.url:
        url = str(part.url).strip()
        if url.startswith("data:") or url.startswith("ms://"):
            return url
        if url.startswith(("http://", "https://")):
            if force_data_url:
                raise ValueError(
                    "当前模型不支持公网图片 URL，请使用本地文件（将转 base64）或 ms://file-id"
                )
            return url
        # 当作本地路径
        return image_data_url(url)
    raise ValueError("image part 缺少 path/url")


def has_images(messages: list[UnifiedMessage] | list[ContentPart]) -> bool:
    if not messages:
        return False
    if messages and isinstance(messages[0], ContentPart):
        return any(p.is_image() for p in messages)  # type: ignore[arg-type]
    for msg in messages:  # type: ignore[assignment]
        if isinstance(msg, UnifiedMessage) and any(p.is_image() for p in msg.parts):
            return True
    return False


def parts_to_openai_chat_content(
    parts: list[ContentPart],
    *,
    force_image_data_url: bool = False,
) -> str | list[dict[str, Any]]:
    """翻译为 OpenAI Chat Completions content（str 或 parts 列表）。"""
    if not parts:
        return ""
    if len(parts) == 1 and parts[0].type == "text":
        return parts[0].text
    out: list[dict[str, Any]] = []
    for p in parts:
        if p.type == "text":
            if p.text:
                out.append({"type": "text", "text": p.text})
        elif p.type == "image":
            try:
                url = resolve_image_url(p, force_data_url=force_image_data_url)
            except Exception as e:
                out.append(
                    {
                        "type": "text",
                        "text": f"（图片无法加载: {p.path or p.url} · {e}）",
                    }
                )
                continue
            out.append(
                {
                    "type": "image_url",
                    "image_url": {"url": url},
                }
            )
    return out or ""


def parts_to_langchain_content(
    parts: list[ContentPart],
    *,
    force_image_data_url: bool = False,
) -> str | list[dict[str, Any]]:
    """LangChain HumanMessage content（与 OpenAI 多模态块兼容）。"""
    if not force_image_data_url:
        try:
            from agent.providers.hub import get_hub
            from agent.providers.sampling import sampling_policy_for

            chat = get_hub().chat
            model = str(getattr(chat, "_model", "") or "")
            if not model:
                spec = getattr(chat, "spec", None)
                if spec is not None:
                    model = str(spec.get("model") or "")
            force_image_data_url = sampling_policy_for(model).force_image_data_url
        except Exception:
            force_image_data_url = False
    return parts_to_openai_chat_content(
        parts, force_image_data_url=force_image_data_url
    )


def parts_to_ark_responses_content(parts: list[ContentPart]) -> list[dict[str, Any]]:
    """翻译为火山方舟 Responses API content 块。"""
    out: list[dict[str, Any]] = []
    for p in parts:
        if p.type == "text" and p.text:
            out.append({"type": "input_text", "text": p.text})
        elif p.type == "image":
            out.append({"type": "input_image", "image_url": resolve_image_url(p)})
    return out


def media_has_images(media_items: list[dict] | None) -> bool:
    return any(
        str(m.get("kind") or "") == "image" and m.get("path")
        for m in (media_items or [])
    )
