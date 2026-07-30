"""
代码/文本文件工作区：路径白名单、危险路径拦截、read-before-write 状态。

对齐 Claude Code 的 filesystem 权限精简版。
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import app_dir

# path -> {content, mtime, ts}
_read_state: dict[str, dict[str, Any]] = {}

DANGEROUS_DIR_NAMES = {
    ".git",
    ".svn",
    ".hg",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".eggs",
    "site-packages",
}

DANGEROUS_FILE_NAMES = {
    ".gitconfig",
    ".bashrc",
    ".zshrc",
    ".bash_profile",
    ".profile",
    ".mcp.json",
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
}

# 应用 config/ 下敏感配置（含明文 API Key）；Agent 读写一律拒绝
_SECRET_NAME_SUFFIXES = (
    ".local.yaml",
    ".local.yml",
    ".local.json",
)
_SECRET_BASENAMES = {
    "models.local.yaml",
    "llm.local.yaml",
    "mcp.local.yaml",
    "skills.local.yaml",
    "metacoding.local.yaml",
    "apps.local.yaml",
    "command_trust.local.yaml",
}

# 绝对禁止写入的系统前缀（Windows / 通用）
_FORBIDDEN_PREFIXES_WIN = (
    r"c:\windows",
    r"c:\program files",
    r"c:\program files (x86)",
    r"c:\programdata",
)

# 控 token：默认少读、少回传；整文件读写仅作兜底
DEFAULT_READ_LINES = 80
MAX_READ_LINES = 200
MAX_READ_CHARS = 24_000
MAX_WRITE_CHARS = 120_000
MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB
# 已有文件超过该行数时禁止 write_file 整文件覆盖（须用 edit_file）
WRITE_EXISTING_MAX_LINES = 40
# focus 命中时，命中行前后各取多少行
FOCUS_CONTEXT_LINES = 25


def _under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _workspace_config_path() -> Path:
    p = app_dir() / "config" / "workspace.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _normalize_root(raw: str | Path) -> Path | None:
    p = Path(str(raw)).expanduser()
    if not p.is_absolute():
        p = (app_dir() / p).resolve()
    else:
        p = p.resolve()
    if not p.exists() or not p.is_dir():
        return None
    return p


def _load_workspace_config() -> dict[str, Any]:
    path = _workspace_config_path()
    if not path.exists():
        return {"roots": [], "active": ""}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"roots": [], "active": ""}
    if not isinstance(raw, dict):
        return {"roots": [], "active": ""}
    roots: list[str] = []
    for item in raw.get("roots") or []:
        n = _normalize_root(item)
        if n is None:
            continue
        s = str(n)
        if s not in roots:
            roots.append(s)
    active = str(raw.get("active") or "").strip()
    if active:
        an = _normalize_root(active)
        active = str(an) if an is not None else ""
        if active and active not in roots:
            roots.append(active)
    return {"roots": roots, "active": active}


def _save_workspace_config(data: dict[str, Any]) -> None:
    payload = {
        "roots": list(data.get("roots") or []),
        "active": str(data.get("active") or ""),
    }
    _workspace_config_path().write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def load_workspace_roots() -> list[Path]:
    """允许读写的根目录：app_dir + 用户配置的 roots（UI / yaml）。"""
    roots: list[Path] = [app_dir().resolve()]
    cfg = _load_workspace_config()
    for item in cfg.get("roots") or []:
        n = _normalize_root(item)
        if n is not None and n not in roots:
            roots.append(n)
    return roots


def list_user_roots() -> list[str]:
    """用户添加的根（不含桌宠安装目录）。"""
    return list(_load_workspace_config().get("roots") or [])


def get_active_root() -> Path | None:
    """当前活动项目根目录。"""
    active = (_load_workspace_config().get("active") or "").strip()
    if not active:
        return None
    return _normalize_root(active)


def format_workspace_status() -> str:
    active = get_active_root()
    roots = load_workspace_roots()
    lines = [
        f"当前项目: {active if active else '（未设置；相对路径默认相对应用目录）'}",
        "可访问根目录:",
    ]
    app = app_dir().resolve()
    for r in roots:
        mark = " ★" if active and r.resolve() == active.resolve() else ""
        builtin = "（应用目录）" if r.resolve() == app else ""
        lines.append(f"- {r}{mark}{builtin}")
    lines.append("切换方式：聊天栏「📁」或右键「工作区…」。")
    return "\n".join(lines)


def add_workspace_root(path: str | Path, *, set_active: bool = True) -> Path:
    """添加文件夹到白名单；可选设为当前项目。"""
    n = _normalize_root(path)
    if n is None:
        raise ValueError(f"不是有效文件夹: {path}")
    low = str(n).lower()
    for pref in _FORBIDDEN_PREFIXES_WIN:
        if low.startswith(pref):
            raise PermissionError(f"禁止将系统目录加入工作区: {n}")
    cfg = _load_workspace_config()
    s = str(n)
    if s not in cfg["roots"] and n.resolve() != app_dir().resolve():
        cfg["roots"].append(s)
    if set_active:
        cfg["active"] = s
    _save_workspace_config(cfg)
    return n


def remove_workspace_root(path: str | Path) -> bool:
    """从白名单移除（不能移除桌宠目录）。"""
    n = _normalize_root(path)
    if n is None:
        raise ValueError(f"路径无效或不是文件夹: {path}")
    if n.resolve() == app_dir().resolve():
        raise PermissionError("应用安装目录为内置根，不能移除")
    cfg = _load_workspace_config()
    before = len(cfg["roots"])
    cfg["roots"] = [r for r in cfg["roots"] if Path(r).resolve() != n.resolve()]
    active = cfg.get("active") or ""
    if active and Path(active).resolve() == n.resolve():
        cfg["active"] = cfg["roots"][0] if cfg["roots"] else ""
    _save_workspace_config(cfg)
    return len(cfg["roots"]) < before


def set_active_workspace(path: str | Path) -> Path:
    """设为当前项目；若尚未在白名单则一并添加。"""
    return add_workspace_root(path, set_active=True)


def clear_active_workspace() -> None:
    cfg = _load_workspace_config()
    cfg["active"] = ""
    _save_workspace_config(cfg)


def backups_dir() -> Path:
    p = app_dir() / "data" / "file_backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _is_app_config_path(path: Path) -> bool:
    """是否位于桌宠安装目录下的 config/（含 API Key 等本地配置）。"""
    try:
        cfg_root = (app_dir() / "config").resolve()
        path.resolve().relative_to(cfg_root)
        return True
    except ValueError:
        return False


def _is_secret_path(path: Path) -> bool:
    """敏感文件：*.local.yaml、.env、以及应用 config/ 下全部文件。"""
    name = path.name
    low = name.lower()
    if low in {n.lower() for n in DANGEROUS_FILE_NAMES}:
        return True
    if low in {n.lower() for n in _SECRET_BASENAMES}:
        return True
    for suf in _SECRET_NAME_SUFFIXES:
        if low.endswith(suf):
            return True
    if low.startswith(".env"):
        return True
    if _is_app_config_path(path):
        return True
    return False


def resolve_workspace_path(file_path: str, *, for_write: bool = False) -> Path:
    """解析并校验路径；非法则抛 PermissionError / ValueError。"""
    raw = (file_path or "").strip()
    if not raw:
        raise ValueError("路径为空")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        base = get_active_root() or app_dir()
        p = (base / raw).resolve()
    else:
        p = p.resolve()

    low = str(p).lower()
    for pref in _FORBIDDEN_PREFIXES_WIN:
        if low.startswith(pref):
            raise PermissionError(f"禁止访问系统目录: {p}")

    if _is_secret_path(p):
        raise PermissionError(
            f"禁止访问敏感配置/密钥文件: {p.name}\n"
            "（API Key 与 *.local.yaml、应用 config/ 对 Agent 工具不可见）"
        )

    if p.name.lower() in {n.lower() for n in DANGEROUS_FILE_NAMES}:
        raise PermissionError(f"禁止操作敏感文件: {p.name}")

    dangerous_dirs = {d.lower() for d in DANGEROUS_DIR_NAMES}
    for part in p.parts:
        if part.lower() not in dangerous_dirs:
            continue
        if for_write:
            raise PermissionError(f"禁止写入受保护目录内的路径: {p}")
        # 读：版本库元数据仍禁止；依赖目录可读但写入已拦
        if part.lower() in {".git", ".svn", ".hg"}:
            raise PermissionError(f"禁止访问版本库内部: {p}")

    roots = load_workspace_roots()
    if not any(_under(p, r) for r in roots):
        roots_s = ", ".join(str(r) for r in roots)
        raise PermissionError(
            f"路径不在工作区内: {p}\n允许的根目录: {roots_s}\n"
            "请用聊天栏「📁」或右键「工作区…」打开/添加文件夹。"
        )
    return p


def mark_read(path: Path, content: str) -> None:
    key = str(path.resolve())
    mtime = path.stat().st_mtime if path.exists() else 0.0
    _read_state[key] = {
        "content": content,
        "mtime": mtime,
        "ts": time.time(),
    }


def require_fresh_read(path: Path, current_content: str) -> None:
    """Edit/Write 前：须先 Read，且磁盘未在外部被改。"""
    key = str(path.resolve())
    prev = _read_state.get(key)
    if prev is None:
        raise RuntimeError("请先用 read_file 读取该文件，再 edit_file / write_file。")
    if not path.exists():
        return
    mtime = path.stat().st_mtime
    if mtime > float(prev.get("mtime") or 0) + 1e-6:
        if prev.get("content") != current_content:
            raise RuntimeError(
                "文件自上次 read_file 后已被外部修改，请重新 read_file 后再编辑。"
            )


def save_backup(path: Path, content: str) -> Path | None:
    if not content and not path.exists():
        return None
    digest = hashlib.sha1(str(path).encode("utf-8", errors="replace")).hexdigest()[:10]
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = backups_dir() / f"{stamp}_{digest}_{path.name}.bak"
    dest.write_text(content, encoding="utf-8", errors="replace")
    return dest


def numbered_slice(text: str, offset: int = 1, limit: int = 0) -> str:
    """1-based offset；limit<=0 时用 DEFAULT_READ_LINES（不再默认读到文件末尾）。"""
    lines = text.splitlines()
    if not lines:
        return "（空文件）"
    start = max(0, int(offset) - 1) if offset else 0
    if limit and int(limit) > 0:
        n = min(int(limit), MAX_READ_LINES)
    else:
        n = DEFAULT_READ_LINES
    end = min(len(lines), start + n)
    chunk = lines[start:end]
    out = []
    for i, line in enumerate(chunk):
        out.append(f"{start + i + 1}|{line}")
    if start > 0:
        out.insert(0, f"…上方省略 {start} 行")
    omitted = len(lines) - end
    if omitted > 0:
        out.append(
            f"…下方另有 {omitted} 行未显示（共 {len(lines)} 行）。"
            "请用 offset/limit 或 focus=符号名 继续读关键段，勿整文件读取。"
        )
    return "\n".join(out)


def find_focus_window(
    text: str,
    focus: str,
    *,
    context: int = FOCUS_CONTEXT_LINES,
) -> tuple[int, int, int] | None:
    """
    按子串/正则找首个命中行，返回 (hit_line_1based, start_1based, end_exclusive_1based)。
    """
    focus = (focus or "").strip()
    if not focus:
        return None
    lines = text.splitlines()
    try:
        rx = re.compile(focus)
        use_re = True
    except re.error:
        rx = None
        use_re = False
    hit = -1
    for i, line in enumerate(lines):
        if use_re and rx is not None and rx.search(line):
            hit = i
            break
        if not use_re and focus in line:
            hit = i
            break
    if hit < 0:
        return None
    ctx = max(0, int(context))
    start = max(0, hit - ctx)
    end = min(len(lines), hit + ctx + 1)
    return hit + 1, start + 1, end


_OUTLINE_PATTERNS = [
    # Python
    re.compile(r"^(\s*)(def|async\s+def|class)\s+(\w+)"),
    # JS/TS
    re.compile(
        r"^(\s*)(export\s+)?(async\s+)?(function\*?|class)\s+(\w+)"
    ),
    re.compile(r"^(\s*)(export\s+)?(const|let|var)\s+(\w+)\s*=\s*(async\s*)?\("),
    # Go / Rust-ish
    re.compile(r"^(\s*)(func|fn|impl|struct|type)\s+"),
]


def build_outline(text: str, *, max_items: int = 80) -> str:
    """提取定义行大纲（低 token 导览），不含函数体。"""
    lines = text.splitlines()
    if not lines:
        return "（空文件）"
    items: list[str] = []
    for i, line in enumerate(lines, 1):
        s = line.rstrip()
        if not s or s.lstrip().startswith(("#", "//", "*", "/*")):
            continue
        for pat in _OUTLINE_PATTERNS:
            if pat.search(s):
                items.append(f"{i}|{s[:160]}")
                break
        if len(items) >= max_items:
            items.append(f"…大纲已截断（共 {len(lines)} 行）")
            break
    if not items:
        # 无结构时只给头尾摘要
        head_n = min(15, len(lines))
        tail_n = min(10, max(0, len(lines) - head_n))
        out = [f"（未识别到 def/class 等结构，共 {len(lines)} 行；头尾摘要）"]
        for i in range(head_n):
            out.append(f"{i+1}|{lines[i][:160]}")
        if tail_n:
            out.append("…")
            for i in range(len(lines) - tail_n, len(lines)):
                out.append(f"{i+1}|{lines[i][:160]}")
        return "\n".join(out)
    return f"共 {len(lines)} 行，结构大纲（仅定义行）：\n" + "\n".join(items)


def ensure_text_size(path: Path) -> None:
    if path.exists() and path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError(f"文件过大（>{MAX_FILE_BYTES} 字节），拒绝操作: {path}")
