"""
Goal 跨轮驱动（按对话 session 隔离）。

对齐 CCB goal 精简版；切换 New Agent 会话后 Goal 互不干扰。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent.llm_client import data_dir

MAX_GOAL_TURNS = 30
BLOCKED_THRESHOLD = 3


def _path():
    p = data_dir() / "goal.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _session_id() -> str:
    try:
        from agent.chat_history import get_active_id

        return get_active_id() or "_default"
    except Exception:
        return "_default"


def _empty() -> dict[str, Any]:
    return {"version": 2, "by_session": {}}


def _load() -> dict[str, Any]:
    path = _path()
    if not path.exists():
        return _empty()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    # 迁移旧单 goal
    if raw.get("version") == 2 and isinstance(raw.get("by_session"), dict):
        return raw
    goal = raw.get("goal")
    data = _empty()
    if isinstance(goal, dict):
        data["by_session"]["_legacy"] = goal
        # 尽量挂到当前会话
        sid = _session_id()
        data["by_session"][sid] = goal
    _save(data)
    return data


def _save(data: dict[str, Any]) -> None:
    data = {"version": 2, "by_session": dict(data.get("by_session") or {})}
    _path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _put(goal: dict[str, Any] | None, *, session_id: str | None = None) -> None:
    sid = session_id or _session_id()
    data = _load()
    if goal is None:
        data["by_session"].pop(sid, None)
    else:
        data["by_session"][sid] = goal
    _save(data)


def get_goal(*, session_id: str | None = None) -> dict[str, Any] | None:
    sid = session_id or _session_id()
    g = _load().get("by_session", {}).get(sid)
    return dict(g) if isinstance(g, dict) else None


def set_goal(
    objective: str,
    *,
    max_turns: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    objective = (objective or "").strip()
    if not objective:
        raise ValueError("目标内容为空")
    mt = int(max_turns) if max_turns else MAX_GOAL_TURNS
    mt = max(1, min(mt, 100))
    goal = {
        "objective": objective,
        "status": "active",
        "turns_executed": 0,
        "max_turns": mt,
        "blocked_attempts": 0,
        "last_block_reason": "",
        "journal": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    _put(goal, session_id=session_id)
    return goal


def clear_goal(*, session_id: str | None = None) -> bool:
    had = get_goal(session_id=session_id) is not None
    _put(None, session_id=session_id)
    return had


def pause_goal(*, session_id: str | None = None) -> dict[str, Any] | None:
    g = get_goal(session_id=session_id)
    if not g or g.get("status") != "active":
        return None
    g["status"] = "paused"
    g["updated_at"] = _now()
    _put(g, session_id=session_id)
    return g


def resume_goal(*, session_id: str | None = None) -> dict[str, Any] | None:
    g = get_goal(session_id=session_id)
    if not g or g.get("status") != "paused":
        return None
    g["status"] = "active"
    g["blocked_attempts"] = 0
    g["last_block_reason"] = ""
    g["updated_at"] = _now()
    _put(g, session_id=session_id)
    return g


def mark_completed(note: str = "", *, session_id: str | None = None) -> dict[str, Any] | None:
    g = get_goal(session_id=session_id)
    if not g:
        return None
    g["status"] = "completed"
    g["updated_at"] = _now()
    if note:
        g.setdefault("journal", []).append({"ts": _now(), "event": "completed", "note": note})
    _put(g, session_id=session_id)
    return g


def report_blocked(reason: str, *, session_id: str | None = None) -> dict[str, Any] | None:
    g = get_goal(session_id=session_id)
    if not g or g.get("status") != "active":
        return None
    g["blocked_attempts"] = int(g.get("blocked_attempts") or 0) + 1
    g["last_block_reason"] = (reason or "").strip()[:200]
    g["updated_at"] = _now()
    g.setdefault("journal", []).append(
        {"ts": _now(), "event": "blocked", "note": g["last_block_reason"]}
    )
    if g["blocked_attempts"] >= BLOCKED_THRESHOLD:
        g["status"] = "blocked"
    _put(g, session_id=session_id)
    return g


def record_turn(note: str = "", *, session_id: str | None = None) -> dict[str, Any] | None:
    g = get_goal(session_id=session_id)
    if not g or g.get("status") != "active":
        return g
    g["turns_executed"] = int(g.get("turns_executed") or 0) + 1
    g["updated_at"] = _now()
    if note:
        g.setdefault("journal", []).append({"ts": _now(), "event": "turn", "note": note[:120]})
    if len(g.get("journal") or []) > 40:
        g["journal"] = g["journal"][-40:]
    if g["turns_executed"] >= int(g.get("max_turns") or MAX_GOAL_TURNS):
        g["status"] = "max_turns"
    _put(g, session_id=session_id)
    return g


def is_active(*, session_id: str | None = None) -> bool:
    g = get_goal(session_id=session_id)
    return bool(g and g.get("status") == "active")


def format_goal_block(*, session_id: str | None = None) -> str:
    g = get_goal(session_id=session_id)
    if not g:
        return ""
    status = g.get("status") or ""
    if status not in ("active", "paused", "blocked", "max_turns"):
        return ""
    lines = [
        "【当前 Goal】",
        f"状态: {status}",
        f"目标: {g.get('objective', '')}",
        f"进度: 第 {g.get('turns_executed', 0)}/{g.get('max_turns', MAX_GOAL_TURNS)} 轮",
    ]
    if g.get("last_block_reason"):
        lines.append(f"最近受阻: {g['last_block_reason']}")
    if status == "active":
        lines.append(
            "请围绕该目标推进；完成时调用 mark_goal_done；受阻调用 report_goal_blocked；"
            "用户未改目标时不要擅自 clear_goal。"
        )
    elif status == "paused":
        lines.append("目标已暂停，除非用户要求恢复，否则不要继续推进。")
    elif status == "blocked":
        lines.append("目标多次受阻已标记 blocked，先向用户说明障碍并等待指示。")
    elif status == "max_turns":
        lines.append("已达轮次上限，向用户汇报进度并询问是否 resume（需先 clear 或重新 set）。")
    return "\n".join(lines)


def format_status(*, session_id: str | None = None) -> str:
    g = get_goal(session_id=session_id)
    if not g:
        return "当前对话没有 Goal。"
    bits = [
        f"状态: {g.get('status')}",
        f"目标: {g.get('objective')}",
        f"轮次: {g.get('turns_executed')}/{g.get('max_turns')}",
        f"受阻次数: {g.get('blocked_attempts', 0)}",
        f"更新: {g.get('updated_at')}",
    ]
    if g.get("last_block_reason"):
        bits.append(f"最近受阻: {g['last_block_reason']}")
    return "\n".join(bits)
