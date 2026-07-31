"""外部 MCP：配置加载 + 工具拉取 + 热插拔缓存。"""
from __future__ import annotations

import asyncio
import copy
import concurrent.futures
import logging
import threading
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import config_read_path
from agent.util_merge import deep_merge as _deep_merge

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_cache_tools: list[Any] | None = None
_cache_status: dict[str, Any] = {
    "enabled": False,
    "loaded": False,
    "servers": {},
    "tool_names": [],
    "error": None,
}




def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        logger.warning("读 MCP 配置失败 %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def load_mcp_config() -> dict[str, Any]:
    cfg = _read_yaml(config_read_path("mcp.yaml"))
    local = _read_yaml(config_read_path("mcp.local.yaml"))
    if local:
        cfg = _deep_merge(cfg, local)
    cfg.setdefault("enabled", False)
    cfg.setdefault("timeout_seconds", 30)
    cfg.setdefault("prefix_tools", True)
    cfg.setdefault("servers", {})
    if not isinstance(cfg["servers"], dict):
        cfg["servers"] = {}
    return cfg


def mcp_status() -> dict[str, Any]:
    with _lock:
        return copy.deepcopy(_cache_status)


def clear_mcp_cache() -> None:
    global _cache_tools
    with _lock:
        _cache_tools = None
        _cache_status["loaded"] = False
        _cache_status["tool_names"] = []
        _cache_status["error"] = None


def _server_connection(sid: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("enabled") is False:
        return None
    transport = str(raw.get("transport") or "stdio").strip().lower()
    if transport in ("stdio", "std"):
        command = str(raw.get("command") or "").strip()
        if not command:
            return None
        conn: dict[str, Any] = {
            "transport": "stdio",
            "command": command,
            "args": list(raw.get("args") or []),
        }
        env = raw.get("env")
        if isinstance(env, dict) and env:
            conn["env"] = {str(k): str(v) for k, v in env.items()}
        cwd = str(raw.get("cwd") or "").strip()
        if cwd:
            conn["cwd"] = cwd
        return conn
    if transport in ("sse",):
        url = str(raw.get("url") or "").strip()
        if not url:
            return None
        conn = {"transport": "sse", "url": url}
        headers = raw.get("headers")
        if isinstance(headers, dict) and headers:
            conn["headers"] = {str(k): str(v) for k, v in headers.items()}
        return conn
    if transport in ("streamable_http", "http", "streamable-http"):
        url = str(raw.get("url") or "").strip()
        if not url:
            return None
        conn = {"transport": "streamable_http", "url": url}
        headers = raw.get("headers")
        if isinstance(headers, dict) and headers:
            conn["headers"] = {str(k): str(v) for k, v in headers.items()}
        return conn
    logger.warning("MCP server [%s] 未知 transport=%s", sid, transport)
    return None


def build_connections(cfg: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    cfg = cfg or load_mcp_config()
    servers = cfg.get("servers") or {}
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(servers, dict):
        return out
    for sid, raw in servers.items():
        conn = _server_connection(str(sid), raw if isinstance(raw, dict) else {})
        if conn:
            out[str(sid)] = conn
    return out


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    def _runner():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_runner).result()


async def _fetch_tools_async(
    connections: dict[str, dict[str, Any]],
    *,
    prefix_tools: bool,
) -> tuple[list[Any], dict[str, Any]]:
    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
    except ImportError as e:
        raise RuntimeError(
            "未安装 langchain-mcp-adapters。请: pip install langchain-mcp-adapters mcp"
        ) from e

    per_server: dict[str, Any] = {}
    all_tools: list[Any] = []

    async def _one(sid: str, conn: dict[str, Any]) -> tuple[str, dict[str, Any], list[Any]]:
        try:
            client = MultiServerMCPClient({sid: conn})
            tools = await client.get_tools()
            names = []
            loaded: list[Any] = []
            for t in tools:
                if prefix_tools:
                    original = getattr(t, "name", "") or "tool"
                    if not original.startswith(f"mcp_{sid}_"):
                        try:
                            t.name = f"mcp_{sid}_{original}"
                        except Exception:
                            pass
                names.append(getattr(t, "name", "?"))
                loaded.append(t)
            return sid, {"ok": True, "tools": names, "error": None}, loaded
        except Exception as e:
            logger.warning("MCP server [%s] 加载失败: %s", sid, e)
            return sid, {"ok": False, "tools": [], "error": str(e)}, []

    if not connections:
        return [], {}

    results = await asyncio.gather(
        *(_one(sid, conn) for sid, conn in connections.items()),
        return_exceptions=False,
    )
    for sid, info, tools in results:
        per_server[sid] = info
        all_tools.extend(tools)

    return all_tools, per_server


def reload_mcp_tools(*, force: bool = True) -> dict[str, Any]:
    """重新从配置拉取 MCP 工具并写入缓存。返回 status。"""
    global _cache_tools
    with _lock:
        if not force and _cache_tools is not None and _cache_status.get("loaded"):
            return copy.deepcopy(_cache_status)
        if force:
            _cache_tools = None

    cfg = load_mcp_config()
    if not cfg.get("enabled"):
        with _lock:
            _cache_tools = []
            _cache_status.update(
                {
                    "enabled": False,
                    "loaded": True,
                    "servers": {},
                    "tool_names": [],
                    "error": None,
                }
            )
            return copy.deepcopy(_cache_status)

    connections = build_connections(cfg)
    if not connections:
        with _lock:
            _cache_tools = []
            _cache_status.update(
                {
                    "enabled": True,
                    "loaded": True,
                    "servers": {},
                    "tool_names": [],
                    "error": "enabled 但未配置可用 servers",
                }
            )
            return copy.deepcopy(_cache_status)

    try:
        tools, per_server = _run_async(
            _fetch_tools_async(
                connections,
                prefix_tools=bool(cfg.get("prefix_tools", True)),
            )
        )
        names = [getattr(t, "name", "?") for t in tools]
        with _lock:
            _cache_tools = list(tools)
            _cache_status.update(
                {
                    "enabled": True,
                    "loaded": True,
                    "servers": per_server,
                    "tool_names": names,
                    "error": None,
                }
            )
            return copy.deepcopy(_cache_status)
    except Exception as e:
        with _lock:
            _cache_tools = []
            _cache_status.update(
                {
                    "enabled": True,
                    "loaded": True,
                    "servers": {},
                    "tool_names": [],
                    "error": str(e),
                }
            )
            return copy.deepcopy(_cache_status)


def get_mcp_tools(*, refresh: bool = False) -> list[Any]:
    """供 default_tools / build_agent 合并；失败返回空列表。"""
    global _cache_tools
    with _lock:
        if _cache_tools is not None and not refresh:
            return list(_cache_tools)
    status = reload_mcp_tools(force=True)
    with _lock:
        tools = list(_cache_tools or [])
    if status.get("error") and not tools:
        logger.info("MCP 未加载工具: %s", status.get("error"))
    return tools


def format_mcp_report() -> str:
    st = mcp_status()
    if not st.get("loaded"):
        # 懒加载一次状态
        reload_mcp_tools(force=False)
        st = mcp_status()
    lines = [
        f"MCP enabled={st.get('enabled')} loaded={st.get('loaded')}",
        f"tools({len(st.get('tool_names') or [])}): "
        + (", ".join(st.get("tool_names") or []) or "（无）"),
    ]
    if st.get("error"):
        lines.append(f"error: {st['error']}")
    for sid, info in (st.get("servers") or {}).items():
        if info.get("ok"):
            lines.append(f"  OK {sid}: {len(info.get('tools') or [])} tools")
        else:
            lines.append(f"  FAIL {sid}: {info.get('error')}")
    cfg = load_mcp_config()
    configured = list((cfg.get("servers") or {}).keys())
    lines.append(f"config servers: {', '.join(configured) or '（空）'}")
    lines.append("热插拔: reload_mcp 或「扩展」面板 → 重新加载 MCP")
    return "\n".join(lines)
