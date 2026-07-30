"""可插拔扩展点：Skills / MCP / Extensions 统一生命周期。"""
from __future__ import annotations

from abc import ABC
from typing import Any, Protocol


class ToolRegistryLike(Protocol):
    def register(self, category: str, tool_fn: Any, **kwargs: Any) -> None: ...

    def register_many(self, category: str, tools: Any, **kwargs: Any) -> None: ...


class PluginHost(Protocol):
    """UI / App 宿主能力（由 PanelManager 满足）。"""

    def open_panel(self, name: str) -> None: ...


class Plugin(ABC):
    """扩展抽象基类。默认空实现，按需覆盖。"""

    name: str = "plugin"
    priority: int = 100  # 越小越先加载

    def register_tools(self, registry: ToolRegistryLike) -> None:
        return None

    def on_agent_event(self, event: str, data: Any = None) -> None:
        return None

    def on_ui_ready(self, host: PluginHost) -> None:
        return None

    def system_prompt_extra(self) -> str:
        """追加到 Agent system prompt（Skills 注入除外，见 collect_system_prompt）。"""
        return ""

    def on_unload(self) -> None:
        return None


class SkillPromptPlugin(Plugin):
    """已启用 Skill 的元数据插件；正文注入由 collect_system_prompt → skills_store 统一完成。"""

    priority = 80

    def __init__(self, skill_name: str, description: str = ""):
        self.name = f"skill:{skill_name}"
        self.skill_name = skill_name
        self.description = description or skill_name


class PluginManager:
    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        self._plugins = [p for p in self._plugins if p.name != plugin.name]
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: (p.priority, p.name))

    def unregister(self, name: str) -> None:
        removed = [p for p in self._plugins if p.name == name]
        self._plugins = [p for p in self._plugins if p.name != name]
        for p in removed:
            try:
                p.on_unload()
            except Exception:
                pass

    def all(self) -> list[Plugin]:
        return list(self._plugins)

    def register_all_tools(self, registry: ToolRegistryLike) -> None:
        for p in self._plugins:
            try:
                p.register_tools(registry)
            except Exception:
                pass

    def dispatch_agent_event(self, event: str, data: Any = None) -> None:
        for p in self._plugins:
            try:
                p.on_agent_event(event, data)
            except Exception:
                pass

    def notify_ui_ready(self, host: PluginHost) -> None:
        for p in self._plugins:
            try:
                p.on_ui_ready(host)
            except Exception:
                pass

    def collect_system_prompt(self, *, max_chars: int | None = None) -> str:
        """Skills 注入只走 skills_store 一次；其它 Plugin 追加 system_prompt_extra。"""
        parts: list[str] = []
        try:
            from agent.skills_store import format_skills_inject_block, load_skills_config

            block = format_skills_inject_block()
            if block:
                parts.append(block)
            if max_chars is None:
                cfg = load_skills_config()
                max_chars = int(cfg.get("auto_inject_max_chars") or 6000)
        except Exception:
            if max_chars is None:
                max_chars = 6000

        for p in self._plugins:
            if isinstance(p, SkillPromptPlugin):
                continue
            try:
                extra = (p.system_prompt_extra() or "").strip()
            except Exception:
                continue
            if extra:
                parts.append(extra)

        text = "\n\n".join(parts).strip()
        if max_chars and len(text) > max_chars:
            text = text[: max_chars - 1] + "…"
        return text

    def load_skill_plugins(self) -> int:
        """从 skills_store 扫描已启用 skill，注册为 SkillPromptPlugin。"""
        # 先卸掉旧 skill:* 插件
        for p in list(self._plugins):
            if isinstance(p, SkillPromptPlugin) or p.name.startswith("skill:"):
                self.unregister(p.name)
        try:
            from agent.skills_store import enabled_skills
        except Exception:
            return 0
        n = 0
        try:
            for sk in enabled_skills() or []:
                name = str(getattr(sk, "name", "") or "").strip()
                if not name:
                    continue
                self.register(
                    SkillPromptPlugin(
                        name,
                        description=str(getattr(sk, "description", "") or ""),
                    )
                )
                n += 1
        except Exception:
            return n
        return n


def reload_plugins_and_tools() -> None:
    """扩展面板变更后：重载 Skill 插件 + 工具注册表。"""
    pm = get_plugin_manager()
    pm.load_skill_plugins()
    from agent.tool_registry import reset_tool_registry

    reset_tool_registry()


_PM: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _PM
    if _PM is None:
        _PM = PluginManager()
    return _PM


def reset_plugin_manager() -> None:
    global _PM
    if _PM is not None:
        for p in list(_PM.all()):
            try:
                p.on_unload()
            except Exception:
                pass
    _PM = None
