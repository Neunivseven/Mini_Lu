"""
System Prompt 版本库：版本管理、A/B 分流、反馈、待确认改写候选。

数据：data/prompts.json
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir

# 首次初始化时写入的内置正文（与 pet_agent.DEFAULT 对齐，可由 UI 再改）
_BUILTIN_NAME = "内置默认"


def data_path() -> Path:
    p = app_dir() / "data" / "prompts.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _builtin_text() -> str:
    try:
        from agent.pet_agent import DEFAULT_AGENT_SYSTEM

        return DEFAULT_AGENT_SYSTEM.strip()
    except Exception:
        return "你是 Mini_Lu，桌面 Agent 助手，回答简洁有用。需要时调用工具完成任务。"


def _empty() -> dict[str, Any]:
    vid = uuid.uuid4().hex[:10]
    return {
        "version": 1,
        "ab_enabled": False,
        "ab_ratio_b": 0.5,
        "active_a": vid,
        "active_b": vid,
        "versions": [
            {
                "id": vid,
                "name": _BUILTIN_NAME,
                "text": _builtin_text(),
                "created_at": _now(),
                "note": "首次启动自动导入",
                "source": "builtin",
                "stats": {"up": 0, "down": 0},
            }
        ],
        "feedback": [],
        "candidates": [],
    }


def _load() -> dict[str, Any]:
    path = data_path()
    if not path.exists():
        data = _empty()
        _save(data)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = _empty()
        _save(data)
        return data
    if not isinstance(data, dict):
        data = _empty()
    data.setdefault("ab_enabled", False)
    data.setdefault("ab_ratio_b", 0.5)
    data.setdefault("versions", [])
    data.setdefault("feedback", [])
    data.setdefault("candidates", [])
    if not data["versions"]:
        seeded = _empty()
        data["versions"] = seeded["versions"]
        data["active_a"] = seeded["active_a"]
        data["active_b"] = seeded["active_b"]
        _save(data)
    if not data.get("active_a"):
        data["active_a"] = data["versions"][0]["id"]
    if not data.get("active_b"):
        data["active_b"] = data["active_a"]
    return data


def _save(data: dict[str, Any]) -> None:
    path = data_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_version(data: dict[str, Any], vid: str) -> dict[str, Any] | None:
    for v in data.get("versions") or []:
        if v.get("id") == vid:
            return v
    return None


def list_versions() -> list[dict[str, Any]]:
    data = _load()
    items = [dict(v) for v in data.get("versions") or []]
    items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return items


def get_version(vid: str) -> dict[str, Any] | None:
    v = _find_version(_load(), vid)
    return dict(v) if v else None


def get_settings() -> dict[str, Any]:
    data = _load()
    return {
        "ab_enabled": bool(data.get("ab_enabled")),
        "ab_ratio_b": float(data.get("ab_ratio_b") or 0.5),
        "active_a": data.get("active_a"),
        "active_b": data.get("active_b"),
        "path": str(data_path()),
    }


def set_ab_enabled(on: bool) -> None:
    data = _load()
    data["ab_enabled"] = bool(on)
    _save(data)


def set_ab_ratio_b(ratio: float) -> None:
    data = _load()
    data["ab_ratio_b"] = max(0.0, min(1.0, float(ratio)))
    _save(data)


def set_active_a(vid: str) -> str:
    data = _load()
    if not _find_version(data, vid):
        return f"版本不存在: {vid}"
    data["active_a"] = vid
    if not data.get("ab_enabled"):
        data["active_b"] = vid
    _save(data)
    return f"已激活 A → {vid}"


def set_active_b(vid: str) -> str:
    data = _load()
    if not _find_version(data, vid):
        return f"版本不存在: {vid}"
    data["active_b"] = vid
    data["ab_enabled"] = True
    _save(data)
    return f"已激活 B → {vid}（已开启 A/B）"


def add_version(
    text: str,
    *,
    name: str = "",
    note: str = "",
    source: str = "manual",
    activate: bool = False,
) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("prompt 正文为空")
    data = _load()
    vid = uuid.uuid4().hex[:10]
    item = {
        "id": vid,
        "name": (name or f"版本 {vid}").strip(),
        "text": text,
        "created_at": _now(),
        "note": (note or "").strip(),
        "source": source,
        "stats": {"up": 0, "down": 0},
    }
    data["versions"].append(item)
    if activate:
        data["active_a"] = vid
        if not data.get("ab_enabled"):
            data["active_b"] = vid
    _save(data)
    return dict(item)


def update_version_text(vid: str, text: str, *, name: str | None = None) -> str:
    data = _load()
    v = _find_version(data, vid)
    if not v:
        return f"版本不存在: {vid}"
    text = (text or "").strip()
    if not text:
        return "正文为空"
    v["text"] = text
    if name is not None and str(name).strip():
        v["name"] = str(name).strip()
    v["updated_at"] = _now()
    _save(data)
    return f"已更新版本 {vid}"


def delete_version(vid: str) -> str:
    data = _load()
    versions = data.get("versions") or []
    if len(versions) <= 1:
        return "至少保留一个版本"
    if not any(v.get("id") == vid for v in versions):
        return f"版本不存在: {vid}"
    data["versions"] = [v for v in versions if v.get("id") != vid]
    if data.get("active_a") == vid:
        data["active_a"] = data["versions"][0]["id"]
    if data.get("active_b") == vid:
        data["active_b"] = data["active_a"]
    _save(data)
    return f"已删除版本 {vid}"


def _pick_ab_slot(session_id: str | None, ratio_b: float) -> str:
    """稳定分流：同一 session 始终落在 A 或 B。"""
    sid = (session_id or "default").strip() or "default"
    digest = hashlib.md5(sid.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return "b" if bucket < float(ratio_b) else "a"


def resolve_version_id(session_id: str | None = None) -> str:
    data = _load()
    if not data.get("ab_enabled"):
        return str(data.get("active_a") or data["versions"][0]["id"])
    if session_id is None:
        try:
            from agent.chat_history import get_active_id

            session_id = get_active_id()
        except Exception:
            session_id = "default"
    slot = _pick_ab_slot(session_id, float(data.get("ab_ratio_b") or 0.5))
    vid = data.get("active_b") if slot == "b" else data.get("active_a")
    if not _find_version(data, str(vid or "")):
        vid = data.get("active_a")
    return str(vid or data["versions"][0]["id"])


def resolve_system_prompt(session_id: str | None = None) -> str:
    """供 Agent 每轮注入的当前 system prompt 正文。"""
    data = _load()
    vid = resolve_version_id(session_id)
    v = _find_version(data, vid)
    if v and (v.get("text") or "").strip():
        return str(v["text"]).strip()
    return _builtin_text()


def sync_builtin_prompt(*, activate: bool = True, name: str = "内置默认") -> dict[str, Any]:
    """把代码里的 DEFAULT_AGENT_SYSTEM 同步为新版本（并可激活）。
    解决：Prompt 面板里旧版本仍在用，导致 TSA / run_command 等新工具不被引导。
    """
    text = _builtin_text()
    data = _load()
    # 若已有 source=builtin 且正文相同，仅确保激活
    for v in data.get("versions") or []:
        if v.get("source") == "builtin" and (v.get("text") or "").strip() == text:
            if activate:
                data["active_a"] = v["id"]
                if not data.get("ab_enabled"):
                    data["active_b"] = v["id"]
                _save(data)
            return {"id": v["id"], "name": v.get("name"), "reused": True, "activated": activate}
    return add_version(
        text,
        name=name,
        note="从 pet_agent.DEFAULT_AGENT_SYSTEM 同步",
        source="builtin",
        activate=activate,
    )


def add_feedback(
    *,
    rating: str,
    message_id: str = "",
    session_id: str | None = None,
    user_note: str = "",
    assistant_preview: str = "",
    user_preview: str = "",
    prompt_version_id: str | None = None,
) -> dict[str, Any]:
    rating = "up" if rating == "up" else "down"
    data = _load()
    if session_id is None:
        try:
            from agent.chat_history import get_active_id

            session_id = get_active_id()
        except Exception:
            session_id = ""
    vid = prompt_version_id or resolve_version_id(session_id)
    item = {
        "id": uuid.uuid4().hex[:10],
        "ts": _now(),
        "message_id": message_id or "",
        "session_id": session_id or "",
        "rating": rating,
        "prompt_version_id": vid,
        "user_note": (user_note or "").strip(),
        "assistant_preview": (assistant_preview or "")[:800],
        "user_preview": (user_preview or "")[:400],
    }
    data.setdefault("feedback", []).append(item)
    v = _find_version(data, vid)
    if v:
        stats = v.setdefault("stats", {"up": 0, "down": 0})
        stats[rating] = int(stats.get(rating) or 0) + 1
    _save(data)
    return dict(item)


def list_feedback(
    *,
    rating: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    data = _load()
    items = [dict(x) for x in data.get("feedback") or []]
    if rating in ("up", "down"):
        items = [x for x in items if x.get("rating") == rating]
    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return items[: max(1, min(int(limit), 200))]


def feedback_stats() -> dict[str, int]:
    data = _load()
    up = down = 0
    for x in data.get("feedback") or []:
        if x.get("rating") == "up":
            up += 1
        elif x.get("rating") == "down":
            down += 1
    return {"up": up, "down": down, "total": up + down}


def list_candidates(*, status: str | None = "pending") -> list[dict[str, Any]]:
    data = _load()
    items = [dict(c) for c in data.get("candidates") or []]
    if status:
        items = [c for c in items if c.get("status") == status]
    items.sort(key=lambda x: x.get("ts") or "", reverse=True)
    return items


def add_candidate(
    proposed_text: str,
    *,
    base_version_id: str,
    rationale: str = "",
    feedback_ids: list[str] | None = None,
) -> dict[str, Any]:
    proposed_text = (proposed_text or "").strip()
    if not proposed_text:
        raise ValueError("候选正文为空")
    data = _load()
    item = {
        "id": uuid.uuid4().hex[:10],
        "ts": _now(),
        "base_version_id": base_version_id,
        "proposed_text": proposed_text,
        "rationale": (rationale or "").strip(),
        "status": "pending",
        "feedback_ids": list(feedback_ids or []),
    }
    data.setdefault("candidates", []).append(item)
    _save(data)
    return dict(item)


def accept_candidate(cid: str, *, activate: bool = True) -> str:
    data = _load()
    cand = None
    for c in data.get("candidates") or []:
        if c.get("id") == cid:
            cand = c
            break
    if not cand:
        return f"候选不存在: {cid}"
    if cand.get("status") != "pending":
        return f"候选已处理（{cand.get('status')}）"
    ver = add_version(
        cand.get("proposed_text") or "",
        name=f"反馈改写 {cid}",
        note=(cand.get("rationale") or "由差评反馈生成")[:200],
        source="rewrite",
        activate=activate,
    )
    # add_version 已 save；重新标记 candidate
    data = _load()
    for c in data.get("candidates") or []:
        if c.get("id") == cid:
            c["status"] = "accepted"
            c["accepted_version_id"] = ver["id"]
            c["closed_at"] = _now()
            break
    _save(data)
    return f"已采纳为版本 {ver['id']}" + (" 并激活为 A" if activate else "")


def reject_candidate(cid: str) -> str:
    data = _load()
    for c in data.get("candidates") or []:
        if c.get("id") == cid:
            if c.get("status") != "pending":
                return f"候选已处理（{c.get('status')}）"
            c["status"] = "rejected"
            c["closed_at"] = _now()
            _save(data)
            return f"已拒绝候选 {cid}"
    return f"候选不存在: {cid}"


def get_candidate(cid: str) -> dict[str, Any] | None:
    for c in _load().get("candidates") or []:
        if c.get("id") == cid:
            return dict(c)
    return None
