"""LLM 端口：Agent 层应依赖此接口；LangChain 仅为一种适配器。

``pet_agent.build_chat_model`` 仍供 LangGraph 使用；无图场景请走
``get_hub().chat`` / ``LLMClient``，或本模块的 ``ChatModelPort``。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ChatModelPort(Protocol):
    """与框架无关的对话端口。"""

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str: ...


def get_chat_model_adapter() -> ChatModelPort:
    """返回当前 active.chat 的 Provider（非 LangChain 对象）。"""
    from agent.providers.hub import get_hub

    return get_hub().chat


def as_langchain_chat_model(config=None):
    """LangGraph / create_react_agent 适配器（显式命名，避免与端口混淆）。"""
    from agent.pet_agent import build_chat_model

    return build_chat_model(config)
