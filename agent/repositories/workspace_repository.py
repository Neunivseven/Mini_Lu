"""工作区根目录配置端口；实现委托 file_workspace。"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class WorkspaceRepository:
    """隔离 workspace.yaml 与路径解析。"""

    def list_roots(self) -> list[str]:
        from agent import file_workspace as fw

        return fw.list_user_roots()

    def get_active_root(self) -> Path | None:
        from agent import file_workspace as fw

        return fw.get_active_root()

    def add_root(self, path: str | Path, *, set_active: bool = True) -> Path:
        from agent import file_workspace as fw

        return fw.add_workspace_root(path, set_active=set_active)

    def remove_root(self, path: str | Path) -> bool:
        from agent import file_workspace as fw

        return fw.remove_workspace_root(path)

    def set_active(self, path: str | Path) -> Path:
        from agent import file_workspace as fw

        return fw.set_active_workspace(path)

    def clear_active(self) -> None:
        from agent import file_workspace as fw

        fw.clear_active_workspace()

    def status_text(self) -> str:
        from agent import file_workspace as fw

        return fw.format_workspace_status()

    def resolve(self, file_path: str, *, for_write: bool = False) -> Path:
        from agent import file_workspace as fw

        return fw.resolve_workspace_path(file_path, for_write=for_write)

    def save_backup(self, path: Path, content: str) -> Path | None:
        from agent import file_workspace as fw

        return fw.save_backup(path, content)


_REPO: WorkspaceRepository | None = None


def get_workspace_repository() -> WorkspaceRepository:
    global _REPO
    if _REPO is None:
        _REPO = WorkspaceRepository()
    return _REPO
