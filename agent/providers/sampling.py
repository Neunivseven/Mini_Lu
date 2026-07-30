"""采样 / 推理参数策略（按模型差异）。

Kimi K3 / K2 官方约束摘要：
- temperature / top_p / n / presence_penalty / frequency_penalty 为固定值，建议不要显式传入
- reasoning_effort: low | high | max（K3 默认 max，且始终开启思考）
- max_completion_tokens 默认很大；可用该字段替代 max_tokens
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DEFAULT_TEMPERATURE = 0.3

_KIMI_K3 = re.compile(r"^kimi-k3(\.|$)", re.I)
_KIMI_K2 = re.compile(r"^kimi-k2(\.|$)", re.I)
_REASONING_EFFORTS = frozenset({"low", "high", "max"})


@dataclass(frozen=True)
class SamplingPolicy:
    """一次请求应如何带采样/推理参数。"""

    omit_fixed_sampling: bool = False
    """True：不要传 temperature/top_p/n/presence_penalty/frequency_penalty。"""
    default_reasoning_effort: str | None = None
    prefer_max_completion_tokens: bool = False
    force_image_data_url: bool = False
    """True：视觉只用 base64 / ms://，禁止公网 http(s) 图链。"""


def is_kimi_k3(model: str) -> bool:
    return bool(_KIMI_K3.match((model or "").strip()))


def is_kimi_k2(model: str) -> bool:
    return bool(_KIMI_K2.match((model or "").strip()))


def is_kimi_fixed_sampling(model: str) -> bool:
    return is_kimi_k3(model) or is_kimi_k2(model)


def model_requires_temperature_one(model: str) -> bool:
    """兼容旧名：固定采样模型若必须传 temperature，只能是 1。"""
    return is_kimi_fixed_sampling(model)


def sampling_policy_for(
    model: str, *, spec: dict[str, Any] | None = None
) -> SamplingPolicy:
    raw = spec or {}
    m = (model or "").strip()
    if is_kimi_k3(m):
        return SamplingPolicy(
            omit_fixed_sampling=True,
            default_reasoning_effort="max",
            prefer_max_completion_tokens=True,
            force_image_data_url=True,
        )
    if is_kimi_k2(m):
        # K2：固定采样参数，建议省略 temperature 等
        return SamplingPolicy(
            omit_fixed_sampling=True,
            default_reasoning_effort=None,
            prefer_max_completion_tokens=True,
            force_image_data_url=True,
        )
    # 配置可强制「省略固定采样」
    if bool(raw.get("omit_sampling_params")):
        return SamplingPolicy(
            omit_fixed_sampling=True,
            default_reasoning_effort=_normalize_effort(raw.get("reasoning_effort")),
            prefer_max_completion_tokens=bool(raw.get("prefer_max_completion_tokens")),
            force_image_data_url=bool(raw.get("force_image_data_url")),
        )
    return SamplingPolicy(
        default_reasoning_effort=_normalize_effort(raw.get("reasoning_effort")),
    )


def _normalize_effort(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _REASONING_EFFORTS:
        return s
    return None


def resolve_reasoning_effort(
    model: str, *, spec: dict[str, Any] | None = None
) -> str | None:
    raw = spec or {}
    explicit = _normalize_effort(raw.get("reasoning_effort"))
    if explicit:
        return explicit
    return sampling_policy_for(model, spec=raw).default_reasoning_effort


def resolve_chat_temperature(
    model: str,
    *,
    requested: float | None = None,
    spec: dict[str, Any] | None = None,
) -> float | None:
    """
    返回应写入请求的 temperature。
    - 固定采样模型：返回 None（省略，符合官方「不要显式传入」）
    - 其它：配置 / 请求值 / 默认 0.3
    """
    raw = spec or {}
    policy = sampling_policy_for(model, spec=raw)
    if policy.omit_fixed_sampling:
        # 若用户在配置里强行写了 temperature，仍尊重（自担风险）
        if "temperature" in raw and raw.get("temperature") is not None:
            try:
                return float(raw["temperature"])
            except (TypeError, ValueError):
                return None
        return None
    if "temperature" in raw and raw.get("temperature") is not None:
        try:
            return float(raw["temperature"])
        except (TypeError, ValueError):
            pass
    if requested is not None:
        return float(requested)
    return DEFAULT_TEMPERATURE


def apply_token_limit(
    body: dict[str, Any],
    *,
    model: str,
    max_tokens: int | None,
    spec: dict[str, Any] | None = None,
) -> None:
    """按策略写入 max_completion_tokens 或 max_tokens。"""
    if max_tokens is None:
        return
    policy = sampling_policy_for(model, spec=spec)
    n = int(max_tokens)
    if policy.prefer_max_completion_tokens:
        # Kimi 文档上限 1048576；默认量级很大，这里只尊重调用方传入值
        body["max_completion_tokens"] = max(1, min(n, 1_048_576))
    else:
        body["max_tokens"] = n
