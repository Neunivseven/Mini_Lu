"""应用级事件总线：面板 / Plugin / AgentController 统一订阅。

线程模型：
- 工作线程请继续走 ``ui_bridge.emit_agent_ui``（Qt 信号进主线程）
- 主线程收到后由 ``AgentController`` 再 ``event_bus.emit(...)``，供订阅方消费
"""
from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Any, Callable

Handler = Callable[..., Any]

# 约定事件名（字符串常量，便于检索与扩展）
AGENT_STREAM = "agent:stream"
AGENT_REPLY = "agent:reply"
AGENT_ERROR = "agent:error"
AGENT_BUSY = "agent:busy"
AGENT_CANCELLED = "agent:cancelled"
UI_OPEN_PANEL = "ui:open_panel"
UI_READY = "ui:ready"


class EventBus:
    def __init__(self) -> None:
        self._lock = RLock()
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def on(self, event: str, handler: Handler) -> Handler:
        """订阅；返回 handler 便于装饰器用法。"""
        with self._lock:
            self._handlers[event].append(handler)
        return handler

    def off(self, event: str, handler: Handler | None = None) -> None:
        with self._lock:
            if handler is None:
                self._handlers.pop(event, None)
                return
            bucket = self._handlers.get(event) or []
            self._handlers[event] = [h for h in bucket if h is not handler]

    def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            handlers = list(self._handlers.get(event) or [])
        for h in handlers:
            try:
                h(*args, **kwargs)
            except Exception:
                pass

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


_BUS: EventBus | None = None


def get_event_bus() -> EventBus:
    global _BUS
    if _BUS is None:
        _BUS = EventBus()
    return _BUS


def reset_event_bus() -> None:
    global _BUS
    if _BUS is not None:
        _BUS.clear()
    _BUS = None
