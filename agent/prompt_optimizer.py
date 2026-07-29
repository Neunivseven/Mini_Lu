"""
根据差评反馈，用当前 Chat 模型生成 system prompt 改写候选（不自动上线）。
"""
from __future__ import annotations

from typing import Any

from agent import prompt_store


_REWRITE_INSTRUCTION = """你是 Prompt 工程师。根据用户对 Mini_Lu Agent 回复的差评，改写 system prompt。

要求：
1. 保留原 prompt 中关于工具用法、安全边界、中文回复等关键能力说明，不要删成空壳。
2. 针对差评中的问题做具体修正（例如：记事与闹钟混淆、乱改文件、幻觉等）。
3. 只输出改写后的完整 system prompt 正文，不要 Markdown 标题、不要解释、不要代码围栏。
"""


def propose_rewrite_from_feedback(*, max_items: int = 8) -> dict[str, Any]:
    """
    读取近期差评 → 调用 LLM → 写入 pending candidate。
    返回 candidate dict；失败抛异常。
    """
    downs = prompt_store.list_feedback(rating="down", limit=max_items)
    if not downs:
        raise ValueError("暂无差评反馈，请先在工作台对回复点踩并可选填写原因。")

    settings = prompt_store.get_settings()
    base_id = str(settings.get("active_a") or "")
    base = prompt_store.get_version(base_id)
    if not base:
        versions = prompt_store.list_versions()
        if not versions:
            raise ValueError("没有可用的 prompt 版本")
        base = versions[0]
        base_id = base["id"]

    fb_lines = []
    fb_ids = []
    for i, f in enumerate(downs, 1):
        fb_ids.append(f.get("id") or "")
        note = f.get("user_note") or "（未写原因）"
        fb_lines.append(
            f"[{i}] 原因: {note}\n"
            f"    用户: {(f.get('user_preview') or '（无）')[:200]}\n"
            f"    助手: {(f.get('assistant_preview') or '（无）')[:300]}"
        )

    user_msg = (
        "【当前 system prompt】\n"
        f"{base.get('text') or ''}\n\n"
        "【差评反馈】\n"
        + "\n\n".join(fb_lines)
        + "\n\n请输出改写后的完整 system prompt："
    )

    from agent.llm_client import load_llm_config
    from agent.pet_agent import build_chat_model

    cfg = load_llm_config()
    model = build_chat_model(cfg)
    resp = model.invoke(
        [
            {"role": "system", "content": _REWRITE_INSTRUCTION},
            {"role": "user", "content": user_msg},
        ]
    )
    content = getattr(resp, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text") or ""))
        text = "\n".join(parts).strip()
    else:
        text = str(content or "").strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text or len(text) < 40:
        raise ValueError("模型返回的改写过短或为空，请重试。")

    rationale = f"基于 {len(downs)} 条差评自动生成，请人工审阅后再采纳。"
    return prompt_store.add_candidate(
        text,
        base_version_id=base_id,
        rationale=rationale,
        feedback_ids=[x for x in fb_ids if x],
    )
