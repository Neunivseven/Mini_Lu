"""OpenAI 兼容 Chat Completions（DeepSeek / OpenAI / Moonshot / 通义兼容网关等）。"""
from __future__ import annotations

from typing import Any

try:
    from openai import OpenAI
except ImportError as e:  # pragma: no cover
    raise ImportError("请先安装 openai：pip install openai") from e

from agent.providers.base import ChatProvider, ProviderError
from agent.providers.config import ProviderSpec
from agent.providers.sampling import (
    apply_token_limit,
    resolve_chat_temperature,
    resolve_reasoning_effort,
    sampling_policy_for,
)


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
        self._policy = sampling_policy_for(self._model, spec=spec.raw)
        self._reasoning_effort = resolve_reasoning_effort(
            self._model, spec=spec.raw
        )
        # enable_thinking：非固定采样模型可用
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
        # 固定采样模型：省略 temperature/top_p/n/penalty（官方建议）
        if not self._policy.omit_fixed_sampling:
            temp = resolve_chat_temperature(
                self._model, requested=temperature, spec=self.spec.raw
            )
            if temp is not None:
                body["temperature"] = temp
        else:
            # 仅当配置显式写了 temperature 才带上
            temp = resolve_chat_temperature(
                self._model, requested=None, spec=self.spec.raw
            )
            if temp is not None and "temperature" in (self.spec.raw or {}):
                body["temperature"] = temp

        apply_token_limit(
            body, model=self._model, max_tokens=max_tokens, spec=self.spec.raw
        )
        if self._reasoning_effort:
            body["reasoning_effort"] = self._reasoning_effort
        if self._enable_thinking and not self._policy.omit_fixed_sampling:
            body["extra_body"] = {"thinking": {"type": "enabled"}}

        # 调用方 kwargs 不得把固定采样参数又塞回来
        if self._policy.omit_fixed_sampling:
            for k in (
                "temperature",
                "top_p",
                "n",
                "presence_penalty",
                "frequency_penalty",
            ):
                kwargs.pop(k, None)
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

        msg = resp.choices[0].message
        content = (getattr(msg, "content", None) or "").strip()
        if not content:
            raise ProviderError(
                f"[{self.name}] 模型返回空内容（可检查 reasoning_effort / 模型名）"
            )
        return content

    def langchain_kwargs(self) -> dict[str, Any]:
        """供 LangChain ChatOpenAI 使用。

        temperature=None 时 LangChain 不会把该字段写入请求（符合 Kimi「勿显式传」）。
        reasoning_effort 为顶层字段。
        """
        kw: dict[str, Any] = {
            "model": self._model,
            "api_key": self.spec.resolve_api_key(),
            "base_url": str(self.spec.get("base_url")).rstrip("/"),
            "timeout": float(self.spec.get("timeout_seconds") or 60),
        }
        if self._policy.omit_fixed_sampling:
            # 显式 None：覆盖 ChatOpenAI 默认 0.7，并在 payload 中省略
            kw["temperature"] = None
            kw["top_p"] = None
            kw["presence_penalty"] = None
            kw["frequency_penalty"] = None
        else:
            temp = resolve_chat_temperature(self._model, spec=self.spec.raw)
            if temp is not None:
                kw["temperature"] = temp
        if self._reasoning_effort:
            kw["reasoning_effort"] = self._reasoning_effort
        return kw
