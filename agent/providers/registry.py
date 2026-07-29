"""模型配置读写：列出 / 切换 active / 写入 models.local.yaml。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent.providers.config import (
    ModelsConfig,
    ProviderSpec,
    app_dir,
    load_models_config,
)
from agent.providers.hub import reset_hub

_CAPABILITIES = ("chat", "asr", "vision", "image")


def config_dir() -> Path:
    return app_dir() / "config"


def models_local_path() -> Path:
    return config_dir() / "models.local.yaml"


def _read_local() -> dict[str, Any]:
    path = models_local_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_local(data: dict[str, Any]) -> None:
    path = models_local_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def list_providers(
    *,
    capability: str | None = None,
    cfg: ModelsConfig | None = None,
) -> list[ProviderSpec]:
    """列出已注册 provider；可按 type 过滤。"""
    cfg = cfg or load_models_config()
    items = list(cfg.providers.values())
    if capability:
        want = capability.strip().lower()
        items = [p for p in items if (p.type or "").lower() == want]
    return sorted(items, key=lambda p: p.id)


def provider_summary(spec: ProviderSpec) -> dict[str, Any]:
    key = spec.resolve_api_key()
    caps = sorted(spec.capabilities())
    return {
        "id": spec.id,
        "type": spec.type,
        "driver": spec.driver,
        "model": str(spec.get("model") or ""),
        "base_url": str(spec.get("base_url") or ""),
        "label": str(spec.get("label") or spec.id),
        "has_key": bool(key),
        "api_key_env": str(spec.get("api_key_env") or ""),
        "capabilities": caps,
    }


def format_providers_report(cfg: ModelsConfig | None = None) -> str:
    cfg = cfg or load_models_config()
    lines = ["【当前启用】"]
    for cap in _CAPABILITIES:
        aid = cfg.active_id(cap)
        if not aid:
            lines.append(f"  {cap}: （未启用）")
            continue
        try:
            sp = cfg.spec(aid)
            mark = "✓Key" if sp.resolve_api_key() else "✗无Key"
            lines.append(
                f"  {cap}: {aid} · {sp.get('model') or '?'} · {mark}"
            )
        except KeyError:
            lines.append(f"  {cap}: {aid}（配置缺失）")

    lines.append("")
    lines.append("【可用 Chat 接口】（OpenAI 兼容均可接入）")
    for sp in list_providers(capability="chat", cfg=cfg):
        s = provider_summary(sp)
        active = "◀" if cfg.active_id("chat") == sp.id else " "
        key = "有Key" if s["has_key"] else "无Key"
        lines.append(
            f"  {active} {s['id']}: {s['label']} | {s['model']} | {key}"
            f" | caps={','.join(s.get('capabilities') or []) or '—'}"
        )
        if s["base_url"]:
            lines.append(f"      {s['base_url']}")
    lines.append("")
    lines.append("切换：UI「模型设置」或工具 set_chat_provider(id)；密钥写 models.local.yaml。")
    return "\n".join(lines)


def set_active(capability: str, provider_id: str | None) -> str:
    """切换 active.*，写入 models.local.yaml 并重置 hub。"""
    cap = (capability or "").strip().lower()
    if cap not in _CAPABILITIES:
        raise ValueError(f"capability 须为 {_CAPABILITIES} 之一")

    cfg = load_models_config()
    if provider_id is None or str(provider_id).strip().lower() in ("", "null", "none"):
        local = _read_local()
        active = dict(local.get("active") or {})
        active[cap] = None
        local["active"] = active
        _write_local(local)
        reset_hub()
        return f"已关闭 active.{cap}"

    pid = str(provider_id).strip()
    if pid not in cfg.providers:
        known = ", ".join(p.id for p in list_providers(capability=cap, cfg=cfg)) or "（无）"
        raise KeyError(f"未知 provider「{pid}」。{cap} 可选: {known}")
    spec = cfg.providers[pid]
    if (spec.type or "").lower() != cap:
        raise ValueError(
            f"[{pid}] 的 type={spec.type}，不能用作 active.{cap}"
        )

    local = _read_local()
    active = dict(local.get("active") or {})
    active[cap] = pid
    local["active"] = active
    _write_local(local)
    reset_hub()
    return f"已切换 active.{cap} → {pid}"


def set_provider_fields(provider_id: str, **fields: Any) -> str:
    """更新某 provider 字段（api_key / model / base_url 等）到 models.local.yaml。"""
    pid = (provider_id or "").strip()
    if not pid:
        raise ValueError("请提供 provider_id")
    cfg = load_models_config()
    if pid not in cfg.providers:
        raise KeyError(f"未知 provider: {pid}")

    allowed = {
        "api_key",
        "base_url",
        "model",
        "api_key_env",
        "timeout_seconds",
        "reasoning_effort",
        "enable_thinking",
        "label",
        "capabilities",
    }
    patch = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not patch:
        raise ValueError(f"无可写字段；允许: {sorted(allowed)}")

    local = _read_local()
    providers = dict(local.get("providers") or {})
    entry = dict(providers.get(pid) or {})
    for k, v in patch.items():
        if k == "api_key" and str(v).strip() == "":
            entry.pop("api_key", None)
        else:
            entry[k] = v
    providers[pid] = entry
    local["providers"] = providers
    _write_local(local)
    reset_hub()
    bits = ", ".join(f"{k}=…" if k == "api_key" else f"{k}={v}" for k, v in patch.items())
    return f"已更新 [{pid}]: {bits}"


def ensure_custom_openai(
    *,
    provider_id: str = "custom_openai",
    base_url: str,
    model: str,
    api_key: str = "",
    label: str = "自定义 OpenAI 兼容",
    set_as_chat: bool = True,
) -> str:
    """注册/更新一个自定义 OpenAI 兼容 Chat 端点（写入 models.local.yaml）。"""
    pid = (provider_id or "custom_openai").strip() or "custom_openai"
    base = (base_url or "").strip().rstrip("/")
    model_name = (model or "").strip()
    if not base:
        raise ValueError("请提供 base_url，例如 https://api.xxx.com/v1")
    if not model_name:
        raise ValueError("请提供 model 名称")

    local = _read_local()
    providers = dict(local.get("providers") or {})
    entry = dict(providers.get(pid) or {})
    entry.update(
        {
            "type": "chat",
            "driver": "openai_compat",
            "label": label or pid,
            "base_url": base,
            "model": model_name,
            "api_key_env": entry.get("api_key_env") or f"{pid.upper()}_API_KEY",
        }
    )
    if api_key.strip():
        entry["api_key"] = api_key.strip()
    providers[pid] = entry
    local["providers"] = providers
    if set_as_chat:
        active = dict(local.get("active") or {})
        active["chat"] = pid
        local["active"] = active
    _write_local(local)
    reset_hub()
    msg = f"已配置 [{pid}] → {base} · {model_name}"
    if set_as_chat:
        msg += "，并设为 active.chat"
    return msg
