"""
语言模型客户端（兼容垫片）。

权威路由在 ``agent.providers``（``get_hub()`` / ``ModelHub``）。
本模块仅提供：
- ``LLMConfig``：当前 active.chat 的扁平视图（旧代码参数形状）
- ``LLMClient`` / ``chat_text`` / ``load_llm_config``：薄封装，内部一律走 hub

新代码请优先 ``from agent.providers import get_hub``，避免再叠一层配置语义。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.providers.config import app_dir, load_models_config
from agent.providers.hub import get_hub, reset_hub


@dataclass
class LLMConfig:
    """当前 active.chat 的扁平视图（兼容旧代码）。"""

    provider: str = "deepseek"
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-v4-flash"
    timeout_seconds: float = 60.0
    system_prompt: str = "你是 Mini_Lu，桌面 Agent 助手，回答简洁有用。"
    reasoning_effort: str | None = None
    enable_thinking: bool = False


def load_llm_config(config_dir: Path | None = None) -> LLMConfig:
    cfg = load_models_config(config_dir)
    chat_id = cfg.active_id("chat") or "deepseek"
    try:
        spec = cfg.spec(chat_id)
    except KeyError:
        return LLMConfig(provider=chat_id)

    effort = spec.get("reasoning_effort", None)
    if effort is not None:
        effort = str(effort).strip() or None

    system = str(
        cfg.defaults.get("system_prompt")
        or spec.get("system_prompt")
        or "你是桌面宠物助手，回答简洁有用。"
    )
    timeout = float(
        spec.get("timeout_seconds")
        or cfg.defaults.get("timeout_seconds")
        or 60
    )

    return LLMConfig(
        provider=chat_id,
        api_key=spec.resolve_api_key(),
        base_url=str(spec.get("base_url") or "https://api.deepseek.com").rstrip("/"),
        model=str(spec.get("model") or "deepseek-v4-flash"),
        timeout_seconds=timeout,
        system_prompt=system,
        reasoning_effort=effort,
        enable_thinking=bool(spec.get("enable_thinking") or False),
    )


class LLMClient:
    """封装当前 active.chat Provider。"""

    def __init__(self, config: LLMConfig | None = None):
        # config 参数保留兼容；实际路由以 models 配置为准
        self.config = config or load_llm_config()
        if not self.config.api_key:
            raise RuntimeError(
                "未配置语言模型 API Key。请任选其一：\n"
                "  1) config/models.local.yaml 或 llm.local.yaml 填写 api_key\n"
                "  2) 设置对应环境变量（如 DEEPSEEK_API_KEY）"
            )
        self._hub = get_hub()

    def chat(
        self,
        user_text: str,
        *,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt or self.config.system_prompt,
            }
        ]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return self._hub.chat.chat(messages)


def get_client() -> LLMClient:
    return LLMClient()


def chat_text(
    *,
    system: str,
    user: str,
    temperature: float = 0.2,
    max_tokens: int = 400,
) -> str:
    """轻量单轮调用（记忆压缩/改写等），不走 Agent 工具环。"""
    hub = get_hub()
    return hub.chat.chat_text(
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def reload_models() -> None:
    """配置变更后清除缓存，下次 get_hub / LLMClient 会重新加载。"""
    reset_hub()


# 再导出，方便 from agent.llm_client import app_dir
__all__ = [
    "LLMConfig",
    "LLMClient",
    "load_llm_config",
    "get_client",
    "chat_text",
    "app_dir",
    "reload_models",
]
