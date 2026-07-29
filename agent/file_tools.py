"""
文本/代码文件工具：对齐 Claude Code 的 Read / Edit / Write + Glob / Grep。

Token 策略：
- 默认只读一小段；优先 focus / outline，禁止无脑整文件读入上下文
- 修改以 edit_file 精确删改为主；已有较大文件禁止 write_file 整盖
"""
from __future__ import annotations

import re
from pathlib import Path

from langchain.tools import tool

from agent.file_workspace import (
    DEFAULT_READ_LINES,
    MAX_READ_CHARS,
    MAX_READ_LINES,
    MAX_WRITE_CHARS,
    WRITE_EXISTING_MAX_LINES,
    ensure_text_size,
    find_focus_window,
    load_workspace_roots,
    mark_read,
    numbered_slice,
    require_fresh_read,
    resolve_workspace_path,
    save_backup,
)


def _read_text(path: Path) -> str:
    ensure_text_size(path)
    return path.read_text(encoding="utf-8", errors="replace")


@tool
def read_outline(file_path: str) -> str:
    """【读代码结构首选】返回文件结构大纲（class/function/method + 行号），不含函数体。
    用 tree-sitter（C/C++/Python 等）；不可用时回退正则。
    用户问「有哪些接口/类/函数」时优先本工具或 list_symbols，不要整文件 read_file。

    Args:
        file_path: 文件路径
    """
    try:
        p = resolve_workspace_path(file_path, for_write=False)
    except Exception as e:
        return f"读取失败: {e}"
    if not p.exists() or not p.is_file():
        return f"文件不存在或不是文件: {p}"
    try:
        text = _read_text(p)
    except Exception as e:
        return f"读取失败: {e}"
    mark_read(p, text)
    from agent.code_intel import file_outline

    body, src = file_outline(p, text)
    if len(body) > MAX_READ_CHARS:
        body = body[: MAX_READ_CHARS - 1] + "\n…(大纲截断)"
    return f"文件 {p}\n来源: {src}\n{body}"


@tool
def list_symbols(file_path: str, kinds: str = "", limit: int = 60) -> str:
    """【读代码结构首选】列出文件内符号（名称 + 行范围）。tree-sitter 解析。
    比大纲更适合精确跳转；kinds 可选 function,class,method,struct,namespace,enum。

    Args:
        file_path: 文件路径
        kinds: 符号类型过滤
        limit: 最多条数
    """
    try:
        p = resolve_workspace_path(file_path, for_write=False)
    except Exception as e:
        return f"读取失败: {e}"
    if not p.exists() or not p.is_file():
        return f"文件不存在或不是文件: {p}"
    try:
        text = _read_text(p)
        mark_read(p, text)
    except Exception as e:
        return f"读取失败: {e}"
    from agent.code_intel import format_symbols_brief, is_available, list_symbols as _ls

    if not is_available():
        from agent.code_intel import file_outline

        body, src = file_outline(p, text)
        return f"tree-sitter 未启用，回退大纲（{src}）：\n{body}"
    syms = _ls(p, kinds=kinds or "", limit=int(limit) or 60)
    return f"文件 {p}\n{format_symbols_brief(syms)}"


@tool
def read_symbol(file_path: str, name: str, kind: str = "", context: int = 0) -> str:
    """按符号名读取定义全文（行范围精确），避免整文件灌入。
    适用于 C++/Python 等；找不到时返回候选列表。

    Args:
        file_path: 文件路径
        name: 符号名（如 LQR、solveRiccati、main）
        kind: 可选类型过滤 function/class/method…
        context: 额外上下文章行数（默认 0）
    """
    name = (name or "").strip()
    if not name:
        return "name 为空"
    try:
        p = resolve_workspace_path(file_path, for_write=False)
    except Exception as e:
        return f"读取失败: {e}"
    if not p.exists() or not p.is_file():
        return f"文件不存在或不是文件: {p}"
    try:
        text = _read_text(p)
        mark_read(p, text)
    except Exception as e:
        return f"读取失败: {e}"

    from agent.code_intel import find_symbol, format_symbols_brief, list_symbols as _ls

    hit = find_symbol(p, name, kind=kind or "")
    if not hit:
        # 尝试 focus 文本窗口
        win = find_focus_window(text, name)
        if win:
            hit_line, start, end = win
            body = numbered_slice(text, offset=start, limit=max(1, end - start + 1))
            return (
                f"文件 {p}（未解析到符号，文本 focus 命中 L{hit_line}；"
                f"窗口 L{start}-{end}）\n{body}"
            )
        cands = _ls(p, kinds=kind or "", limit=40)
        return (
            f"未找到符号 {name!r}。候选：\n{format_symbols_brief(cands)}"
        )

    sl = max(1, int(hit.get("start_line") or 1))
    el = max(sl, int(hit.get("end_line") or sl))
    ctx = max(0, min(int(context or 0), 40))
    total = len(text.splitlines())
    start = max(1, sl - ctx)
    end = min(total, el + ctx)
    body = numbered_slice(text, offset=start, limit=max(1, end - start + 1))
    et = hit.get("type") or "symbol"
    return (
        f"文件 {p} · {et} {hit.get('name')} · L{sl}-{el}"
        + (f"（含上下文 → L{start}-{end}）" if ctx else "")
        + f"\n{body}"
    )


@tool
def index_codebase(mode: str = "incremental", max_files: int = 3000) -> str:
    """建立或更新当前工作区的代码调用图索引（写入项目 .ast-cache）。
    跨文件查 callers/callees 前若索引为空或过期，先调用本工具。
    默认 incremental（增量）；仅用户明确要求全量重建时用 mode=\"full\"。
    大仓库请限制 max_files，避免一次扫全库过久。

    Args:
        mode: incremental（默认）或 full
        max_files: 本次最多索引文件数（默认 3000，上限 20000）
    """
    from agent.code_intel import format_index_report, index_codebase as _index

    result = _index(mode=mode or "incremental", max_files=int(max_files) or 3000)
    return format_index_report(result)


@tool
def codegraph_status() -> str:
    """查看当前工作区调用图索引状态（是否已建、规模摘要）。"""
    from agent.code_intel import format_index_report, index_status

    return format_index_report(index_status())


@tool
def find_callers(function_name: str, file_path: str = "", limit: int = 40) -> str:
    """查谁调用了某函数（跨文件）。依赖工作区调用图索引；空索引时先 index_codebase。

    Args:
        function_name: 函数名（如 solveRiccati）
        file_path: 可选，限定定义所在文件（相对当前项目或绝对路径）
        limit: 最多返回条数
    """
    from agent.code_intel import find_callers as _callers, format_call_relations

    result = _callers(
        function_name,
        file_path=file_path or "",
        limit=int(limit) or 40,
    )
    return format_call_relations(result, role="callers")


@tool
def find_callees(function_name: str, file_path: str = "", limit: int = 40) -> str:
    """查某函数调用了谁（跨文件）。依赖工作区调用图索引；空索引时先 index_codebase。

    Args:
        function_name: 函数名
        file_path: 可选，限定定义所在文件
        limit: 最多返回条数
    """
    from agent.code_intel import find_callees as _callees, format_call_relations

    result = _callees(
        function_name,
        file_path=file_path or "",
        limit=int(limit) or 40,
    )
    return format_call_relations(result, role="callees")


@tool
def metacoding_doctor() -> str:
    """检查 MetaCoding 旁路是否可用（Bun + MetaCoding-main + bun install）。
    C/C++ 日常用 tree-sitter 工具；TS/Python 大仓可选本旁路。
    """
    from agent.metacoding_bridge import doctor, format_metacoding_report

    return format_metacoding_report(doctor())


@tool
def metacoding_status() -> str:
    """查看当前工作区的 MetaCoding 图索引状态（符号数、是否过期）。"""
    from agent.metacoding_bridge import format_metacoding_report, status

    return format_metacoding_report(status())


@tool
def metacoding_index(scip: bool = False) -> str:
    """索引当前工作区到 MetaCoding 图库（旁路，可选）。
    默认仅 tree-sitter；TS/Python 需要精确 CALLS/IMPLEMENTS 时再 scip=true（更慢）。
    C/C++ 优先用 index_codebase（TSA），不要默认走本工具。

    Args:
        scip: 是否跑 SCIP（TS/Python）
    """
    from agent.metacoding_bridge import format_metacoding_report, index_workspace

    return format_metacoding_report(index_workspace(scip=bool(scip)))


@tool
def metacoding_search(query: str, limit: int = 40) -> str:
    """在 MetaCoding FTS 索引中搜索标识符/字面量（需先 metacoding_index）。

    Args:
        query: 查询串（至少 2 字符）
        limit: 最多条数
    """
    from agent.metacoding_bridge import code_search, format_metacoding_report

    q = (query or "").strip()
    if len(q) < 2:
        return "query 至少 2 个字符"
    return format_metacoding_report(code_search(q, limit=int(limit) or 40))


@tool
def metacoding_callers(symbol: str, limit: int = 40) -> str:
    """MetaCoding 图：谁调用/引用了该符号（SCIP 后更准；TS/Python）。
    C/C++ 优先 find_callers（TSA）。

    Args:
        symbol: 短名或 qualified_name（如 Store / path.ts::Foo）
        limit: 最多条数
    """
    from agent.metacoding_bridge import format_metacoding_report, graph_callers

    s = (symbol or "").strip()
    if not s:
        return "symbol 为空"
    return format_metacoding_report(graph_callers(s, limit=int(limit) or 40))


@tool
def metacoding_implementers(symbol: str, limit: int = 40) -> str:
    """MetaCoding 图：谁实现/继承了该接口或类（需 SCIP）。

    Args:
        symbol: 接口/类名或 qualified_name
        limit: 最多条数
    """
    from agent.metacoding_bridge import format_metacoding_report, graph_implementers

    s = (symbol or "").strip()
    if not s:
        return "symbol 为空"
    return format_metacoding_report(graph_implementers(s, limit=int(limit) or 40))


@tool
def metacoding_neighbors(
    symbol: str,
    direction: str = "out",
    limit: int = 40,
) -> str:
    """MetaCoding 图：符号的入/出邻接（CALLS/EXTENDS/IMPORTS 等）。

    Args:
        symbol: 符号名或 qualified_name
        direction: out | in | both
        limit: 最多条数
    """
    from agent.metacoding_bridge import format_metacoding_report, graph_neighbors

    s = (symbol or "").strip()
    if not s:
        return "symbol 为空"
    d = (direction or "out").strip().lower()
    if d not in ("out", "in", "both"):
        d = "out"
    return format_metacoding_report(
        graph_neighbors(s, direction=d, limit=int(limit) or 40)
    )


@tool
def read_file(
    file_path: str,
    offset: int = 1,
    limit: int = 0,
    focus: str = "",
) -> str:
    """读取工作区文本/代码的关键片段（默认最多约 80 行，禁止默认整文件灌入）。

    优先用法：
    1) read_outline / list_symbols 定位
    2) read_symbol(name=...) 读完整定义；或 read_file(focus=\"符号名\")
    3) 再用 offset/limit 精确窗口（limit 上限 200）

    Args:
        file_path: 绝对路径或相对桌宠根的路径
        offset: 起始行（1-based）；与 focus 同时给时，以 focus 为准
        limit: 读取行数；0=默认 80 行（不会读到文件末尾）
        focus: 子串或符号名；命中后只返回附近窗口
    """
    try:
        p = resolve_workspace_path(file_path, for_write=False)
    except Exception as e:
        return f"读取失败: {e}"
    if not p.exists():
        return f"文件不存在: {p}"
    if not p.is_file():
        return f"不是文件: {p}"
    try:
        text = _read_text(p)
    except Exception as e:
        return f"读取失败: {e}"
    mark_read(p, text)
    total = len(text.splitlines())

    focus = (focus or "").strip()
    if focus:
        # 先尝试符号精确范围
        try:
            from agent.code_intel import find_symbol

            sym = find_symbol(p, focus)
        except Exception:
            sym = None
        if sym and sym.get("start_line"):
            sl = int(sym["start_line"])
            el = max(sl, int(sym.get("end_line") or sl))
            # 若用户给了 limit，以符号起点截断
            if limit and int(limit) > 0:
                el = min(el, sl + min(int(limit), MAX_READ_LINES) - 1)
            span = min(el - sl + 1, MAX_READ_LINES)
            body = numbered_slice(text, offset=sl, limit=max(1, span))
            return (
                f"文件 {p}（共 {total} 行；符号 {sym.get('type')} "
                f"{sym.get('name')} L{sl}-{el}）\n{body}"
            )
        win = find_focus_window(text, focus)
        if not win:
            from agent.code_intel import file_outline

            outline, src = file_outline(p, text)
            return (
                f"文件 {p}（共 {total} 行）未找到 focus={focus!r}。\n"
                f"请换关键词 / read_symbol，或根据大纲（{src}）指定 offset/limit：\n{outline}"
            )
        hit, start, end = win
        lim = end - start + 1
        if limit and int(limit) > 0:
            lim = min(int(limit), MAX_READ_LINES)
            half = lim // 2
            start = max(1, hit - half)
            end = min(total, start + lim - 1)
            start = max(1, end - lim + 1)
        body = numbered_slice(text, offset=start, limit=max(1, end - start + 1))
        header = f"文件 {p}（共 {total} 行；focus 命中 L{hit}；窗口 L{start}-{end}）\n"
    else:
        use_limit = int(limit) if limit and int(limit) > 0 else DEFAULT_READ_LINES
        use_limit = min(use_limit, MAX_READ_LINES)
        # 小文件可一次读完
        if total <= DEFAULT_READ_LINES and (not limit or int(limit) <= 0):
            use_limit = total
        body = numbered_slice(text, offset=offset or 1, limit=use_limit)
        header = (
            f"文件 {p}（共 {total} 行；本次最多 {use_limit} 行。"
            "大文件请用 focus / offset，勿反复整读）\n"
        )

    out = header + body
    if len(out) > MAX_READ_CHARS:
        out = out[: MAX_READ_CHARS - 1] + "\n…(已截断)"
    return out


@tool
def edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """精确删改文件（首选）。用 old_string→new_string 替换；删除则把 new_string 设为空串。
    禁止为小改动 write_file 整文件重写。必须先 read_file 或 read_outline。
    old_string 须唯一（或 replace_all=True）；只含要改的最小片段+少量上下文。

    Args:
        file_path: 目标文件
        old_string: 原文（尽量短但唯一）
        new_string: 新文；空字符串表示删除该段
        replace_all: 是否替换全部匹配
    """
    if old_string == new_string:
        return "old_string 与 new_string 相同，无变更"
    try:
        p = resolve_workspace_path(file_path, for_write=True)
    except Exception as e:
        return f"编辑失败: {e}"

    if not p.exists():
        if old_string == "":
            return write_file.invoke({"file_path": str(p), "content": new_string})
        return f"文件不存在: {p}（新建请用 write_file）"

    try:
        content = _read_text(p)
        require_fresh_read(p, content)
    except Exception as e:
        return f"编辑失败: {e}"

    if old_string == "":
        return "已有文件请用非空 old_string 做精确替换；删除内容时 new_string 留空。"

    count = content.count(old_string)
    if count == 0:
        preview = old_string[:120].replace("\n", "\\n")
        return f"未找到 old_string（核对缩进或重新 read_file(focus=...)）。片段: {preview}"
    if count > 1 and not replace_all:
        return (
            f"找到 {count} 处匹配。扩大 old_string 上下文使其唯一，"
            "或 replace_all=True。"
        )

    backup = save_backup(p, content)
    if replace_all:
        updated = content.replace(old_string, new_string)
    else:
        updated = content.replace(old_string, new_string, 1)

    from agent.edit_staging import is_review_enabled, stage_edit

    action = "删除" if new_string == "" else "替换"
    n = count if replace_all else 1
    summary = f"{action} {n} 处 @ {p.name}"
    if is_review_enabled():
        return stage_edit(p, content, updated, summary=summary)

    try:
        p.write_text(updated, encoding="utf-8", newline="\n")
    except Exception as e:
        return f"写入失败: {e}"

    mark_read(p, updated)
    bak = f"；备份 {backup.name}" if backup else ""
    return f"已{action} {p}（{n} 处）{bak}"


@tool
def write_file(file_path: str, content: str) -> str:
    """仅用于【新建】小文件，或【确实需要】整文件重写的短文件。
    已有文件且超过约 40 行时会被拒绝——必须改用 edit_file 做删改。
    覆盖已有文件前须先 read_file / read_outline。

    Args:
        file_path: 目标路径
        content: 完整文件内容
    """
    content = content if content is not None else ""
    if len(content) > MAX_WRITE_CHARS:
        return f"内容过长（>{MAX_WRITE_CHARS} 字符）。请拆成多次 edit_file，勿整文件灌入。"
    try:
        p = resolve_workspace_path(file_path, for_write=True)
    except Exception as e:
        return f"写入失败: {e}"

    existed = p.exists()
    old = ""
    if existed:
        if not p.is_file():
            return f"路径已存在且不是文件: {p}"
        try:
            old = _read_text(p)
            require_fresh_read(p, old)
        except Exception as e:
            return f"写入失败: {e}"
        old_lines = len(old.splitlines())
        if old_lines > WRITE_EXISTING_MAX_LINES:
            return (
                f"拒绝 write_file：{p} 已有 {old_lines} 行。"
                f"请用 edit_file 做精确删改（new_string 为空即删除），以节省 token。"
            )
        backup = save_backup(p, old)
    else:
        backup = None

    from agent.edit_staging import is_review_enabled, stage_edit

    action = "覆盖" if existed else "新建"
    summary = f"{action} {p.name}（{len(content)} 字符）"
    if is_review_enabled():
        return stage_edit(p, old, content, summary=summary)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    except Exception as e:
        return f"写入失败: {e}"

    mark_read(p, content)
    bak = f"；备份 {backup.name}" if backup else ""
    return f"已{action} {p}（{len(content)} 字符）{bak}"


@tool
def glob_files(pattern: str, path: str = "", limit: int = 30) -> str:
    """在工作区内按 glob 查找文件路径（只返回路径，不读内容）。

    Args:
        pattern: 如 **/*.py、src/**/*.ts
        path: 搜索根；空则用全部 workspace roots
        limit: 最多条数（默认 30）
    """
    pattern = (pattern or "").strip() or "**/*"
    limit = max(1, min(int(limit), 100))
    roots: list[Path] = []
    if (path or "").strip():
        try:
            roots = [resolve_workspace_path(path, for_write=False)]
        except Exception as e:
            return f"搜索失败: {e}"
        if not roots[0].is_dir():
            return f"不是目录: {roots[0]}"
    else:
        roots = load_workspace_roots()

    hits: list[str] = []
    for root in roots:
        try:
            for p in root.glob(pattern):
                if p.is_file() and not p.name.endswith(".pet.before"):
                    hits.append(str(p))
                    if len(hits) >= limit:
                        break
            if len(hits) < limit and "**" not in pattern and "/" not in pattern and "\\" not in pattern:
                for p in root.rglob(pattern):
                    if p.is_file() and not p.name.endswith(".pet.before") and str(p) not in hits:
                        hits.append(str(p))
                        if len(hits) >= limit:
                            break
        except Exception:
            continue
        if len(hits) >= limit:
            break

    if not hits:
        return f"未找到匹配: {pattern}"
    more = "" if len(hits) < limit else f"\n…已达 limit={limit}"
    return "\n".join(hits[:limit]) + more


@tool
def grep_files(
    pattern: str,
    path: str = "",
    glob: str = "",
    head_limit: int = 20,
) -> str:
    """搜索匹配行（默认最多 20 条）。定位后用 read_file(focus=...) 读附近，不要 grep 后整文件 Read。

    Args:
        pattern: 正则或子串
        path: 搜索根；空则 workspace roots
        glob: 文件过滤，如 *.py
        head_limit: 最多匹配行数
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return "pattern 为空"
    head_limit = max(1, min(int(head_limit), 80))
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则无效: {e}"

    if (path or "").strip():
        try:
            roots = [resolve_workspace_path(path, for_write=False)]
        except Exception as e:
            return f"搜索失败: {e}"
    else:
        roots = load_workspace_roots()

    file_glob = (glob or "").strip() or "*"
    matches: list[str] = []
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build"}

    for root in roots:
        base = root if root.is_dir() else root.parent
        try:
            candidates = list(base.rglob(file_glob)) if file_glob != "*" else base.rglob("*")
        except Exception:
            continue
        for fp in candidates:
            if len(matches) >= head_limit:
                break
            if not fp.is_file():
                continue
            if any(part in skip_dirs for part in fp.parts):
                continue
            if fp.suffix.lower() in {
                ".png", ".jpg", ".jpeg", ".gif", ".exe", ".dll", ".pyd", ".zip", ".rar",
            }:
                continue
            try:
                if fp.stat().st_size > MAX_READ_CHARS * 4:
                    continue
                text = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    matches.append(f"{fp}:{i}:{line[:160]}")
                    if len(matches) >= head_limit:
                        break
        if len(matches) >= head_limit:
            break

    if not matches:
        return f"无匹配: {pattern}"
    tip = "\n（下一步: read_file(focus=关键词) 或 offset=行号，勿整文件读取）"
    return "\n".join(matches) + tip


@tool
def list_workspaces() -> str:
    """查看当前代码工作区：活动项目与可读写根目录列表。"""
    from agent.file_workspace import format_workspace_status

    return format_workspace_status()


@tool
def open_workspace_picker() -> str:
    """打开工作区面板，让用户用系统对话框选择/切换项目文件夹（类似 VS Code 打开文件夹）。"""
    from agent.ui_bridge import get_bridge

    bridge = get_bridge()
    if bridge is None:
        return "界面未就绪；请用户点击聊天栏「📁」或右键「工作区…」"
    if hasattr(bridge, "open_workspace"):
        bridge.open_workspace.emit()
        return "已打开工作区面板，请用户选择文件夹"
    return "当前版本未接工作区面板"


@tool
def add_workspace(path: str, set_active: bool = True) -> str:
    """把指定文件夹加入工作区白名单。

    Args:
        path: 文件夹绝对路径
        set_active: 是否同时设为当前项目（默认是）
    """
    from agent.file_workspace import add_workspace_root

    try:
        p = add_workspace_root(path, set_active=bool(set_active))
    except Exception as e:
        return f"添加失败: {e}"
    return f"已加入工作区{'并设为当前' if set_active else ''}: {p}"


@tool
def set_workspace(path: str) -> str:
    """将指定文件夹设为当前项目（相对路径以此为根）；不在白名单则自动加入。"""
    from agent.file_workspace import set_active_workspace

    try:
        p = set_active_workspace(path)
    except Exception as e:
        return f"设置失败: {e}"
    return f"当前项目已设为: {p}"


def coding_tools() -> list:
    return [
        list_workspaces,
        open_workspace_picker,
        add_workspace,
        set_workspace,
        read_outline,
        list_symbols,
        read_symbol,
        index_codebase,
        codegraph_status,
        find_callers,
        find_callees,
        metacoding_doctor,
        metacoding_status,
        metacoding_index,
        metacoding_search,
        metacoding_callers,
        metacoding_implementers,
        metacoding_neighbors,
        read_file,
        edit_file,
        write_file,
        glob_files,
        grep_files,
    ]


# 在 file_tools 末尾不放 session tools；放到 tools.py 或 chat 侧
# —— 下面 session tools 供 tools.py 导入 ——


@tool
def list_chat_sessions() -> str:
    """列出所有对话 / Agent 会话（当前 ★）。"""
    from agent.chat_history import format_sessions_brief

    return format_sessions_brief()


@tool
def new_chat_session(title: str = "新对话") -> str:
    """新建独立对话（New Agent），上下文与 Goal 与旧对话隔离。

    Args:
        title: 可选标题
    """
    from agent.chat_history import create_session

    s = create_session(title or "新对话", activate=True)
    return f"已新建并切换到对话 [{s['id']}] {s['title']}"


@tool
def switch_chat_session(session_id: str) -> str:
    """切换到指定对话 session_id（见 list_chat_sessions）。"""
    from agent.chat_history import switch_session

    s = switch_session((session_id or "").strip())
    if not s:
        return f"未找到会话: {session_id}"
    return f"已切换到 [{s['id']}] {s['title']}"


def session_tools() -> list:
    return [list_chat_sessions, new_chat_session, switch_chat_session]
