"""
扫描本机已安装软件并按名称相关性启动。

快路径优先（where / App Paths / 开始菜单）；全量索引缓存 + 内存缓存；
过期时 stale-while-revalidate，不在 open_app 热路径上强制全盘重扫。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from agent.llm_client import data_dir

# 仅作「查询词扩展」，不是安装路径
_NICKNAMES: dict[str, list[str]] = {
    "qq": ["qq", "qqnt", "腾讯qq"],
    "微信": ["微信", "wechat", "weixin"],
    "wechat": ["微信", "wechat", "weixin"],
    "钉钉": ["钉钉", "dingtalk"],
    "chrome": ["chrome", "谷歌浏览器", "google chrome"],
    "谷歌浏览器": ["chrome", "谷歌浏览器", "google chrome"],
    "edge": ["edge", "microsoft edge", "浏览器"],
    "浏览器": ["edge", "chrome", "浏览器", "msedge"],
    "vscode": ["code", "vscode", "visual studio code"],
    "记事本": ["notepad", "记事本"],
    "计算器": ["calc", "calculator", "计算器"],
    "word": ["winword", "word", "microsoft word"],
    "excel": ["excel", "microsoft excel"],
    "ppt": ["powerpnt", "powerpoint", "ppt"],
    "powerpoint": ["powerpnt", "powerpoint", "ppt"],
}

# where / App Paths 常用可执行名（由昵称映射）
_WHERE_CANDIDATES: dict[str, list[str]] = {
    "notepad": ["notepad.exe"],
    "记事本": ["notepad.exe"],
    "calc": ["calc.exe"],
    "计算器": ["calc.exe"],
    "mspaint": ["mspaint.exe"],
    "画图": ["mspaint.exe"],
    "explorer": ["explorer.exe"],
    "资源管理器": ["explorer.exe"],
    "chrome": ["chrome.exe"],
    "谷歌浏览器": ["chrome.exe"],
    "edge": ["msedge.exe"],
    "msedge": ["msedge.exe"],
    "code": ["code.cmd", "code.exe"],
    "vscode": ["code.cmd", "code.exe"],
    "winword": ["winword.exe"],
    "word": ["winword.exe"],
    "excel": ["excel.exe"],
    "powerpnt": ["powerpnt.exe"],
    "ppt": ["powerpnt.exe"],
    "powerpoint": ["powerpnt.exe"],
}

_SKIP_EXE_RE = re.compile(
    r"(uninstall|uninst|setup|install|update|updater|crash|helper|repair|"
    r"vcredist|redistributable|dotnet|runtime|overlay|cefsharp|reporter|"
    r"crashpad|notification_helper|elevation_service|chrmstp)",
    re.I,
)

_CACHE_TTL_SEC = 6 * 3600  # 索引缓存 6 小时
_MAX_WALK_DEPTH = 3
_MAX_EXES_PER_ROOT = 500
_FAST_SCORE_MIN = 120  # 快路径足够自信才直接启动
_HIT_SCORE_MIN = 50

_MEM_LOCK = threading.RLock()
_MEM_INDEX: list[AppEntry] | None = None
_MEM_SCANNED_AT: float = 0.0
_REFRESH_LOCK = threading.Lock()
_REFRESH_THREAD: threading.Thread | None = None


@dataclass
class AppEntry:
    name: str
    path: str
    source: str  # start_menu | programs | registry | system | app_paths | where

    @property
    def path_obj(self) -> Path:
        return Path(self.path)


def _cache_path() -> Path:
    p = data_dir() / "apps_index.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _normalize_query(name: str) -> str:
    q = (name or "").strip()
    for prefix in ("打开", "启动", "运行", "帮我打开", "请打开", "open "):
        if q.lower().startswith(prefix) or q.startswith(prefix):
            q = q[len(prefix) :].strip()
    return q


def _query_tokens(name: str) -> list[str]:
    q = _normalize_query(name)
    if not q:
        return []
    tokens = {q, q.lower()}
    key = q.lower()
    if key in _NICKNAMES:
        tokens.update(_NICKNAMES[key])
    if q in _NICKNAMES:
        tokens.update(_NICKNAMES[q])
    tokens.add(re.sub(r"[\s_\-]+", "", q.lower()))
    return [t for t in tokens if t]


def _fixed_drives() -> list[Path]:
    drives: list[Path] = []
    if sys.platform.startswith("win"):
        for letter in string.ascii_uppercase:
            root = Path(f"{letter}:/")
            try:
                if root.exists():
                    drives.append(root)
            except Exception:
                continue
    else:
        drives.append(Path("/"))
    return drives


def _program_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform.startswith("win"):
        for drive in _fixed_drives():
            for name in (
                "Program Files",
                "Program Files (x86)",
                "Programs",
                "Software",
                "Soft",
                "App",
                "Apps",
                "软件",
                "应用",
                "我的软件",
            ):
                p = drive / name
                if p.is_dir():
                    roots.append(p)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            prog = Path(local) / "Programs"
            if prog.is_dir():
                roots.append(prog)
            py = prog / "Python"
            if py.is_dir():
                roots.append(py)
        return roots

    # Linux / macOS：不扫整盘，只索引常见可执行目录（轻量）
    for p in (
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path.home() / ".local" / "bin",
        Path("/snap/bin"),
    ):
        if p.is_dir():
            roots.append(p)
    return roots


def _desktop_dirs() -> list[Path]:
    dirs: list[Path] = []
    if sys.platform.startswith("win"):
        return dirs
    for d in (
        Path.home() / ".local" / "share" / "applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ):
        if d.is_dir():
            dirs.append(d)
    xdg = os.environ.get("XDG_DATA_DIRS") or ""
    for part in xdg.split(":"):
        if not part.strip():
            continue
        d = Path(part.strip()) / "applications"
        if d.is_dir() and d not in dirs:
            dirs.append(d)
    return dirs


def _parse_desktop_file(path: Path) -> AppEntry | None:
    """解析 .desktop → Name + 可启动目标（优先保留 .desktop 路径便于 gio launch）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    name = ""
    name_zh = ""
    no_display = False
    hidden = False
    typ = ""
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("["):
            if s.startswith("[") and s != "[Desktop Entry]" and name:
                break
            continue
        if s.startswith("Name[zh_CN]=") or s.startswith("Name[zh]="):
            name_zh = s.split("=", 1)[1].strip()
        elif s.startswith("Name=") and not name:
            name = s.split("=", 1)[1].strip()
        elif s.startswith("Type="):
            typ = s.split("=", 1)[1].strip()
        elif s.startswith("NoDisplay="):
            no_display = s.split("=", 1)[1].strip().lower() in ("true", "1")
        elif s.startswith("Hidden="):
            hidden = s.split("=", 1)[1].strip().lower() in ("true", "1")
    if no_display or hidden:
        return None
    if typ and typ != "Application":
        return None
    display = name_zh or name or path.stem
    if not display:
        return None
    return AppEntry(name=display, path=str(path.resolve()), source="desktop")


def _scan_desktop_apps() -> list[AppEntry]:
    items: list[AppEntry] = []
    seen: set[str] = set()
    for d in _desktop_dirs():
        try:
            files = list(d.glob("*.desktop"))
        except Exception:
            continue
        for f in files:
            key = str(f.resolve()).lower()
            if key in seen:
                continue
            entry = _parse_desktop_file(f)
            if not entry:
                continue
            seen.add(key)
            items.append(entry)
            # 也用桌面文件 stem 作为别名（如 code.desktop → code）
            stem = f.stem
            if stem and stem.lower() != entry.name.lower():
                items.append(
                    AppEntry(name=stem, path=entry.path, source="desktop")
                )
    return items


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("PROGRAMDATA", "APPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        d = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        if d.is_dir():
            dirs.append(d)
    return dirs

def _walk_exes(root: Path, max_depth: int = _MAX_WALK_DEPTH) -> Iterable[Path]:
    root = root.resolve()
    count = 0
    skip = {
        "windows",
        "winsxs",
        "system32",
        "syswow64",
        "node_modules",
        "$recycle.bin",
        "temp",
        "tmp",
        "cache",
        "logs",
        ".git",
        "packages",
        "package cache",
        "installer",
        "installers",
    }
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            rel = Path(dirpath).relative_to(root)
            depth = len(rel.parts)
            if depth >= max_depth:
                dirnames.clear()
            dirnames[:] = [d for d in dirnames if d.lower() not in skip]
            for fn in filenames:
                if not fn.lower().endswith(".exe"):
                    continue
                if _SKIP_EXE_RE.search(fn):
                    continue
                yield Path(dirpath) / fn
                count += 1
                if count >= _MAX_EXES_PER_ROOT:
                    return
    except Exception:
        return


def _scan_start_menu() -> list[AppEntry]:
    items: list[AppEntry] = []
    for root in _start_menu_dirs():
        try:
            for lnk in root.rglob("*.lnk"):
                stem = lnk.stem.strip()
                if not stem or _SKIP_EXE_RE.search(stem):
                    continue
                items.append(AppEntry(name=stem, path=str(lnk), source="start_menu"))
        except Exception:
            continue
    return items


def _top_level_exe(folder: Path, hint: str) -> Path | None:
    """仅看安装目录顶层 *.exe，不做深 rglob。"""
    tokens = _query_tokens(hint)
    ranked: list[tuple[int, Path]] = []
    try:
        cands = [
            p
            for p in folder.glob("*.exe")
            if p.is_file() and not _SKIP_EXE_RE.search(p.name)
        ]
    except Exception:
        return None
    if not cands:
        return None
    for exe in cands:
        score = _score_path(exe, tokens, display_name=hint)
        ranked.append((score, exe))
    ranked.sort(key=lambda x: (-x[0], -x[1].stat().st_size if x[1].exists() else 0))
    if ranked and ranked[0][0] > 0:
        return ranked[0][1]
    cands.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return cands[0]


def _scan_registry() -> list[AppEntry]:
    """卸载注册表：只用 DisplayIcon + InstallLocation 顶层 exe（无深扫）。"""
    if not sys.platform.startswith("win"):
        return []
    items: list[AppEntry] = []
    try:
        import winreg
    except ImportError:
        return []

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as root:
                for i in range(0, winreg.QueryInfoKey(root)[0]):
                    try:
                        sk_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, sk_name) as sk:

                            def _get(n: str) -> str:
                                try:
                                    return str(winreg.QueryValueEx(sk, n)[0] or "")
                                except OSError:
                                    return ""

                            display = _get("DisplayName").strip()
                            if not display:
                                continue
                            icon = _get("DisplayIcon").split(",")[0].strip().strip('"')
                            loc = _get("InstallLocation").strip().strip('"')
                            path = ""
                            if icon.lower().endswith(".exe"):
                                try:
                                    if Path(icon).is_file():
                                        path = icon
                                except Exception:
                                    path = ""
                            if not path and loc:
                                folder = Path(loc)
                                if folder.is_dir():
                                    hit = _top_level_exe(folder, display)
                                    if hit:
                                        path = str(hit)
                            if path:
                                items.append(
                                    AppEntry(name=display, path=path, source="registry")
                                )
                    except OSError:
                        continue
        except OSError:
            continue
    return items


def _scan_app_paths() -> list[AppEntry]:
    """HKLM/HKCU App Paths — 极轻，适合快路径与索引补充。"""
    if not sys.platform.startswith("win"):
        return []
    items: list[AppEntry] = []
    try:
        import winreg
    except ImportError:
        return []
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, sub in keys:
        try:
            with winreg.OpenKey(hive, sub) as root:
                for i in range(0, winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, name) as sk:
                            try:
                                raw = str(winreg.QueryValueEx(sk, None)[0] or "")
                            except OSError:
                                raw = ""
                            raw = raw.strip().strip('"')
                            if not raw:
                                continue
                            p = Path(os.path.expandvars(raw))
                            if p.is_file():
                                stem = Path(name).stem if name.lower().endswith(".exe") else name
                                items.append(
                                    AppEntry(
                                        name=stem or p.stem,
                                        path=str(p),
                                        source="app_paths",
                                    )
                                )
                                items.append(
                                    AppEntry(name=p.stem, path=str(p), source="app_paths")
                                )
                    except OSError:
                        continue
        except OSError:
            continue
    return items


def _scan_program_dirs() -> list[AppEntry]:
    items: list[AppEntry] = []
    seen: set[str] = set()
    for root in _program_roots():
        for exe in _walk_exes(root):
            key = str(exe).lower()
            if key in seen:
                continue
            seen.add(key)
            parent = exe.parent.name
            name = exe.stem
            items.append(AppEntry(name=name, path=str(exe), source="programs"))
            if parent and parent.lower() not in {name.lower(), "bin", "application", "app"}:
                items.append(AppEntry(name=parent, path=str(exe), source="programs"))
    return items


def _scan_system_basics() -> list[AppEntry]:
    items: list[AppEntry] = []
    if sys.platform.startswith("win"):
        for label, exe in (
            ("记事本", "notepad.exe"),
            ("计算器", "calc.exe"),
            ("画图", "mspaint.exe"),
            ("资源管理器", "explorer.exe"),
        ):
            hit = _where(exe)
            if hit:
                items.append(AppEntry(name=label, path=str(hit), source="system"))
                items.append(
                    AppEntry(name=exe.replace(".exe", ""), path=str(hit), source="system")
                )
        return items

    # Linux 常见命令
    for label, exe in (
        ("终端", "gnome-terminal"),
        ("终端", "x-terminal-emulator"),
        ("文件管理器", "nautilus"),
        ("文件管理器", "dolphin"),
        ("浏览器", "firefox"),
        ("浏览器", "google-chrome"),
        ("浏览器", "chromium-browser"),
        ("浏览器", "chromium"),
        ("vscode", "code"),
        ("计算器", "gnome-calculator"),
        ("文本编辑", "gedit"),
        ("文本编辑", "gnome-text-editor"),
    ):
        hit = _where(exe)
        if hit:
            items.append(AppEntry(name=label, path=str(hit), source="system"))
            items.append(AppEntry(name=exe, path=str(hit), source="system"))
    return items


def _where(exe_name: str) -> Path | None:
    name = (exe_name or "").strip()
    if not name:
        return None
    # 各平台通用：PATH 查找
    try:
        hit = shutil.which(name)
        if hit:
            p = Path(hit)
            if p.is_file():
                return p
    except Exception:
        pass
    if not sys.platform.startswith("win"):
        return None
    try:
        r = subprocess.run(
            ["where", name],
            capture_output=True,
            text=True,
            timeout=3,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0 and r.stdout.strip():
            p = Path(r.stdout.strip().splitlines()[0].strip())
            if p.is_file():
                return p
    except Exception:
        pass
    return None


def _score_path(path: Path, tokens: list[str], display_name: str = "") -> int:
    if not tokens:
        return 0
    stem = path.stem.lower()
    full = str(path).lower().replace("\\", "/")
    parts = [p.lower() for p in path.parts]
    dname = (display_name or "").lower()
    score = 0
    for tok in tokens:
        t = tok.lower()
        if not t:
            continue
        if stem == t:
            score += 160
        elif t in stem or stem in t:
            score += 70
        if dname and (dname == t):
            score += 120
        elif dname and (t in dname or dname in t):
            score += 90
        for part in parts[-4:]:
            if t == part:
                score += 60
            elif t in part or part in t:
                score += 35
        if t in full:
            score += 15
    if _SKIP_EXE_RE.search(path.name) or _SKIP_EXE_RE.search(dname):
        score -= 200
    if any(
        x in stem or x in dname
        for x in ("update", "helper", "crash", "setup", "卸载", "uninstall")
    ):
        score -= 120
    score -= min(len(parts), 12)
    return score


def _rank_entries(
    apps: list[AppEntry], tokens: list[str], *, limit: int
) -> list[tuple[int, AppEntry]]:
    ranked: list[tuple[int, AppEntry]] = []
    for a in apps:
        s1 = _score_path(a.path_obj, tokens, display_name=a.name)
        for tok in tokens:
            t = tok.lower()
            n = a.name.lower()
            if n == t:
                s1 += 100
            elif t in n or n in t:
                s1 += 55
        if s1 >= _HIT_SCORE_MIN:
            ranked.append((s1, a))
    best: dict[str, tuple[int, AppEntry]] = {}
    for s, a in ranked:
        k = a.path.lower()
        if k not in best or s > best[k][0]:
            best[k] = (s, a)
    out = sorted(best.values(), key=lambda x: (-x[0], len(x[1].path)))
    return out[: max(1, min(int(limit), 20))]


def _fast_resolve(name: str, *, limit: int = 5) -> list[tuple[int, AppEntry]]:
    """不扫盘：直接路径 / PATH / App Paths / 开始菜单 / .desktop。"""
    tokens = _query_tokens(name)
    if not tokens:
        return []
    q = _normalize_query(name)
    apps: list[AppEntry] = []

    direct = Path(os.path.expandvars(os.path.expanduser(q)))
    if direct.is_file():
        if sys.platform.startswith("win"):
            if direct.suffix.lower() in {".exe", ".lnk", ".bat", ".cmd"}:
                return [
                    (999, AppEntry(name=direct.stem, path=str(direct), source="direct"))
                ]
        else:
            if direct.suffix.lower() == ".desktop" or os.access(direct, os.X_OK):
                return [
                    (999, AppEntry(name=direct.stem, path=str(direct), source="direct"))
                ]

    # PATH / where 候选
    where_names: set[str] = set()
    for tok in tokens:
        key = tok.lower()
        for cand in _WHERE_CANDIDATES.get(key, []):
            where_names.add(cand)
        if sys.platform.startswith("win"):
            if key.endswith(".exe"):
                where_names.add(key)
            elif key.isascii() and " " not in key and len(key) >= 2:
                where_names.add(f"{key}.exe")
        elif key.isascii() and " " not in key and len(key) >= 2:
            where_names.add(key)
    for exe in list(where_names)[:8]:
        hit = _where(exe)
        if hit:
            apps.append(AppEntry(name=q or hit.stem, path=str(hit), source="where"))
            apps.append(AppEntry(name=hit.stem, path=str(hit), source="where"))

    if sys.platform.startswith("win"):
        apps.extend(_scan_app_paths())
        apps.extend(_scan_start_menu())
    else:
        # 轻量：只扫用户 applications 目录（快路径）
        user_apps = Path.home() / ".local" / "share" / "applications"
        if user_apps.is_dir():
            for f in user_apps.glob("*.desktop"):
                entry = _parse_desktop_file(f)
                if entry:
                    apps.append(entry)
    return _rank_entries(apps, tokens, limit=limit)


def _load_disk_cache() -> tuple[list[AppEntry], float] | None:
    cache = _cache_path()
    if not cache.exists():
        return None
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        apps = [AppEntry(**a) for a in data.get("apps") or [] if a.get("path")]
        scanned = float(data.get("scanned_at") or 0)
        return apps, scanned
    except Exception:
        return None


def _save_disk_cache(apps: list[AppEntry], scanned_at: float) -> None:
    payload = {
        "scanned_at": scanned_at,
        "count": len(apps),
        "apps": [asdict(a) for a in apps],
    }
    try:
        _cache_path().write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _set_mem(apps: list[AppEntry], scanned_at: float) -> None:
    global _MEM_INDEX, _MEM_SCANNED_AT
    with _MEM_LOCK:
        _MEM_INDEX = list(apps)
        _MEM_SCANNED_AT = scanned_at


def _get_mem() -> tuple[list[AppEntry], float] | None:
    with _MEM_LOCK:
        if _MEM_INDEX is None:
            return None
        return list(_MEM_INDEX), _MEM_SCANNED_AT


def _rebuild_index_sync() -> list[AppEntry]:
    apps: list[AppEntry] = []
    if sys.platform.startswith("win"):
        apps.extend(_scan_start_menu())
        apps.extend(_scan_app_paths())
        apps.extend(_scan_registry())
        apps.extend(_scan_program_dirs())
        apps.extend(_scan_system_basics())
    else:
        apps.extend(_scan_desktop_apps())
        apps.extend(_scan_program_dirs())
        apps.extend(_scan_system_basics())
    uniq: dict[tuple[str, str], AppEntry] = {}
    for a in apps:
        uniq[(a.name.lower(), a.path.lower())] = a
    result = list(uniq.values())
    now = time.time()
    _save_disk_cache(result, now)
    _set_mem(result, now)
    return result


def _schedule_background_refresh() -> None:
    global _REFRESH_THREAD
    with _REFRESH_LOCK:
        if _REFRESH_THREAD is not None and _REFRESH_THREAD.is_alive():
            return

        def _job():
            try:
                _rebuild_index_sync()
            except Exception:
                pass

        t = threading.Thread(target=_job, name="apps-index-refresh", daemon=True)
        _REFRESH_THREAD = t
        t.start()


def build_index(*, force: bool = False) -> list[AppEntry]:
    """获取软件索引。force=True 同步全量重建；否则优先内存/磁盘，过期则 stale-while-revalidate。"""
    if force:
        return _rebuild_index_sync()

    now = time.time()
    mem = _get_mem()
    if mem:
        apps, scanned = mem
        if now - scanned < _CACHE_TTL_SEC:
            return apps
        # 过期：先返回旧数据，后台刷新
        _schedule_background_refresh()
        return apps

    disk = _load_disk_cache()
    if disk:
        apps, scanned = disk
        _set_mem(apps, scanned)
        if now - scanned >= _CACHE_TTL_SEC:
            _schedule_background_refresh()
        return apps

    # 无缓存：同步建一次（首次），并尽快完成
    return _rebuild_index_sync()


def find_apps(name: str, *, limit: int = 8, refresh: bool = False) -> list[tuple[int, AppEntry]]:
    tokens = _query_tokens(name)
    if not tokens:
        return []
    apps = build_index(force=refresh)
    return _rank_entries(apps, tokens, limit=limit)


def resolve_app(name: str, *, refresh: bool = False) -> Path | None:
    name = (name or "").strip()
    if not name:
        return None
    direct = Path(os.path.expandvars(os.path.expanduser(name)))
    if direct.is_file() and direct.suffix.lower() in {".exe", ".lnk", ".bat", ".cmd"}:
        return direct

    fast = _fast_resolve(name, limit=3)
    if fast and fast[0][0] >= _FAST_SCORE_MIN:
        return Path(fast[0][1].path)

    hits = find_apps(name, limit=5, refresh=refresh)
    if not hits:
        # 轻量补扫：仅开始菜单 + App Paths（不堵全量）
        hits = _fast_resolve(name, limit=5)
    if not hits:
        return None
    return Path(hits[0][1].path)


def list_apps_brief(query: str = "", limit: int = 30) -> str:
    apps = build_index(force=False)
    q = (query or "").strip().lower()
    rows = []
    for a in apps:
        if q and q not in a.name.lower() and q not in a.path.lower():
            continue
        rows.append(f"- {a.name}  [{a.source}]  {a.path}")
        if len(rows) >= limit:
            break
    if not rows:
        return "（未找到匹配软件；可 refresh_app_index 后重试）"
    return f"共展示 {len(rows)} 条（索引约 {len(apps)} 项）\n" + "\n".join(rows)


def refresh_index() -> str:
    apps = build_index(force=True)
    return f"已重新扫描，索引 {len(apps)} 条软件条目 → {_cache_path()}"


def warmup_index(*, force: bool = False) -> None:
    """后台预热：启动时调用，不阻塞 UI。"""
    if force:
        _schedule_background_refresh()
        return

    def _job():
        try:
            build_index(force=False)
            mem = _get_mem()
            if mem:
                _, scanned = mem
                if time.time() - scanned >= _CACHE_TTL_SEC:
                    _rebuild_index_sync()
        except Exception:
            pass

    threading.Thread(target=_job, name="apps-index-warmup", daemon=True).start()


def _start_target(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], close_fds=True)
        return

    # Linux：.desktop 用 gio/gtk-launch；可执行文件直接跑；其它 xdg-open
    p = path.expanduser()
    if p.suffix.lower() == ".desktop" and p.is_file():
        for argv in (
            ["gio", "launch", str(p)],
            ["gtk-launch", p.stem],
            ["xdg-open", str(p)],
        ):
            try:
                if shutil.which(argv[0]):
                    subprocess.Popen(argv, close_fds=True, start_new_session=True)
                    return
            except Exception:
                continue
        raise FileNotFoundError(f"无法启动桌面项: {p}")

    if p.is_file() and os.access(p, os.X_OK):
        subprocess.Popen([str(p)], close_fds=True, start_new_session=True)
        return

    opener = shutil.which("xdg-open")
    if not opener:
        raise FileNotFoundError("未找到 xdg-open")
    subprocess.Popen([opener, str(p)], close_fds=True, start_new_session=True)


def launch_app(name: str, *, refresh: bool = False) -> str:
    name = (name or "").strip()
    if not name:
        return "未指定软件名称"
    bad = (";", "&", "|", "`", "$(", "\n", "&&")
    if any(b in name for b in bad):
        return "名称含有不允许的字符"

    # 1) 快路径
    hits = _fast_resolve(name, limit=5)
    used_fast = bool(hits and hits[0][0] >= _FAST_SCORE_MIN)

    # 2) 缓存索引（可能 stale + 后台刷新）；显式 refresh 才全量同步
    if not used_fast:
        cached = find_apps(name, limit=5, refresh=refresh)
        if cached:
            hits = cached
        elif not hits:
            # 3) 快路径低分结果仍可用；否则失败（不强制全量重扫）
            hits = _fast_resolve(name, limit=5)

    if not hits:
        return (
            f"未找到与「{_normalize_query(name)}」相关的软件。"
            "可换更接近的文件名/开始菜单名，或调用 refresh_app_index 后再试。"
        )

    best_score, best = hits[0]
    alts = [f"{e.name}" for s, e in hits[1:3] if s >= best_score - 25]

    try:
        _start_target(Path(best.path))
    except Exception as e:
        return f"启动失败: {e}"

    via = "快路径" if used_fast else best.source
    msg = f"已启动「{best.name}」（相关度 {best_score} · {via}）\n路径: {best.path}"
    if alts:
        msg += f"\n其他候选: {', '.join(alts)}"
    return msg
