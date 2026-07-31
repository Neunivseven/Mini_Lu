"""Agent 运行模式与 Plan-and-Execute 配置。"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.llm_client import config_read_path


@dataclass
class RouterConfig:
    min_plan_signals: int = 2
    long_text_chars: int = 200
    use_llm_when_ambiguous: bool = False


@dataclass
class PlannerConfig:
    max_plan_steps: int = 8


@dataclass
class ExecutorConfig:
    recursion_limit: int = 40


@dataclass
class MemoryConfig:
    """langmem 后台记忆提取（默认关闭；开启有额外 LLM 调用成本）。"""

    auto_extract: bool = False
    min_interval_seconds: int = 120
    max_chars: int = 4000


@dataclass
class AgentRuntimeConfig:
    """mode: auto | react | plan_execute"""

    mode: str = "auto"
    router: RouterConfig = field(default_factory=RouterConfig)
    planner: PlannerConfig = field(default_factory=PlannerConfig)
    executor: ExecutorConfig = field(default_factory=ExecutorConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)




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
    if config_dir is not None:
        base = _read_yaml(config_dir / "agent.yaml")
        local = _read_yaml(config_dir / "agent.local.yaml")
    else:
        base = _read_yaml(config_read_path("agent.yaml"))
        local = _read_yaml(config_read_path("agent.local.yaml"))
    data = {**base, **local} if local else dict(base)
    if local:
        for key in ("router", "planner", "executor", "memory"):
            data[key] = _merge_section(base, local, key)

    mode = str(data.get("mode") or "auto").strip().lower()
    if mode not in ("auto", "react", "plan_execute"):
        mode = "auto"

    r = data.get("router") if isinstance(data.get("router"), dict) else {}
    p = data.get("planner") if isinstance(data.get("planner"), dict) else {}
    e = data.get("executor") if isinstance(data.get("executor"), dict) else {}
    m = data.get("memory") if isinstance(data.get("memory"), dict) else {}

    return AgentRuntimeConfig(
        mode=mode,
        router=RouterConfig(
            min_plan_signals=max(1, int(r.get("min_plan_signals") or 2)),
            long_text_chars=max(40, int(r.get("long_text_chars") or 200)),
            use_llm_when_ambiguous=bool(r.get("use_llm_when_ambiguous", False)),
        ),
        planner=PlannerConfig(
            max_plan_steps=max(2, int(p.get("max_plan_steps") or 8)),
        ),
        executor=ExecutorConfig(
            recursion_limit=max(10, int(e.get("recursion_limit") or 40)),
        ),
        memory=MemoryConfig(
            auto_extract=bool(m.get("auto_extract", False)),
            min_interval_seconds=max(0, int(m.get("min_interval_seconds") or 120)),
            max_chars=max(500, int(m.get("max_chars") or 4000)),
        ),
    )
