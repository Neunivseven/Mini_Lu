"""媒体网关：按 Chat 能力在「原生多模态」与「vision→文本降级」之间分流。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from agent.file_extract import build_agent_prompt
from agent.providers.message_protocol import (
    ContentPart,
    media_has_images,
    parts_to_langchain_content,
)


Mode = Literal["native", "fallback", "text"]


@dataclass
class ResolvedTurn:
    mode: Mode
    """发给 Agent / 历史的文本摘要（始终有字符串，便于 pending/重试）。"""
    text_prompt: str
    """LangChain user content：str 或多模态 parts 列表。"""
    user_content: Any
    media_items: list[dict] = field(default_factory=list)
    doc_paths: list[str] = field(default_factory=list)


def _doc_text_block(doc_paths: list[str]) -> str:
    if not doc_paths:
        return ""
    from agent.file_extract import extract_text

    chunks: list[str] = []
    for fp in doc_paths:
        p = Path(fp)
        try:
            body = extract_text(p)
            chunks.append(f"--- 文档: {p.name} ---\n{body}\n---")
        except Exception as e:
            chunks.append(f"--- 文档: {p.name} ---\n(提取失败: {e})\n---")
    return "\n".join(chunks)


def build_native_parts(
    user_text: str,
    *,
    doc_paths: list[str] | None = None,
    media_items: list[dict] | None = None,
) -> list[ContentPart]:
    """构造原生多模态 parts：文本（含文档抽取）+ 图片。"""
    user_text = (user_text or "").strip()
    doc_paths = list(doc_paths or [])
    media_items = list(media_items or [])
    text_bits: list[str] = []
    if user_text:
        text_bits.append(user_text)
    else:
        text_bits.append("请结合下方附件给出简洁有用的回答。")

    doc_block = _doc_text_block(doc_paths)
    if doc_block:
        text_bits.append("\n【附件文档】\n" + doc_block)

    # 音频仍用 analysis 文字（一期不做原生音频进 Chat）
    for m in media_items:
        kind = str(m.get("kind") or "")
        if kind != "audio":
            continue
        name = str(m.get("name") or Path(str(m.get("path") or "")).name or "音频")
        analysis = (m.get("analysis") or "").strip()
        if analysis:
            text_bits.append(f"\n--- 语音识别: {name} ---\n{analysis}\n---")
        else:
            text_bits.append(f"\n--- 语音识别: {name} ---\n(无识别文本)\n---")

    images = [
        m
        for m in media_items
        if str(m.get("kind") or "") == "image" and m.get("path")
    ]
    if images:
        names = ", ".join(
            str(m.get("name") or Path(str(m.get("path"))).name) for m in images
        )
        text_bits.append(f"\n（用户附带图片：{names}，请直接理解图片内容。）")

    parts: list[ContentPart] = [
        ContentPart(type="text", text="\n".join(text_bits).strip())
    ]
    for m in images:
        parts.append(
            ContentPart(
                type="image",
                path=str(m.get("path") or ""),
                url="",
            )
        )
    return parts


def resolve_turn(
    user_text: str,
    *,
    doc_paths: list[str] | None = None,
    media_items: list[dict] | None = None,
    hub=None,
) -> ResolvedTurn:
    """
    分流：
    - 有图且 active.chat 支持 image → native（不跑独立 vision）
    - 否则 → fallback（build_agent_prompt，依赖 analysis）
    """
    doc_paths = list(doc_paths or [])
    media_items = list(media_items or [])

    if hub is None:
        try:
            from agent.providers.hub import get_hub

            hub = get_hub()
        except Exception:
            hub = None

    has_img = media_has_images(media_items)
    native_ok = bool(hub and has_img and hub.chat_supports("image"))

    if native_ok:
        parts = build_native_parts(
            user_text, doc_paths=doc_paths, media_items=media_items
        )
        content = parts_to_langchain_content(parts)
        # 文本摘要供历史 / pending
        text_prompt = parts[0].text if parts and parts[0].type == "text" else (
            user_text or "（多模态附件）"
        )
        return ResolvedTurn(
            mode="native",
            text_prompt=text_prompt,
            user_content=content,
            media_items=media_items,
            doc_paths=doc_paths,
        )

    prompt = build_agent_prompt(user_text, doc_paths, media_items=media_items)
    mode: Mode = "fallback" if has_img else "text"
    return ResolvedTurn(
        mode=mode,
        text_prompt=prompt,
        user_content=prompt,
        media_items=media_items,
        doc_paths=doc_paths,
    )
