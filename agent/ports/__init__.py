"""Ports & Adapters：应用核心依赖的抽象端口。"""

from agent.ports.llm import ChatModelPort, get_chat_model_adapter

__all__ = ["ChatModelPort", "get_chat_model_adapter"]
