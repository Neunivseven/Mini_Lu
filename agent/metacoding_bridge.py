"""
MetaCoding 可选旁路（阶段 C）。

- 不嵌入 exe：通过本机 Bun + MetaCoding 仓库/全局包调用
- 一次性 JSON CLI（scripts/pet-bridge.ts），非常驻 MCP
- 未安装 / 未启用时优雅降级，Agent 继续用 TSA 工具
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from agent.file_workspace import get_active_root
from agent.llm_client import app_dir

_CONFIG_CACHE: dict[str, Any] | None = None


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config() -> dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE
    base = _load_yaml(app_dir() / "config" / "metacoding.yaml")
    local = _load_yaml(app_dir() / "config" / "metacoding.local.yaml")
    merged = {**base, **local}
    _CONFIG_CACHE = merged
    return merged


def reload_config() -> dict[str, Any]:
    global _CONFIG_CACHE
    _CONFIG_CACHE = None
    return load_config()


def _default_metacoding_root() -> Path:
    # 开发态：桌宠旁的 MetaCoding-main
    sibling = app_dir() / "MetaCoding-main"
    if sibling.is_dir():
        return sibling
    return Path("")


def metacoding_root() -> Path | None:
    cfg = load_config()
    raw = (cfg.get("metacoding_root") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (app_dir() / p).resolve()
        return p if p.is_dir() else None
    d = _default_metacoding_root()
    return d if d and d.is_dir() else None


def find_bun() -> str | None:
    cfg = load_config()
    raw = (cfg.get("bun_path") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if p.is_file():
            return str(p)
    which = shutil.which("bun")
    if which:
        return which
    # 常见 Windows / 用户安装位置
    home = Path.home()
    candidates = [
        home / ".bun" / "bin" / "bun.exe",
        home / ".bun" / "bin" / "bun",
        Path(os.environ.get("USERPROFILE", "")) / ".bun" / "bin" / "bun.exe",
        Path("C:/Users") / Path.home().name / ".bun" / "bin" / "bun.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def bridge_script() -> Path | None:
    root = metacoding_root()
    if not root:
        return None
    p = root / "scripts" / "pet-bridge.ts"
    return p if p.is_file() else None


def is_enabled() -> bool:
    cfg = load_config()
    if cfg.get("enabled") is False:
        return False
    return True


def availability() -> dict[str, Any]:
    """探测旁路是否可用（不跑索引）。"""
    info: dict[str, Any] = {
        "enabled": is_enabled(),
        "bun": find_bun(),
        "metacoding_root": str(metacoding_root() or ""),
        "bridge": str(bridge_script() or ""),
        "ok": False,
        "hint": "",
    }
    if not info["enabled"]:
        info["hint"] = "已在 config/metacoding.yaml 关闭（enabled: false）"
        return info
    if not info["bun"]:
        info["hint"] = "未找到 bun。请安装 https://bun.sh 并确保 PATH 可调用，或配置 bun_path。"
        return info
    if not info["metacoding_root"]:
        info["hint"] = (
            "未找到 MetaCoding 目录。将仓库放在桌宠旁 MetaCoding-main，"
            "或在 config/metacoding.local.yaml 设置 metacoding_root。"
        )
        return info
    if not info["bridge"]:
        info["hint"] = f"缺少桥接脚本: {info['metacoding_root']}/scripts/pet-bridge.ts"
        return info
    node_modules = Path(info["metacoding_root"]) / "node_modules"
    if not node_modules.is_dir():
        info["hint"] = (
            f"MetaCoding 依赖未安装。请在 {info['metacoding_root']} 执行: bun install"
        )
        info["needs_install"] = True
        return info
    info["ok"] = True
    info["hint"] = "可用"
    return info


def workspace_or_error() -> Path | str:
    root = get_active_root()
    if not root:
        return "未设置工作区。请先切换/设置项目文件夹。"
    p = Path(root)
    if not p.is_dir():
        return f"工作区无效: {p}"
    return p.resolve()


def _timeout_for(cmd: str) -> float:
    cfg = load_config()
    if cmd == "index":
        return float(cfg.get("index_timeout_seconds") or 600)
    return float(cfg.get("timeout_seconds") or 120)


def run_bridge(cmd: str, *extra: str, timeout: float | None = None) -> dict[str, Any]:
    """执行 pet-bridge，返回解析后的 JSON dict。"""
    avail = availability()
    if not avail.get("ok"):
        return {
            "ok": False,
            "error": avail.get("hint") or "MetaCoding 不可用",
            "availability": avail,
        }
    bun = avail["bun"]
    script = avail["bridge"]
    root = avail["metacoding_root"]
    args = [bun, "run", script, cmd, *extra]
    try:
        proc = subprocess.run(
            args,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout if timeout is not None else _timeout_for(cmd),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"MetaCoding {cmd} 超时"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    raw = (proc.stdout or "").strip()
    # 取最后一行 JSON（避免 stderr 混入，或 CLI 多段输出）
    line = raw.splitlines()[-1] if raw else ""
    parsed: dict[str, Any] | None = None
    if line:
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                parsed = obj
        except Exception:
            parsed = None
    if parsed is None:
        err_blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
        hint = ""
        if any(
            w in err_blob
            for w in ("segmentation fault", "panic(", "err_dlopen", "loadlibrary")
        ):
            hint = (
                "Windows 上 Bun 加载 ladybugdb 原生模块易崩溃（Node 可加载同一文件）。"
                "请继续用 TSA（index_codebase / find_callers）；MetaCoding 旁路暂不可用。"
            )
        return {
            "ok": False,
            "error": f"无法解析 MetaCoding 输出 (exit={proc.returncode})",
            "hint": hint or None,
            "stdout": (proc.stdout or "")[:1500],
            "stderr": (proc.stderr or "")[:1500],
        }
    if proc.returncode != 0 and parsed.get("ok") is not False:
        parsed.setdefault("ok", False)
        parsed.setdefault("error", f"exit {proc.returncode}")
    if proc.stderr and "stderr" not in parsed:
        # 保留少量诊断
        parsed["_stderr"] = proc.stderr[:800]
    return parsed


def doctor() -> dict[str, Any]:
    avail = availability()
    if not avail.get("ok"):
        return {"ok": False, **avail}
    result = run_bridge("doctor")
    result["availability"] = avail
    # Windows + Bun 加载 @ladybugdb/core 可能直接 segfault（Node 下正常）
    if result.get("ok") is False:
        err = str(result.get("error") or "")
        stderr = str(result.get("stderr") or result.get("_stderr") or "")
        blob = (err + "\n" + stderr).lower()
        if any(
            w in blob
            for w in ("segmentation fault", "panic(", "err_dlopen", "loadlibrary")
        ):
            result["hint"] = (
                "检测到 Windows 上 Bun 加载 ladybugdb 原生模块失败/崩溃。"
                "这是 Bun+ladybug 已知兼容问题（同一 lbugjs.node 在 Node 下可加载）。"
                "桌宠日常请继续用 TSA：index_codebase / find_callers。"
                "MetaCoding 旁路可等 Bun/ladybug 修复，或在 WSL/macOS/Linux 使用。"
            )
            result["error"] = result.get("error") or "Bun 加载 ladybugdb 失败"
    return result


def status(workspace: Path | None = None) -> dict[str, Any]:
    ws = workspace
    if ws is None:
        root = workspace_or_error()
        if isinstance(root, str):
            return {"ok": False, "error": root}
        ws = root
    args = ["--workspace", str(ws)]
    data_dir = (load_config().get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("status", *args)


def index_workspace(
    *,
    workspace: Path | None = None,
    scip: bool | None = None,
) -> dict[str, Any]:
    ws = workspace
    if ws is None:
        root = workspace_or_error()
        if isinstance(root, str):
            return {"ok": False, "error": root}
        ws = root
    cfg = load_config()
    if scip is None:
        scip = bool(cfg.get("default_scip"))
    args = ["--workspace", str(ws), "--scip", "true" if scip else "false"]
    data_dir = (cfg.get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("index", *args, timeout=_timeout_for("index"))


def code_search(query: str, *, limit: int = 40) -> dict[str, Any]:
    root = workspace_or_error()
    if isinstance(root, str):
        return {"ok": False, "error": root}
    args = ["--workspace", str(root), "--query", query, "--limit", str(limit)]
    data_dir = (load_config().get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("code_search", *args)


def graph_callers(symbol: str, *, limit: int = 40) -> dict[str, Any]:
    root = workspace_or_error()
    if isinstance(root, str):
        return {"ok": False, "error": root}
    args = ["--workspace", str(root), "--symbol", symbol, "--limit", str(limit)]
    data_dir = (load_config().get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("graph_callers", *args)


def graph_implementers(symbol: str, *, limit: int = 40) -> dict[str, Any]:
    root = workspace_or_error()
    if isinstance(root, str):
        return {"ok": False, "error": root}
    args = ["--workspace", str(root), "--symbol", symbol, "--limit", str(limit)]
    data_dir = (load_config().get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("graph_implementers", *args)


def graph_neighbors(
    symbol: str,
    *,
    direction: str = "out",
    limit: int = 40,
) -> dict[str, Any]:
    root = workspace_or_error()
    if isinstance(root, str):
        return {"ok": False, "error": root}
    args = [
        "--workspace",
        str(root),
        "--symbol",
        symbol,
        "--direction",
        direction or "out",
        "--limit",
        str(limit),
    ]
    data_dir = (load_config().get("data_dir") or "").strip()
    if data_dir:
        args += ["--data-dir", data_dir]
    return run_bridge("graph_neighbors", *args)


def format_metacoding_report(result: dict[str, Any]) -> str:
    if not result:
        return "（无结果）"
    if result.get("ok") is False or result.get("error"):
        err = result.get("error") or result.get("hint") or "失败"
        hint = ""
        avail = result.get("availability")
        if isinstance(avail, dict) and avail.get("hint") and avail.get("hint") != err:
            hint = f"\n{avail['hint']}"
        elif result.get("hint") and result.get("hint") != err and not result.get("error"):
            # doctor() 直接展开 availability 时 hint 已是主信息
            pass
        return f"MetaCoding 不可用/失败: {err}{hint}"

    lines: list[str] = []
    if "indexed" in result:
        lines.append(
            f"indexed={result.get('indexed')} symbols={result.get('symbols')} "
            f"dataDir={result.get('dataDir')}"
        )
        repos = result.get("repos")
        if isinstance(repos, list) and repos:
            for r in repos[:8]:
                if isinstance(r, dict):
                    lines.append(
                        f"  repo={r.get('repo')} symbols={r.get('symbols')} "
                        f"sha={(r.get('repo_commit_sha') or '')[:8]}"
                    )
        st = result.get("staleness")
        if isinstance(st, dict):
            lines.append(
                f"staleness: head_behind={st.get('head_behind')} "
                f"dirty={st.get('dirty_files')}"
            )
        return "\n".join(lines) or json.dumps(result, ensure_ascii=False)[:1200]

    if "hits" in result:
        hits = result.get("hits") or []
        lines.append(f"code_search hits={result.get('count', len(hits))}")
        for h in hits[:40]:
            if not isinstance(h, dict):
                continue
            lines.append(
                f"  {h.get('file')}:{h.get('line')} [{h.get('kind')}] {h.get('text')}"
            )
        return "\n".join(lines)

    for key in ("callers", "implementers", "neighbors"):
        if key not in result:
            continue
        rows = result.get(key) or []
        lines.append(f"{key} count={result.get('count', len(rows))}")
        if result.get("hint"):
            lines.append(str(result["hint"]))
        for row in rows[:50]:
            if not isinstance(row, dict):
                continue
            sym = row.get("symbol") if isinstance(row.get("symbol"), dict) else row
            edge = row.get("edge") if isinstance(row.get("edge"), dict) else {}
            direction = row.get("direction") or ""
            name = sym.get("qualified_name") or sym.get("short_name") or "?"
            fp = sym.get("file") or ""
            line = sym.get("line")
            ek = edge.get("kind") or ""
            bit = f"  {name}"
            if fp:
                bit += f" @ {fp}"
            if line is not None:
                bit += f":{line}"
            if ek:
                bit += f" [{ek}]"
            if direction:
                bit += f" ({direction})"
            lines.append(bit)
        return "\n".join(lines)

    if "result" in result and isinstance(result["result"], dict):
        r = result["result"]
        lines.append(f"index ok dataDir={r.get('dataDir')} repo={r.get('repo')}")
        ts = r.get("treeSitter")
        if isinstance(ts, dict):
            lines.append(
                f"tree-sitter: scanned={ts.get('filesScanned')} "
                f"updated={ts.get('filesUpdated')} symbols={ts.get('symbols')} "
                f"edges={ts.get('edges')} {ts.get('durationMs')}ms"
            )
        if r.get("scip"):
            lines.append(f"scip: {r.get('scip')}")
        return "\n".join(lines)

    if result.get("bun"):
        tools = result.get("tools") or []
        return f"doctor ok bun={result.get('bun')} tools={len(tools)}"

    # fallback
    text = json.dumps(result, ensure_ascii=False, indent=2)
    return text if len(text) < 2000 else text[:1990] + "…"
