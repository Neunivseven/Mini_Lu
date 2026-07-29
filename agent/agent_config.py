"""Agent 运行模式与 Plan-and-Execute 配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir


@dataclass
class RouterConfig:
    min_plan_signals: int = 2
    long_text_chars: int = 120
    use_llm_when_ambiguous: bool = True


@dataclass
class PlannerConfig:
    max_plan_steps: int = 8


@dataclass
class ExecutorConfig:
    recursion_limit: int = 40


@dataclass
class AgentRuntimeConfig:
    """mode: auto | react | plan_execute"""

    mode: str = "auto"
    router: RouterConfig = field(default_factory=RouterConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)


def _config_dir() -> Path:
    return app_dir() / "config"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merge_section(base: dict[str, Any], overlay: dict[str, Any], key: str) -> dict[str, Any]:
    b = base.get(key) if isinstance(base.get(key), dict) else {}
    o = overlay.get(key) if isinstance(overlay.get(key), dict) else {}
    return {**b, **o}


def load_agent_config(config_dir: Path | None = None) -> AgentRuntimeConfig:
    root = config_dir or _config_dir()
    base = _read_yaml(root / "agent.yaml")
    local = _read_yaml(root / "agent.local.yaml")
    data = {**base, **local} if local else dict(base)
    if local:
        for key in ("router", "planner", "executor"):
            data[key] = _merge_section(base, local, key)

    mode = str(data.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "react", "plan_execute"):
        mode = "auto"

    r = data.get("router") if isinstance(data.get("router"), dict) else {}
    p = data.get("planner") if isinstance(data.get("planner"), dict) else {}
    e = data.get("executor") if isinstance(data.get("executor"), dict) else {}

    return AgentRuntimeConfig(
        mode=mode,
        router=RouterConfig(
            min_plan_signals=max(1, int(r.get("min_plan_signals") or 2)),
            long_text_chars=max(40, int(r.get("long_text_chars") or 120)),
            use_llm_when_ambiguous=bool(r.get("use_llm_when_ambiguous", True)),
        ),
        planner=PlannerConfig(
            max_plan_steps=max(2, int(p.get("max_plan_steps") or 8)),
        ),
        executor=ExecutorConfig(
            recursion_limit=max(10, int(e.get("recursion_limit") or 40)),
        ),
    )
