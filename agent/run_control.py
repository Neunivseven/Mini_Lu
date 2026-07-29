"""本轮运行控制：取消、失败重试、中断后续。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


class RunCancelled(Exception):
    """用户主动停止本轮。"""


@dataclass
class PendingRun:
    """可重试 / 续跑的上下文。"""

    prompt: str
    user_text: str = ""
    user_msg_id: str = ""
    session_id: str = ""
    reason: str = ""  # cancelled | network | error | history
    error: str = ""
    attempts: int = 0


_cancel = threading.Event()
_lock = threading.Lock()
_pending: PendingRun | None = None

# 网络类错误关键字（小写匹配）
# 注意：不要用裸 "timeout"（会误伤 timeout_seconds 等工具参数）
_NETWORK_HINTS = (
    "timed out",
    "timeouterror",
    "read timed out",
    "connect timeout",
    "connection timed out",
    "connection error",
    "connection reset",
    "connection aborted",
    "connection refused",
    "connecterror",
    "connect error",
    "network",
    "temporarily unavailable",
    "503",
    "502",
    "504",
    "429",
    "reset by peer",
    "broken pipe",
    "disconnected",
    "remoteprotocol",
    "ssl",
    "proxyerror",
    "name or service not known",
    "getaddrinfo",
    "max retries",
    "readerror",
    "writeerror",
)


def clear_cancel() -> None:
    _cancel.clear()


def request_cancel() -> None:
    _cancel.set()
    # 若卡在终端审批 wait，一并放行，避免 UI 丢审批按钮后永久挂起
    try:
        from agent.command_approval import deny_all_pending

        deny_all_pending()
    except Exception:
        pass


def is_cancelled() -> bool:
    return _cancel.is_set()


def raise_if_cancelled() -> None:
    if _cancel.is_set():
        raise RunCancelled("已停止本轮任务")


def is_invalid_chat_history(err: BaseException | str) -> bool:
    """LangGraph INVALID_CHAT_HISTORY：AIMessage.tool_calls 缺少对应 ToolMessage。"""
    s = str(err or "")
    low = s.lower()
    return (
        "invalid_chat_history" in low
        or ("tool_calls" in low and "toolmessage" in low)
        or ("tool_calls that do not have a corresponding" in low)
    )


def is_network_error(err: BaseException | str) -> bool:
    s = str(err or "").lower()
    # 勿把「对话历史损坏」误判成网络：错误里常带 timeout_seconds 等参数名
    if is_invalid_chat_history(err):
        return False
    return any(h in s for h in _NETWORK_HINTS)


def set_pending(run: PendingRun | None) -> None:
    global _pending
    with _lock:
        _pending = run


def get_pending() -> PendingRun | None:
    with _lock:
        return _pending


def clear_pending() -> None:
    set_pending(None)


def sleep_backoff(attempt: int) -> None:
    """attempt 从 0 起；短暂退避，期间可取消。"""
    delay = min(1.5 * (2 ** max(0, attempt)), 6.0)
    end = time.monotonic() + delay
    while time.monotonic() < end:
        raise_if_cancelled()
        time.sleep(0.15)
