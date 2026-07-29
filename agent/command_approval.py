"""跨线程：Agent 工作线程请求主线程确认终端命令。"""
from __future__ import annotations

import threading
import uuid
from typing import Any, Callable

from agent.command_trust import is_trusted, trust_exact


_pending: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def request_command_approval(
    *,
    command: str,
    cwd: str = "",
    timeout_sec: float = 600.0,
) -> dict[str, Any]:
    """
    在工作线程调用。返回:
      {action: allow|deny|always, trusted: bool, request_id: str}
    always 会写入信任表并按 allow 处理。
    """
    import time

    cmd = (command or "").strip()
    rid = uuid.uuid4().hex[:12]

    if is_trusted(cmd):
        _emit_ui(
            "command_auto",
            {"request_id": rid, "command": cmd, "cwd": cwd or "", "trusted": True},
        )
        return {"action": "allow", "trusted": True, "request_id": rid}

    event = threading.Event()
    with _lock:
        _pending[rid] = {
            "event": event,
            "result": None,
            "command": cmd,
            "cwd": cwd or "",
        }

    _emit_ui(
        "command_approval",
        {"request_id": rid, "command": cmd, "cwd": cwd or "", "trusted": False},
    )

    deadline = time.monotonic() + max(30.0, float(timeout_sec or 600))
    ok = False
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if event.wait(timeout=min(0.5, remaining)):
            ok = True
            break
        try:
            from agent.run_control import is_cancelled

            if is_cancelled():
                break
        except Exception:
            pass

    with _lock:
        box = _pending.pop(rid, None)
    if not ok or not box or not box.get("result"):
        return {
            "action": "deny",
            "trusted": False,
            "request_id": rid,
            "error": "超时未确认" if not ok else "已取消",
        }

    result = dict(box["result"])
    action = str(result.get("action") or "deny")
    if action == "always":
        trust_exact(cmd)
        action = "allow"
        result["action"] = "allow"
        result["trusted"] = True
    result["request_id"] = rid
    return result


def resolve_command_approval(request_id: str, action: str) -> None:
    """主线程：用户点了 运行 / 总是允许 / 取消。"""
    rid = (request_id or "").strip()
    act = (action or "deny").strip().lower()
    if act not in ("allow", "deny", "always"):
        act = "deny"
    with _lock:
        box = _pending.get(rid)
        if not box:
            return
        box["result"] = {"action": act}
        box["event"].set()


def deny_all_pending() -> None:
    """停止本轮时：拒绝所有未确认终端请求，避免工作线程永久阻塞。"""
    with _lock:
        for box in _pending.values():
            if box.get("result"):
                continue
            box["result"] = {"action": "deny"}
            try:
                box["event"].set()
            except Exception:
                pass


def list_pending_approvals() -> list[dict[str, Any]]:
    """主线程：尚未确认的终端请求（用于 UI 被刷新后恢复）。"""
    with _lock:
        out = []
        for rid, box in _pending.items():
            if box.get("result"):
                continue
            out.append(
                {
                    "request_id": rid,
                    "command": str(box.get("command") or ""),
                    "cwd": str(box.get("cwd") or ""),
                }
            )
        return out


def reemit_pending_approvals() -> int:
    """把未确认请求再推到 UI（流式卡片被清掉时调用）。"""
    pending = list_pending_approvals()
    for item in pending:
        _emit_ui("command_approval", {**item, "trusted": False})
    return len(pending)


def notify_command_result(request_id: str, payload: dict[str, Any]) -> None:
    """命令执行结束，推到 UI 更新内嵌终端块。"""
    data = dict(payload or {})
    data["request_id"] = request_id
    _emit_ui("command_result", data)


def _emit_ui(kind: str, payload: dict[str, Any]) -> None:
    try:
        from agent.ui_bridge import get_bridge

        br = get_bridge()
        if br is not None:
            br.agent_ui_event.emit({"kind": kind, **payload})
    except Exception:
        pass


# 可选：单元测试注入
_test_hook: Callable[[dict[str, Any]], None] | None = None
