"""跨线程 UI 请求：Agent 工作线程 → 主线程桌宠窗口。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    open_notes = Signal()
    open_memory = Signal()
    open_workspace = Signal()
    open_agent_studio = Signal()
    open_prompt = Signal()
    edits_changed = Signal()
    show_bubble = Signal(str)
    reminders_changed = Signal()  # 闹钟增删改 → 重调度到期 Timer
    # 工作线程 → 主线程：流式/终端审批等（dict，含 kind）
    agent_ui_event = Signal(object)


# 主线程创建后挂到 DesktopPet；工具侧通过 get_bridge() 发信号
_bridge: UiBridge | None = None


def init_bridge(parent: QObject | None = None) -> UiBridge:
    global _bridge
    _bridge = UiBridge(parent)
    return _bridge


def get_bridge() -> UiBridge | None:
    return _bridge


def emit_agent_ui(event: dict) -> None:
    """工具 / Agent 线程安全地往主线程推事件。

    主线程侧（DesktopPet / 未来 AgentController）收到后应再
    ``event_bus.emit('agent:stream', ...)``，供面板与 Plugin 订阅。
    """
    br = get_bridge()
    if br is not None:
        br.agent_ui_event.emit(event)
