"""
LangGraph 官方记忆运行时（短时 Checkpointer + 长时 Store）。

不自研记忆逻辑：仅封装 SqliteSaver / SqliteStore 的进程级单例与路径。
"""
from __future__ import annotations

import atexit
from pathlib import Path
from typing import Any

from agent.llm_client import data_dir

# 跨会话长期记忆命名空间（Store）
MEMORY_NAMESPACE: tuple[str, ...] = ("memories", "pet")

_checkpointer = None
_store = None
_cp_cm = None
_store_cm = None


def _data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def checkpoint_db_path() -> Path:
    return _data_dir() / "lg_checkpoints.sqlite"


def store_db_path() -> Path:
    return _data_dir() / "lg_store.sqlite"


def get_checkpointer():
    """短时记忆：按 thread_id（会话 id）持久化 messages。"""
    global _checkpointer, _cp_cm
    if _checkpointer is not None:
        return _checkpointer
    from langgraph.checkpoint.sqlite import SqliteSaver

    _cp_cm = SqliteSaver.from_conn_string(str(checkpoint_db_path()))
    _checkpointer = _cp_cm.__enter__()
    atexit.register(_close_checkpointer)
    return _checkpointer


def get_store():
    """长时记忆：跨 thread 的 key-value Store。"""
    global _store, _store_cm
    if _store is not None:
        return _store
    from langgraph.store.sqlite import SqliteStore

    _store_cm = SqliteStore.from_conn_string(str(store_db_path()))
    _store = _store_cm.__enter__()
    # 部分版本需要 setup
    setup = getattr(_store, "setup", None)
    if callable(setup):
        try:
            setup()
        except Exception:
            pass
    atexit.register(_close_store)
    return _store


def _close_checkpointer() -> None:
    global _checkpointer, _cp_cm
    if _cp_cm is not None:
        try:
            _cp_cm.__exit__(None, None, None)
        except Exception:
            pass
    _cp_cm = None
    _checkpointer = None


def _close_store() -> None:
    global _store, _store_cm
    if _store_cm is not None:
        try:
            _store_cm.__exit__(None, None, None)
        except Exception:
            pass
    _store_cm = None
    _store = None


def thread_config(session_id: str | None = None) -> dict[str, Any]:
    """invoke / get_state 用的 configurable。"""
    if not session_id:
        try:
            from agent.chat_history import get_active_id

            session_id = get_active_id()
        except Exception:
            session_id = "default"
    sid = (session_id or "default").strip() or "default"
    return {
        "configurable": {
            "thread_id": sid,
            "user_id": "pet",
        }
    }


def delete_thread(session_id: str) -> None:
    """删除某会话的短时 checkpoint（会话被删时调用）。"""
    sid = (session_id or "").strip()
    if not sid:
        return
    try:
        cp = get_checkpointer()
        cp.delete_thread(sid)
    except Exception:
        pass


def _tool_call_id(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("id") or "")
    return str(getattr(tc, "id", None) or "")


def _tool_call_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return str(tc.get("name") or "tool")
    return str(getattr(tc, "name", None) or "tool")


def repair_dangling_tool_calls(
    session_id: str | None = None,
    *,
    agent=None,
    note: str = "（工具调用中断，未返回结果）",
) -> int:
    """
    为 checkpoint 中缺少 ToolMessage 的 tool_calls 补占位结果。
    中断/取消/网络失败后若留下半截 AIMessage.tool_calls，下次会触发
    INVALID_CHAT_HISTORY；调用本函数可修复。返回补了几条。
    """
    cfg = thread_config(session_id)
    msgs: list[Any] = []

    if agent is not None:
        try:
            snap = agent.get_state(cfg)
            values = getattr(snap, "values", None) or {}
            msgs = list(values.get("messages") or [])
        except Exception:
            msgs = []
    if not msgs:
        try:
            cp = get_checkpointer()
            tup = cp.get_tuple(cfg)
            if tup is not None:
                ch = getattr(tup, "checkpoint", None) or {}
                channel = ch.get("channel_values") if isinstance(ch, dict) else None
                if isinstance(channel, dict):
                    msgs = list(channel.get("messages") or [])
        except Exception:
            msgs = []
    if not msgs:
        return 0

    answered: set[str] = set()
    for m in msgs:
        name = m.__class__.__name__ if not isinstance(m, dict) else ""
        role = getattr(m, "type", None) or getattr(m, "role", None)
        if role in ("tool",) or name == "ToolMessage" or (
            isinstance(m, dict) and m.get("role") == "tool"
        ):
            tid = getattr(m, "tool_call_id", None)
            if tid is None and isinstance(m, dict):
                tid = m.get("tool_call_id")
            if tid:
                answered.add(str(tid))

    patches: list[Any] = []
    for m in msgs:
        tcs = getattr(m, "tool_calls", None) or []
        if not tcs and isinstance(m, dict):
            tcs = m.get("tool_calls") or []
        for tc in tcs:
            tid = _tool_call_id(tc)
            if not tid or tid in answered:
                continue
            answered.add(tid)
            try:
                from langchain_core.messages import ToolMessage

                patches.append(
                    ToolMessage(
                        content=note,
                        tool_call_id=tid,
                        name=_tool_call_name(tc),
                    )
                )
            except Exception:
                patches.append(
                    {
                        "role": "tool",
                        "content": note,
                        "tool_call_id": tid,
                        "name": _tool_call_name(tc),
                    }
                )

    if not patches:
        return 0

    if agent is not None:
        try:
            agent.update_state(cfg, {"messages": patches})
            return len(patches)
        except Exception:
            pass

    # 无 agent 或 update_state 失败：整段 thread 清掉，下次由 UI transcript 冷启动
    sid = ""
    try:
        sid = str((cfg.get("configurable") or {}).get("thread_id") or "")
    except Exception:
        sid = ""
    delete_thread(sid)
    return -1


def ensure_thread_sane(session_id: str | None = None, *, agent=None) -> int:
    """开跑前确保无悬空 tool_calls；返回修补条数（-1 表示已清空 thread）。"""
    try:
        return repair_dangling_tool_calls(session_id, agent=agent)
    except Exception:
        return 0


def _value_text(val: Any) -> str:
    """取记忆条目的可读文本；兼容 remember（{"text"}）与 langmem（{"content"}）格式。"""
    if not isinstance(val, dict):
        return str(val or "")
    t = val.get("text") or val.get("data")
    if t:
        return str(t)
    c = val.get("content")
    if isinstance(c, dict):
        return str(c.get("content") or c.get("text") or c)
    if c is not None:
        return str(c)
    return str(val)


def list_long_term_items(limit: int = 50) -> list[dict[str, Any]]:
    """供 UI 展示 Store 中的长期记忆。"""
    store = get_store()
    items = store.search(MEMORY_NAMESPACE, limit=max(1, min(int(limit), 200)))
    out: list[dict[str, Any]] = []
    for it in items:
        out.append(
            {
                "key": getattr(it, "key", ""),
                "text": _value_text(getattr(it, "value", None)),
                "updated_at": str(getattr(it, "updated_at", "") or ""),
            }
        )
    return out


def format_store_block(limit: int = 20) -> str:
    """注入 system 的长期记忆摘要。"""
    items = list_long_term_items(limit=limit)
    if not items:
        return ""
    lines = ["【长期记忆 · LangGraph Store】"]
    for it in items:
        t = (it.get("text") or "").strip()
        if t:
            lines.append(f"- ({it.get('key')}) {t}")
    return "\n".join(lines) if len(lines) > 1 else ""


def clear_long_term_store() -> int:
    store = get_store()
    items = store.search(MEMORY_NAMESPACE, limit=500)
    n = 0
    for it in items:
        key = getattr(it, "key", None)
        if key:
            store.delete(MEMORY_NAMESPACE, key)
            n += 1
    return n
