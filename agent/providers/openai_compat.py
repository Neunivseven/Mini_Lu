"""OpenAI 兼容 Chat Completions（DeepSeek / OpenAI / 通义兼容网关等）。"""
from __future__ import annotations

from typing import Any

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise ImportError("请先安装 openai：pip install openai") from e

from agent.providers.base import ChatProvider, ProviderError
from agent.providers.config import ProviderSpec


class OpenAICompatChatProvider(ChatProvider):
    def __init__(self, spec: ProviderSpec):
        self.spec = spec
        self.name = spec.id
        api_key = spec.resolve_api_key()
        if not api_key:
            env = spec.get("api_key_env") or "API_KEY"
            raise ProviderError(
                f"未配置 [{spec.id}] 的 API Key。请在 models.local.yaml / llm.local.yaml "
                f"填写 api_key，或设置环境变量 {env}。"
            )
        base_url = str(spec.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ProviderError(f"[{spec.id}] 缺少 base_url")
        timeout = float(spec.get("timeout_seconds") or 60)
        self._model = str(spec.get("model") or "")
        if not self._model:
            raise ProviderError(f"[{spec.id}] 缺少 model")
        self._reasoning_effort = spec.get("reasoning_effort")
        if self._reasoning_effort is not None:
            self._reasoning_effort = str(self._reasoning_effort).strip() or None
        self._enable_thinking = bool(spec.get("enable_thinking") or False)
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "stream": False,
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        if self._enable_thinking:
            body["extra_body"] = {"thinking": {"type": "enabled"}}
        body.update(kwargs)

        try:
            resp = self._client.chat.completions.create(**body)
        except Exception as e:
            err = str(e)
            if "401" in err or "Unauthorized" in err or "invalid_api_key" in err.lower():
                raise ProviderError(
                    f"[{self.name}] API 鉴权失败（401）。请检查 api_key。"
                ) from e
            raise ProviderError(f"[{self.name}] API 调用失败: {e}") from e

        content = (resp.choices[0].message.content or "").strip()
        if not content:
            raise ProviderError(
                f"[{self.name}] 模型返回空内容（可检查 enable_thinking / 模型名）"
            )
        return content

    def langchain_kwargs(self) -> dict[str, Any]:
        """供 LangChain ChatOpenAI 使用。"""
        return {
            "model": self._model,
            "api_key": self.spec.resolve_api_key(),
            "base_url": str(self.spec.get("base_url")).rstrip("/"),
            "timeout": float(self.spec.get("timeout_seconds") or 60),
        }
