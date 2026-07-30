"""写操作策略：基于 ToolRegistry access=read|write。

文件写 → edit_staging；终端 → command_approval。
本模块提供统一查询与备份入口，避免工具侧散落判断。
"""
from __future__ import annotations

from pathlib import Path


def requires_write_guard(tool_name: str) -> bool:
    """是否应按写工具处理（审核 / 备份 / 失效读缓存）。"""
    name = (tool_name or "").strip()
    if not name:
        return True
    try:
        from agent.tool_registry import get_tool_registry

        return get_tool_registry().access_of(name) == "write"
    except Exception:
        return True


def backup_before_write(path: Path, content: str) -> Path | None:
    """写前备份：委托 WorkspaceRepository。"""
    from agent.repositories import get_workspace_repository

    return get_workspace_repository().save_backup(path, content)


def assert_write_tool(tool_name: str) -> None:
    """调试断言：读工具不应进入审批/备份路径。"""
    if not requires_write_guard(tool_name):
        raise AssertionError(f"只读工具不应进入写护栏: {tool_name}")
