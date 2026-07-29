"""兼容旧导入：提醒气泡已并入 chat_bubble.BubbleLane。"""
from agent.chat_bubble import BubbleLane, ChatBubble

__all__ = ["BubbleLane", "ChatBubble"]
