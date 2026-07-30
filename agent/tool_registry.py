"""可插拔工具注册表：内置 / MCP / Skills 等统一入口；支持 read/write 访问分类。"""
from __future__ import annotations

from threading import RLock
from typing import Any, Callable, Iterable, Literal

ToolLike = Any  # LangChain BaseTool / StructuredTool
ToolAccess = Literal["read", "write"]

# 工具对象上挂的元数据属性名
ACCESS_ATTR = "_mini_lu_access"


def tool_access(tool_fn: ToolLike) -> ToolAccess:
    raw = getattr(tool_fn, ACCESS_ATTR, None)
    if raw in ("read", "write"):
        return raw  # type: ignore[return-value]
    return "write"  # 未标注默认按写操作（更安全：走审核/备份策略）


def is_write_tool(tool_fn: ToolLike | str) -> bool:
    if isinstance(tool_fn, str):
        # 仅名字时无法判断 → 保守视为写
        return True
    return tool_access(tool_fn) == "write"


def is_read_tool(tool_fn: ToolLike) -> bool:
    return tool_access(tool_fn) == "read"


class ToolRegistry:
    """按类别注册工具；支持排除名、按类/访问性取出、MCP 热插拔。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self._by_category: dict[str, list[ToolLike]] = {}
        self._providers: list[tuple[str, Callable[[], Iterable[ToolLike]]]] = []
        self._access: dict[str, ToolAccess] = {}  # tool name → access

    def clear(self) -> None:
        with self._lock:
            self._by_category.clear()
            self._providers.clear()
            self._access.clear()

    def register(
        self,
        category: str,
        tool_fn: ToolLike,
        *,
        access: ToolAccess | None = None,
    ) -> None:
        cat = (category or "misc").strip() or "misc"
        acc: ToolAccess = access or tool_access(tool_fn)
        try:
            setattr(tool_fn, ACCESS_ATTR, acc)
        except Exception:
            pass
        with self._lock:
            bucket = self._by_category.setdefault(cat, [])
            name = getattr(tool_fn, "name", None)
            if name:
                bucket[:] = [t for t in bucket if getattr(t, "name", None) != name]
                self._access[str(name)] = acc
            bucket.append(tool_fn)

    def register_many(
        self,
        category: str,
        tools: Iterable[ToolLike],
        *,
        access: ToolAccess | None = None,
    ) -> None:
        for t in tools:
            self.register(category, t, access=access)

    def add_provider(
        self, category: str, factory: Callable[[], Iterable[ToolLike]]
    ) -> None:
        """延迟提供工具（如 MCP）：每次 all_tools 时调用 factory。"""
        with self._lock:
            self._providers.append(((category or "mcp").strip() or "mcp", factory))

    def categories(self) -> list[str]:
        with self._lock:
            cats = set(self._by_category)
            cats.update(c for c, _ in self._providers)
            return sorted(cats)

    def access_of(self, tool_name: str) -> ToolAccess:
        with self._lock:
            return self._access.get(tool_name, "write")

    def tools_by_category(self, category: str) -> list[ToolLike]:
        cat = (category or "").strip()
        with self._lock:
            out = list(self._by_category.get(cat, []))
            for c, factory in self._providers:
                if c != cat:
                    continue
                try:
                    out.extend(list(factory() or []))
                except Exception:
                    pass
            return self._dedupe(out)

    def tools_by_access(self, access: ToolAccess) -> list[ToolLike]:
        want = "read" if access == "read" else "write"
        out = []
        for t in self.all_tools():
            name = getattr(t, "name", "") or ""
            with self._lock:
                acc = self._access.get(name) or tool_access(t)
            if acc == want:
                out.append(t)
        return out

    def all_tools(self, exclude: set[str] | None = None) -> list[ToolLike]:
        with self._lock:
            out: list[ToolLike] = []
            for tools in self._by_category.values():
                out.extend(tools)
            for _cat, factory in self._providers:
                try:
                    out.extend(list(factory() or []))
                except Exception:
                    pass
            out = self._dedupe(out)
        if exclude:
            out = [t for t in out if getattr(t, "name", "") not in exclude]
        return out

    @staticmethod
    def _dedupe(tools: list[ToolLike]) -> list[ToolLike]:
        seen: set[str] = set()
        result: list[ToolLike] = []
        for t in tools:
            name = getattr(t, "name", "") or ""
            key = name or f"id:{id(t)}"
            if key in seen:
                continue
            seen.add(key)
            result.append(t)
        return result


_GLOBAL: ToolRegistry | None = None
_BOOTSTRAPPED = False


def get_tool_registry(*, reload: bool = False) -> ToolRegistry:
    global _GLOBAL, _BOOTSTRAPPED
    if reload or _GLOBAL is None:
        _GLOBAL = ToolRegistry()
        _BOOTSTRAPPED = False
    if not _BOOTSTRAPPED:
        _bootstrap_builtin(_GLOBAL)
        _BOOTSTRAPPED = True
    return _GLOBAL


def reset_tool_registry() -> None:
    """测试 / 重载 MCP 后可清空并重新引导。"""
    global _GLOBAL, _BOOTSTRAPPED
    _GLOBAL = None
    _BOOTSTRAPPED = False


def _bootstrap_builtin(reg: ToolRegistry) -> None:
    """注册内置工具与 MCP 提供器（避免 tools↔registry 循环：延迟 import）。"""
    from agent import tools as tools_mod

    tools_mod.register_builtin_tools(reg)

    def _mcp_factory():
        try:
            from agent.mcp_client import get_mcp_tools

            return get_mcp_tools()
        except Exception:
            return []

    reg.add_provider("mcp", _mcp_factory)

    try:
        from agent.plugin import get_plugin_manager

        get_plugin_manager().register_all_tools(reg)
    except Exception:
        pass
