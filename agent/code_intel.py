"""
代码结构智能：tree-sitter-analyzer 薄封装。

阶段 A：大纲 / 符号 / 按符号截取（失败回退正则）
阶段 B：按需全库索引 + callers / callees
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent.file_workspace import build_outline, get_active_root

_MAX_OUTLINE = 120
_ENGINE_CACHE: dict[str, Any] = {}
_TSA_AVAILABLE: bool | None = None


def is_available() -> bool:
    global _TSA_AVAILABLE
    if _TSA_AVAILABLE is None:
        try:
            import tree_sitter_analyzer  # noqa: F401

            _TSA_AVAILABLE = True
        except Exception:
            _TSA_AVAILABLE = False
    return bool(_TSA_AVAILABLE)


def _engine_for(root: Path):
    key = str(root.resolve())
    eng = _ENGINE_CACHE.get(key)
    if eng is not None:
        return eng
    from tree_sitter_analyzer.core.analysis_engine import get_analysis_engine

    eng = get_analysis_engine(key)
    _ENGINE_CACHE[key] = eng
    # 限制缓存数量，避免多工作区泄漏
    if len(_ENGINE_CACHE) > 4:
        old = next(iter(_ENGINE_CACHE))
        if old != key:
            _ENGINE_CACHE.pop(old, None)
    return eng


def _resolve_root(file_path: Path) -> Path:
    active = get_active_root()
    if active:
        try:
            file_path.resolve().relative_to(Path(active).resolve())
            return Path(active).resolve()
        except Exception:
            pass
    return file_path.resolve().parent


def _elem_dict(e: Any) -> dict[str, Any]:
    if isinstance(e, dict):
        name = e.get("name") or ""
        et = e.get("type") or e.get("element_type") or "symbol"
        sl = int(e.get("start_line") or (e.get("lines") or {}).get("start") or 0)
        el = int(e.get("end_line") or (e.get("lines") or {}).get("end") or sl)
        return {
            "name": str(name),
            "type": str(et),
            "start_line": sl,
            "end_line": el,
            "params": e.get("parameters") or [],
            "return_type": e.get("return_type"),
        }
    return {
        "name": str(getattr(e, "name", "") or ""),
        "type": str(
            getattr(e, "class_type", None)
            or getattr(e, "element_type", None)
            or type(e).__name__.lower()
        ),
        "start_line": int(getattr(e, "start_line", 0) or 0),
        "end_line": int(getattr(e, "end_line", 0) or 0),
        "params": list(getattr(e, "parameters", None) or []),
        "return_type": getattr(e, "return_type", None),
    }


def _analyze_elements(file_path: Path) -> list[dict[str, Any]]:
    if not is_available():
        return []
    root = _resolve_root(file_path)
    try:
        from tree_sitter_analyzer.core.analysis_engine import AnalysisRequest

        eng = _engine_for(root)
        req = AnalysisRequest(
            file_path=str(file_path.resolve()),
            include_elements=True,
            include_queries=False,
        )
        result = eng.analyze_sync(req)
        raw = getattr(result, "elements", None) or []
        out = [_elem_dict(e) for e in raw]
        # 过滤空名
        return [x for x in out if x.get("name")]
    except Exception:
        return []


_OUTLINE_TYPES = {
    "class",
    "struct",
    "union",
    "enum",
    "namespace",
    "function",
    "method",
    "constructor",
    "destructor",
    "interface",
    "type",
}


def format_outline_from_elements(elements: list[dict[str, Any]]) -> str:
    """把 TSA 元素格式化成低 token 大纲。"""
    rows: list[str] = []
    for e in elements:
        et = (e.get("type") or "").lower()
        if et in ("import", "variable", "field", "include") and et not in _OUTLINE_TYPES:
            # 仍保留 class/function；import 太多时跳过
            if et == "import":
                continue
            if et in ("variable", "field") and e.get("end_line", 0) - e.get(
                "start_line", 0
            ) < 1:
                continue
        if et not in _OUTLINE_TYPES and not et.endswith("class"):
            # 只保留结构性符号
            if et not in ("function", "method", "class", "struct", "namespace", "enum"):
                continue
        name = e.get("name") or "?"
        sl, el = e.get("start_line") or 0, e.get("end_line") or 0
        extra = ""
        params = e.get("params") or []
        if params and et in ("function", "method", "constructor"):
            ps = ", ".join(str(p) for p in params[:4])
            if len(params) > 4:
                ps += ", …"
            extra = f"({ps})"
        rt = e.get("return_type")
        if rt:
            extra += f" -> {rt}"
        if sl and el and el != sl:
            rows.append(f"{sl}-{el}|{et} {name}{extra}")
        else:
            rows.append(f"{sl}|{et} {name}{extra}")
        if len(rows) >= _MAX_OUTLINE:
            rows.append("…大纲已截断")
            break
    return "\n".join(rows) if rows else ""


def file_outline(file_path: Path, text: str | None = None) -> tuple[str, str]:
    """
    返回 (大纲正文, 来源标记)。
    来源: tsa | regex
    """
    elems = _analyze_elements(file_path)
    structural = [
        e
        for e in elems
        if (e.get("type") or "").lower()
        in _OUTLINE_TYPES
        or (e.get("type") or "").lower() in ("function", "method", "class", "struct")
    ]
    body = format_outline_from_elements(structural) if structural else ""
    if body.strip():
        return body, "tsa"

    # 回退正则
    if text is None:
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
    # 给 C++ 一点正则补强
    boosted = _cpp_boost_outline(text) if file_path.suffix.lower() in {
        ".hpp",
        ".h",
        ".hh",
        ".hxx",
        ".cpp",
        ".cc",
        ".cxx",
    } else ""
    regex_body = build_outline(text or "")
    if boosted:
        # 合并去重
        seen = set()
        lines = []
        for line in (boosted + "\n" + regex_body).splitlines():
            if line and line not in seen:
                seen.add(line)
                lines.append(line)
        regex_body = "\n".join(lines[:_MAX_OUTLINE])
    return regex_body or "（未识别到结构符号）", "regex"


def _cpp_boost_outline(text: str) -> str:
    """简单补强：class/struct/namespace 行（头文件 TSA 有时抽不全）。"""
    import re

    rows: list[str] = []
    pats = [
        re.compile(r"^(\s*)(template\s*<[^>]*>\s*)?(class|struct)\s+(\w+)"),
        re.compile(r"^(\s*)namespace\s+(\w+)"),
    ]
    for i, line in enumerate((text or "").splitlines(), 1):
        s = line.rstrip()
        if not s or s.lstrip().startswith("//"):
            continue
        for pat in pats:
            m = pat.search(s)
            if m:
                rows.append(f"{i}|{s[:160]}")
                break
        if len(rows) >= 40:
            break
    return "\n".join(rows)


def list_symbols(
    file_path: Path,
    *,
    kinds: str = "",
    limit: int = 80,
) -> list[dict[str, Any]]:
    """列出符号；kinds 逗号分隔，如 function,class,method。"""
    elems = _analyze_elements(file_path)
    want = {k.strip().lower() for k in (kinds or "").split(",") if k.strip()}
    out: list[dict[str, Any]] = []
    for e in elems:
        et = (e.get("type") or "").lower()
        if want and et not in want:
            continue
        # 默认跳过 import 噪音，除非显式要
        if not want and et in ("import", "include"):
            continue
        out.append(e)
        if len(out) >= max(1, min(int(limit), 200)):
            break
    return out


def find_symbol(
    file_path: Path,
    name: str,
    *,
    kind: str = "",
) -> dict[str, Any] | None:
    """按名称找符号（精确优先，其次后缀匹配）。"""
    name = (name or "").strip()
    if not name:
        return None
    kind = (kind or "").strip().lower()
    elems = list_symbols(file_path, kinds=kind, limit=200)
    exact = [
        e
        for e in elems
        if e.get("name") == name or e.get("name", "").endswith(f"::{name}")
    ]
    if exact:
        # 取跨度最大的（更可能是定义而非前向声明）
        exact.sort(
            key=lambda e: int(e.get("end_line") or 0) - int(e.get("start_line") or 0),
            reverse=True,
        )
        return exact[0]
    lower = name.lower()
    fuzzy = [e for e in elems if lower in (e.get("name") or "").lower()]
    if fuzzy:
        fuzzy.sort(
            key=lambda e: int(e.get("end_line") or 0) - int(e.get("start_line") or 0),
            reverse=True,
        )
        return fuzzy[0]
    return None


def format_symbols_brief(symbols: list[dict[str, Any]]) -> str:
    if not symbols:
        return "（无符号）"
    lines = []
    for e in symbols:
        et = e.get("type") or "?"
        name = e.get("name") or "?"
        sl, el = e.get("start_line") or 0, e.get("end_line") or 0
        span = f"L{sl}-{el}" if el and el != sl else f"L{sl}"
        lines.append(f"{span}\t{et}\t{name}")
    return "\n".join(lines)


# ── 阶段 B：调用图 ─────────────────────────────────────────────


def workspace_root_or_error() -> Path | str:
    """返回 Path，或错误字符串。"""
    root = get_active_root()
    if not root:
        return "未设置工作区。请先切换/设置项目文件夹。"
    p = Path(root)
    if not p.is_dir():
        return f"工作区无效: {p}"
    return p.resolve()


def _run_async(coro) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        # 工具线程里一般没有跑中的 loop；若有则新建线程跑
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=600)
    return asyncio.run(coro)


def index_codebase(
    *,
    mode: str = "incremental",
    max_files: int = 3000,
) -> dict[str, Any]:
    """建立/更新工作区调用图索引（写入项目 .ast-cache）。"""
    if not is_available():
        return {"success": False, "error": "未安装 tree-sitter-analyzer"}
    root = workspace_root_or_error()
    if isinstance(root, str):
        return {"success": False, "error": root}
    mode = "full" if (mode or "").strip().lower() == "full" else "incremental"
    max_files = max(50, min(int(max_files or 3000), 20000))
    try:
        from tree_sitter_analyzer.mcp.tools.full_index_tool import CodeGraphFullIndexTool

        tool = CodeGraphFullIndexTool(project_root=str(root))
        result = _run_async(
            tool.execute(
                {
                    "mode": mode,
                    "max_files": max_files,
                    "resolve_synapse": True,
                    "include_activation": False,
                    "output_format": "json",
                }
            )
        )
        if not isinstance(result, dict):
            return {"success": False, "error": f"索引返回异常: {type(result)}"}
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def index_status() -> dict[str, Any]:
    if not is_available():
        return {"success": False, "error": "未安装 tree-sitter-analyzer"}
    root = workspace_root_or_error()
    if isinstance(root, str):
        return {"success": False, "error": root}
    try:
        from tree_sitter_analyzer.mcp.tools.codegraph_status_tool import (
            CodeGraphStatusTool,
        )

        tool = CodeGraphStatusTool(project_root=str(root))
        result = _run_async(tool.execute({"output_format": "json"}))
        return result if isinstance(result, dict) else {"success": False, "error": str(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _rel_file(root: Path, file_path: str) -> str | None:
    raw = (file_path or "").strip()
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = (root / p).resolve()
    else:
        p = p.resolve()
    try:
        return str(p.relative_to(root)).replace("\\", "/")
    except Exception:
        return str(p)


def find_callers(
    function_name: str,
    *,
    file_path: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    if not is_available():
        return {"success": False, "error": "未安装 tree-sitter-analyzer"}
    root = workspace_root_or_error()
    if isinstance(root, str):
        return {"success": False, "error": root}
    name = (function_name or "").strip()
    if not name:
        return {"success": False, "error": "function_name 为空"}
    try:
        from tree_sitter_analyzer.mcp.tools.callers_tool import CodeGraphCallersTool

        args: dict[str, Any] = {
            "function_name": name,
            "limit": max(1, min(int(limit or 40), 100)),
            "output_format": "json",
            "include_activation": False,
        }
        rel = _rel_file(root, file_path)
        if rel:
            args["file_path"] = rel
        tool = CodeGraphCallersTool(project_root=str(root))
        result = _run_async(tool.execute(args))
        return result if isinstance(result, dict) else {"success": False, "error": str(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def find_callees(
    function_name: str,
    *,
    file_path: str = "",
    limit: int = 40,
) -> dict[str, Any]:
    if not is_available():
        return {"success": False, "error": "未安装 tree-sitter-analyzer"}
    root = workspace_root_or_error()
    if isinstance(root, str):
        return {"success": False, "error": root}
    name = (function_name or "").strip()
    if not name:
        return {"success": False, "error": "function_name 为空"}
    try:
        from tree_sitter_analyzer.mcp.tools.callees_tool import CodeGraphCalleesTool

        args: dict[str, Any] = {
            "function_name": name,
            "limit": max(1, min(int(limit or 40), 100)),
            "output_format": "json",
            "include_activation": False,
        }
        rel = _rel_file(root, file_path)
        if rel:
            args["file_path"] = rel
        tool = CodeGraphCalleesTool(project_root=str(root))
        result = _run_async(tool.execute(args))
        return result if isinstance(result, dict) else {"success": False, "error": str(result)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def format_index_report(result: dict[str, Any]) -> str:
    if not result:
        return "（无结果）"
    if result.get("error") and not result.get("success", True):
        return f"失败: {result.get('error')}"
    if result.get("success") is False:
        return f"失败: {result.get('error') or result}"
    bits = [f"verdict={result.get('verdict')}"]
    if result.get("mode") is not None:
        bits.append(f"mode={result.get('mode')}")
    if result.get("elapsed_seconds") is not None:
        bits.append(f"耗时={result.get('elapsed_seconds')}s")
    if result.get("indexed") is not None:
        bits.append(f"indexed={result.get('indexed')}")
    lines = [" ".join(bits)]
    for k in (
        "project_root",
        "total_files",
        "total_symbols",
        "total_call_edges",
        "files",
        "symbols",
        "call_edges",
        "indexed_files",
        "fts5_available",
        "lag_seconds",
        "cache_path",
    ):
        if k in result and result[k] is not None:
            lines.append(f"{k}: {result[k]}")
    phases = result.get("phases")
    if isinstance(phases, dict):
        for name, info in phases.items():
            if isinstance(info, dict):
                st = info.get("status") or "?"
                el = info.get("elapsed_seconds")
                extra = ""
                for key in ("files_indexed", "files_cached", "errors", "edges"):
                    if key in info:
                        extra += f" {key}={info[key]}"
                lines.append(f"phase[{name}]: {st} {el}s{extra}")
    for k in ("hint", "next_step"):
        if result.get(k):
            lines.append(f"{k}: {result[k]}")
    summary = result.get("agent_summary")
    if isinstance(summary, dict) and summary.get("next_step"):
        ns = summary["next_step"]
        if ns and f"next_step: {ns}" not in lines and f"hint: {ns}" not in lines:
            lines.append(f"next_step: {ns}")
    return "\n".join(lines)


def format_call_relations(result: dict[str, Any], *, role: str) -> str:
    """role: callers | callees"""
    if not result:
        return "（无结果）"
    if result.get("error") and result.get("success") is False:
        return f"失败: {result.get('error')}"
    verdict = result.get("verdict") or ""
    func = result.get("function") or ""
    key = "callers" if role == "callers" else "callees"
    count_key = "caller_count" if role == "callers" else "callee_count"
    items = result.get(key) or []
    total = result.get(count_key)
    if total is None:
        total = len(items) if isinstance(items, list) else 0
    lines = [
        f"{role} of {func!r} · verdict={verdict} · count={total}"
        + (f" · source={result.get('data_source')}" if result.get("data_source") else "")
    ]
    if verdict in ("NOT_FOUND", "WARN") and result.get("next_step"):
        lines.append(str(result["next_step"]))
    if not items:
        hint = result.get("next_step") or ""
        if isinstance(result.get("agent_summary"), dict):
            hint = hint or result["agent_summary"].get("next_step") or ""
        if hint and str(hint) not in "\n".join(lines):
            lines.append(str(hint))
        blob = " ".join(str(x) for x in (hint, result.get("hint"), verdict)).lower()
        if any(w in blob for w in ("index", "empty", "missing", "warm", "--full-index")):
            lines.append(
                "提示：可先调用 codegraph_status，必要时 index_codebase(mode=\"incremental\")。"
            )
        return "\n".join(lines)
    for it in items[:80]:
        if not isinstance(it, dict):
            lines.append(f"  {it}")
            continue
        name = it.get("name") or it.get("function") or it.get("caller") or it.get("callee") or "?"
        fp = it.get("file") or it.get("file_path") or it.get("path") or ""
        line = it.get("line") or it.get("start_line") or ""
        lang = it.get("language") or ""
        res = it.get("callee_resolution") or it.get("resolution") or ""
        bit = f"  {name}"
        if fp:
            bit += f" @ {fp}"
        if line:
            bit += f":{line}"
        if lang:
            bit += f" [{lang}]"
        if res:
            bit += f" ({res})"
        lines.append(bit)
    if result.get("truncated"):
        lines.append(f"…已截断（listed_cap={result.get('listed_cap')}）")
    if result.get("next_step"):
        lines.append(f"next: {result['next_step']}")
    return "\n".join(lines)
