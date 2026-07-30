"""多会话对话：每会话独立 transcript。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir

MAX_MESSAGES = 300
MAX_SESSIONS = 40


def history_path() -> Path:
    p = app_dir() / "data" / "chat_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_session(title: str = "新对话") -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex[:10],
        "title": (title or "新对话").strip() or "新对话",
        "created_at": _now(),
        "updated_at": _now(),
        "messages": [],
    }


def _empty() -> dict[str, Any]:
    s = _new_session("默认对话")
    return {"version": 2, "active_id": s["id"], "sessions": [s]}


def _migrate(raw: dict[str, Any]) -> dict[str, Any]:
    """旧版 {messages:[...]} → 多会话。"""
    if raw.get("version") == 2 and isinstance(raw.get("sessions"), list):
        data = {
            "version": 2,
            "active_id": str(raw.get("active_id") or ""),
            "sessions": [],
        }
        for s in raw["sessions"]:
            if not isinstance(s, dict) or not s.get("id"):
                continue
            msgs = s.get("messages") if isinstance(s.get("messages"), list) else []
            data["sessions"].append(
                {
                    "id": str(s["id"]),
                    "title": str(s.get("title") or "对话"),
                    "created_at": str(s.get("created_at") or _now()),
                    "updated_at": str(s.get("updated_at") or _now()),
                    "messages": msgs[-MAX_MESSAGES:],
                }
            )
        if not data["sessions"]:
            return _empty()
        ids = {s["id"] for s in data["sessions"]}
        if data["active_id"] not in ids:
            data["active_id"] = data["sessions"][0]["id"]
        return data

    # 旧单会话
    msgs = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    s = _new_session("默认对话")
    s["messages"] = msgs[-MAX_MESSAGES:]
    if msgs:
        first_user = next((m for m in msgs if m.get("role") == "user"), None)
        if first_user and first_user.get("text"):
            s["title"] = _title_from_text(str(first_user["text"]))
    return {"version": 2, "active_id": s["id"], "sessions": [s]}


def _title_from_text(text: str, limit: int = 20) -> str:
    t = " ".join((text or "").strip().split())
    if not t:
        return "新对话"
    if len(t) > limit:
        return t[: limit - 1] + "…"
    return t


def _load() -> dict[str, Any]:
    path = history_path()
    if not path.exists():
        data = _empty()
        _save(data)
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    data = _migrate(raw)
    # 若刚迁移，落盘
    if raw.get("version") != 2 or "sessions" not in raw:
        _save(data)
    return data


def _save(data: dict[str, Any]) -> None:
    sessions = data.get("sessions") or []
    if len(sessions) > MAX_SESSIONS:
        # 保留 active + 最近更新的
        active = data.get("active_id")
        ordered = sorted(
            sessions,
            key=lambda s: s.get("updated_at") or "",
            reverse=True,
        )
        keep = []
        seen = set()
        for s in ordered:
            if s["id"] == active or len(keep) < MAX_SESSIONS:
                if s["id"] not in seen:
                    keep.append(s)
                    seen.add(s["id"])
            if len(keep) >= MAX_SESSIONS:
                break
        # 确保 active 在
        if active and active not in seen:
            for s in sessions:
                if s["id"] == active:
                    keep[-1] = s
                    break
        data["sessions"] = keep
    for s in data["sessions"]:
        msgs = s.get("messages") or []
        if len(msgs) > MAX_MESSAGES:
            s["messages"] = msgs[-MAX_MESSAGES:]
    history_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _find(data: dict[str, Any], sid: str) -> dict[str, Any] | None:
    for s in data.get("sessions") or []:
        if s.get("id") == sid:
            return s
    return None


def get_active_id() -> str:
    data = _load()
    return str(data.get("active_id") or "")


def get_active_session() -> dict[str, Any]:
    data = _load()
    s = _find(data, str(data.get("active_id") or ""))
    if s:
        return dict(s)
    if data["sessions"]:
        return dict(data["sessions"][0])
    return _new_session()


def list_sessions() -> list[dict[str, Any]]:
    data = _load()
    active = data.get("active_id")
    out = []
    for s in sorted(
        data.get("sessions") or [],
        key=lambda x: x.get("updated_at") or "",
        reverse=True,
    ):
        item = {
            "id": s["id"],
            "title": s.get("title") or "对话",
            "created_at": s.get("created_at") or "",
            "updated_at": s.get("updated_at") or "",
            "message_count": len(s.get("messages") or []),
            "active": s["id"] == active,
        }
        out.append(item)
    return out


def create_session(title: str = "新对话", *, activate: bool = True) -> dict[str, Any]:
    data = _load()
    s = _new_session(title)
    data["sessions"].insert(0, s)
    if activate:
        data["active_id"] = s["id"]
    _save(data)
    return dict(s)


def switch_session(session_id: str) -> dict[str, Any] | None:
    data = _load()
    s = _find(data, session_id)
    if not s:
        return None
    data["active_id"] = session_id
    s["updated_at"] = _now()
    _save(data)
    return dict(s)


def rename_session(session_id: str, title: str) -> bool:
    data = _load()
    s = _find(data, session_id)
    if not s:
        return False
    s["title"] = _title_from_text(title, limit=40) if title.strip() else s["title"]
    s["updated_at"] = _now()
    _save(data)
    return True


def delete_session(session_id: str) -> bool:
    data = _load()
    sessions = data.get("sessions") or []
    if len(sessions) <= 1:
        # 至少保留一个：清空消息即可
        s = _find(data, session_id) or sessions[0]
        sid = str(s.get("id") or session_id)
        s["messages"] = []
        s["title"] = "新对话"
        s["updated_at"] = _now()
        data["active_id"] = s["id"]
        _save(data)
        try:
            from agent.lg_runtime import delete_thread

            delete_thread(sid)
        except Exception:
            pass
        return True
    data["sessions"] = [s for s in sessions if s.get("id") != session_id]
    if data.get("active_id") == session_id:
        data["active_id"] = data["sessions"][0]["id"]
    _save(data)
    try:
        from agent.lg_runtime import delete_thread

        delete_thread(session_id)
    except Exception:
        pass
    return True


def add_message(
    role: str,
    text: str,
    *,
    session_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}
    role = role if role in ("user", "assistant", "alarm", "system") else "assistant"
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        s = data["sessions"][0]
        data["active_id"] = s["id"]
    item: dict[str, Any] = {
        "id": uuid.uuid4().hex[:12],
        "role": role,
        "text": text,
        "ts": _now(),
    }
    if meta:
        # 可序列化字段：过程/终端 + 运行状态（失败重试/回退）
        clean: dict[str, Any] = {}
        proc = meta.get("process")
        if isinstance(proc, list) and proc:
            clean["process"] = [str(x) for x in proc if str(x).strip()][:40]
        terms = meta.get("terminals")
        if isinstance(terms, list) and terms:
            out_t = []
            for t in terms:
                if not isinstance(t, dict):
                    continue
                out_t.append(
                    {
                        "command": str(t.get("command") or ""),
                        "cwd": str(t.get("cwd") or ""),
                        "output": str(t.get("output") or "")[:8000],
                        "ok": bool(t.get("ok", True)),
                        "denied": bool(t.get("denied", False)),
                        "exit_code": t.get("exit_code"),
                    }
                )
            if out_t:
                clean["terminals"] = out_t
        for key in ("status", "error", "retryable", "prompt"):
            if key in meta and meta[key] is not None:
                val = meta[key]
                if key == "prompt":
                    clean[key] = str(val)[:50000]
                elif key == "retryable":
                    clean[key] = bool(val)
                else:
                    clean[key] = str(val)[:2000]
        if clean:
            item["meta"] = clean
    s.setdefault("messages", []).append(item)
    s["updated_at"] = _now()
    # 首条用户消息自动命名
    if role == "user" and (s.get("title") in ("新对话", "默认对话") or not s.get("title")):
        s["title"] = _title_from_text(text)
    _save(data)
    return item


def list_messages(limit: int = 200, *, session_id: str | None = None) -> list[dict[str, Any]]:
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return []
    msgs = s.get("messages") or []
    limit = max(1, min(int(limit), MAX_MESSAGES))
    return list(msgs[-limit:])


def get_message(msg_id: str, *, session_id: str | None = None) -> dict[str, Any] | None:
    data = _load()
    sessions = data.get("sessions") or []
    if session_id:
        sessions = [s for s in sessions if s.get("id") == session_id]
    for s in sessions:
        for m in s.get("messages") or []:
            if m.get("id") == msg_id:
                return dict(m)
    return None


def update_message_meta(
    msg_id: str,
    patch: dict[str, Any],
    *,
    session_id: str | None = None,
) -> bool:
    """合并写入某条消息的 meta。"""
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return False
    for m in s.get("messages") or []:
        if m.get("id") != msg_id:
            continue
        meta = dict(m.get("meta") or {}) if isinstance(m.get("meta"), dict) else {}
        meta.update(patch or {})
        m["meta"] = meta
        s["updated_at"] = _now()
        _save(data)
        return True
    return False


def replace_message_text(
    msg_id: str,
    text: str,
    *,
    session_id: str | None = None,
    prompt: str | None = None,
) -> bool:
    """改写某条消息正文；可选同步 meta.prompt（从此重开编辑后用）。"""
    text = (text or "").strip()
    if not text:
        return False
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return False
    for m in s.get("messages") or []:
        if m.get("id") != msg_id:
            continue
        m["text"] = text
        meta = dict(m.get("meta") or {}) if isinstance(m.get("meta"), dict) else {}
        if prompt is not None:
            meta["prompt"] = str(prompt)[:50000]
        elif "prompt" in meta:
            # 正文已改：旧 prompt 作废，用新正文作为请求
            meta["prompt"] = text[:50000]
        m["meta"] = meta
        m["ts"] = _now()
        s["updated_at"] = _now()
        # 若是会话首条用户消息，同步标题
        if m.get("role") == "user":
            msgs = s.get("messages") or []
            first_user = next((x for x in msgs if x.get("role") == "user"), None)
            if first_user and first_user.get("id") == msg_id:
                s["title"] = _title_from_text(text)
        _save(data)
        return True
    return False


def truncate_after_message(
    msg_id: str,
    *,
    session_id: str | None = None,
    keep_anchor: bool = True,
) -> dict[str, Any]:
    """截断到某条消息：默认保留该条及之前；丢弃之后全部。

    返回 {ok, session_id, kept, removed, anchor}。
    同时删除该会话 LangGraph checkpointer，下次由 transcript 冷启动。
    """
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return {"ok": False, "error": "会话不存在"}
    msgs = list(s.get("messages") or [])
    idx = next((i for i, m in enumerate(msgs) if m.get("id") == msg_id), -1)
    if idx < 0:
        return {"ok": False, "error": "消息不存在"}
    end = idx + 1 if keep_anchor else idx
    kept = msgs[:end]
    removed = msgs[end:]
    s["messages"] = kept
    s["updated_at"] = _now()
    _save(data)
    try:
        from agent.lg_runtime import delete_thread

        delete_thread(str(s.get("id") or sid))
    except Exception:
        pass
    anchor = dict(kept[-1]) if kept else {}
    return {
        "ok": True,
        "session_id": str(s.get("id") or sid),
        "kept": len(kept),
        "removed": len(removed),
        "anchor": anchor,
    }


def drop_trailing_failed_assistant(*, session_id: str | None = None) -> bool:
    """若最后一条是失败/取消的助手消息，删掉以便重试。"""
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return False
    msgs = s.get("messages") or []
    if not msgs:
        return False
    last = msgs[-1]
    if last.get("role") != "assistant":
        return False
    meta = last.get("meta") if isinstance(last.get("meta"), dict) else {}
    status = str(meta.get("status") or "")
    text = str(last.get("text") or "")
    if status in ("failed", "cancelled", "interrupted") or text.startswith("出错了：") or text.startswith("已停止："):
        msgs.pop()
        s["updated_at"] = _now()
        _save(data)
        return True
    return False


def clear_history(*, session_id: str | None = None) -> None:
    """清空当前（或指定）会话的消息，保留会话本身。"""
    data = _load()
    sid = session_id or data.get("active_id")
    s = _find(data, str(sid or ""))
    if not s:
        return
    s["messages"] = []
    s["updated_at"] = _now()
    _save(data)
    try:
        from agent.lg_runtime import delete_thread

        delete_thread(str(s.get("id") or sid))
    except Exception:
        pass


def recent_for_llm(
    limit: int = 16,
    *,
    exclude_trailing_user: bool = True,
    max_chars_per_msg: int = 2000,
    session_id: str | None = None,
) -> list[dict[str, str]]:
    msgs = list_messages(limit=max(1, int(limit) + 2), session_id=session_id)
    out: list[dict[str, str]] = []
    for m in msgs:
        role = m.get("role")
        if role not in ("user", "assistant"):
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        if len(text) > max_chars_per_msg:
            text = text[: max_chars_per_msg - 1] + "…"
        out.append({"role": role, "content": text})
    if exclude_trailing_user and out and out[-1]["role"] == "user":
        out = out[:-1]
    limit = max(1, min(int(limit), 40))
    return out[-limit:]


def format_sessions_brief() -> str:
    active = get_active_id()
    lines = [f"当前对话: {get_active_session().get('title')} ({active})"]
    for s in list_sessions()[:15]:
        mark = " ★" if s["id"] == active else ""
        lines.append(
            f"- [{s['id']}]{mark} {s['title']} · {s['message_count']}条 · {s['updated_at']}"
        )
    return "\n".join(lines)
