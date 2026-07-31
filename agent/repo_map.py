"""项目符号地图（repo map）：低 token 的全局代码视野。

一行一个文件：相对路径 + 顶层类/函数名列表（TSA 解析，符号过多则截断）。
供 Agent 在接手任务时一眼看到「东西都在哪」，减少盲目 glob/grep 轮次。
带 TTL 缓存；.gitignore 中的纯目录名条目会被跳过。
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".mypy_cache", ".ruff_cache",
    "backups", "__snapshots__",
}
_SOURCE_EXTS = {
    ".py", ".c", ".h", ".hh", ".hpp", ".hxx", ".cc", ".cpp", ".cxx",
    ".js", ".mjs", ".ts", ".tsx", ".java", ".go", ".rs", ".vue",
}
_STRUCTURAL = {"class", "struct", "function", "method", "interface", "enum", "namespace"}

_MAX_SCAN_FILES = 2000
_SYMBOLS_PER_FILE = 8
_TTL_SECONDS = 120.0

_cache: dict[str, tuple[float, str]] = {}


def invalidate_cache() -> None:
    _cache.clear()


def _gitignore_dir_names(root: Path) -> set[str]:
    """取 .gitignore 中的纯目录名条目（不含通配/路径的简单模式）。"""
    out: set[str] = set()
    gi = root / ".gitignore"
    if not gi.is_file():
        return out
    try:
        for ln in gi.read_text(encoding="utf-8", errors="replace").splitlines():
            s = ln.strip()
            if not s or s.startswith(("#", "!")):
                continue
            s = s.rstrip("/")
            if not s or any(ch in s for ch in "/*?["):
                continue
            out.add(s)
    except Exception:
        pass
    return out


def _iter_source_files(root: Path) -> list[Path]:
    skip = _SKIP_DIRS | _gitignore_dir_names(root)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in skip and not d.startswith(".")
        )
        for fn in sorted(filenames):
            if Path(fn).suffix.lower() in _SOURCE_EXTS:
                files.append(Path(dirpath) / fn)
                if len(files) >= _MAX_SCAN_FILES:
                    return files
    return files


def _file_symbols(p: Path) -> list[str]:
    try:
        from agent.code_intel import list_symbols

        elems = list_symbols(p, kinds="", limit=120)
    except Exception:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for e in elems:
        et = (e.get("type") or "").lower()
        if et not in _STRUCTURAL:
            continue
        name = str(e.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        prefix = "C " if et in ("class", "struct", "interface") else ""
        out.append(f"{prefix}{name}")
        if len(out) >= _SYMBOLS_PER_FILE + 1:
            break
    if len(out) > _SYMBOLS_PER_FILE:
        out = out[:_SYMBOLS_PER_FILE] + ["…"]
    return out


def build_repo_map(root: Path, *, max_files: int = 80, max_chars: int = 7000) -> str:
    key = f"{root.resolve()}|{max_files}|{max_chars}"
    hit = _cache.get(key)
    now = time.time()
    if hit and now - hit[0] < _TTL_SECONDS:
        return hit[1]

    files = _iter_source_files(root)
    total = len(files)
    lines: list[str] = [
        f"项目地图 @ {root}（源文件 {total} 个；C=类/结构体，其余为函数）"
    ]
    used = len(lines[0])
    shown = 0
    for p in files:
        if shown >= max_files or used > max_chars:
            break
        try:
            rel = p.relative_to(root)
        except Exception:
            rel = p
        syms = _file_symbols(p)
        row = f"{rel} — {', '.join(syms)}" if syms else str(rel)
        lines.append(row)
        used += len(row) + 1
        shown += 1
    rest = total - shown
    if rest > 0:
        lines.append(f"…其余 {rest} 个文件未展开（glob_files / read_outline 查看）")

    text = "\n".join(lines)
    _cache[key] = (now, text)
    return text
