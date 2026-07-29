"""
桌宠 Agent 第一批工具：剪贴板、打开路径、列目录、本地记事。

危险能力（任意 shell / 键鼠）暂不开放。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any

from langchain.tools import tool
from langgraph.prebuilt import InjectedStore

from agent.llm_client import app_dir


def _get_clipboard_text() -> str:
    try:
        import pyperclip

        return pyperclip.paste() or ""
    except Exception:
        pass
    # Windows 兜底：PowerShell
    if sys.platform.startswith("win"):
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                return r.stdout
        except Exception:
            pass
        return ""
    # Linux：wl-paste / xclip / xsel
    for argv in (
        ["wl-paste", "-n"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
    ):
        try:
            if not shutil.which(argv[0]):
                continue
            r = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=5,
                encoding="utf-8",
                errors="replace",
            )
            if r.returncode == 0:
                return r.stdout or ""
        except Exception:
            continue
    return ""


def _set_clipboard_text(text: str) -> None:
    try:
        import pyperclip

        pyperclip.copy(text)
        return
    except Exception:
        pass
    if sys.platform.startswith("win"):
        # 通过管道设置剪贴板
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
            input=text,
            text=True,
            timeout=5,
            check=False,
            encoding="utf-8",
        )
        return
    for argv in (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
    ):
        try:
            if not shutil.which(argv[0]):
                continue
            subprocess.run(
                argv,
                input=text,
                text=True,
                timeout=5,
                check=False,
                encoding="utf-8",
            )
            return
        except Exception:
            continue
    raise RuntimeError(
        "当前环境无法写入剪贴板（可 pip install pyperclip，或安装 wl-clipboard / xclip）"
    )


@tool
def get_clipboard() -> str:
    """读取当前系统剪贴板中的文本内容。"""
    text = _get_clipboard_text()
    if not text:
        return "（剪贴板为空或无法读取）"
    # 防止过长上下文
    if len(text) > 4000:
        return text[:4000] + "\n…(已截断)"
    return text


@tool
def set_clipboard(text: str) -> str:
    """把给定文本写入系统剪贴板。

    Args:
        text: 要写入剪贴板的文本
    """
    _set_clipboard_text(text)
    return f"已写入剪贴板（{len(text)} 字符）"


@tool
def open_path(path: str) -> str:
    """用系统默认方式打开本地文件或文件夹（Windows 上调用默认关联程序）。

    Args:
        path: 绝对路径或相对项目根的路径
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (app_dir() / p).resolve()
    if not p.exists():
        return f"路径不存在: {p}"
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=False)
        else:
            subprocess.run(["xdg-open", str(p)], check=False)
        return f"已打开: {p}"
    except Exception as e:
        return f"打开失败: {e}"


@tool
def open_app(name: str) -> str:
    """打开本机软件。会先扫描/检索本机索引（各盘 Program Files、开始菜单、卸载信息等），
    按文件名与文件夹名相关性匹配，不依赖写死路径。
    用户说「打开QQ」「启动微信」时必须调用。

    Args:
        name: 软件名称（如 QQ、微信）或 .exe 完整路径
    """
    from agent.app_launcher import launch_app

    return launch_app(name)


@tool
def open_terminal(cwd: str = "", shell: str = "auto", title: str = "") -> str:
    """打开本机交互式终端窗口（Windows Terminal / PowerShell / cmd 等）。
    用户说「打开终端」「开个 PowerShell」且只要窗口、不要 Agent 代跑时用。
    需要执行并查看结果时用 run_command。

    Args:
        cwd: 工作目录；空=当前项目工作区
        shell: auto | wt | pwsh | powershell | cmd | bash
        title: 可选窗口标题
    """
    from agent.terminal_launcher import format_open_terminal, open_terminal as _open

    return format_open_terminal(
        _open(cwd=cwd or "", shell=shell or "auto", title=title or "")
    )


@tool
def run_command(
    command: str,
    cwd: str = "",
    shell: str = "auto",
    timeout_seconds: float = 60,
) -> str:
    """在当前工作区执行一条 shell 命令，返回 exit code + stdout/stderr。
    用于编译、测试、git 状态、pip/npm、运行脚本等闭环。
    默认 cwd=当前项目；Windows 默认 PowerShell。命令在无窗口子进程中跑完即返回。
    注意：执行前会请求用户确认（已信任的命令可自动运行）。

    Args:
        command: 要执行的命令（如 g++ main.cpp -o main、pytest、git status）
        cwd: 工作目录；空=当前工作区；可为相对路径
        shell: auto | pwsh | powershell | cmd | bash
        timeout_seconds: 超时秒数（默认 60，上限 600）
    """
    from agent.command_approval import notify_command_result, request_command_approval
    from agent.terminal_launcher import format_run_command, run_command as _run

    cmd = (command or "").strip()
    if not cmd:
        return "command 为空"

    decision = request_command_approval(command=cmd, cwd=cwd or "")
    rid = str(decision.get("request_id") or "")
    if str(decision.get("action") or "deny") != "allow":
        notify_command_result(
            rid,
            {
                "command": cmd,
                "cwd": cwd or "",
                "ok": False,
                "denied": True,
                "output": "用户取消执行",
            },
        )
        return "用户取消执行该终端命令。请改用不需要终端的方法，或等用户允许后再试。"

    result = _run(
        cmd,
        cwd=cwd or "",
        shell=shell or "auto",
        timeout_seconds=float(timeout_seconds or 60),
    )
    notify_command_result(
        rid,
        {
            "command": cmd,
            "cwd": str(result.get("cwd") or cwd or ""),
            "ok": bool(result.get("ok")),
            "exit_code": result.get("exit_code"),
            "output": format_run_command(result),
            "denied": False,
        },
    )
    return format_run_command(result)


@tool
def refresh_app_index() -> str:
    """强制重新扫描本机已安装软件并刷新索引。找不到软件或刚装完新软件时调用。"""
    from agent.app_launcher import refresh_index

    return refresh_index()


@tool
def list_apps(query: str = "", limit: int = 30) -> str:
    """列出本机软件索引中的条目（可按关键词过滤），用于确认有哪些可打开的软件。

    Args:
        query: 过滤关键词，可空
        limit: 最多条数
    """
    from agent.app_launcher import list_apps_brief

    return list_apps_brief(query, limit=limit)


@tool
def list_directory(path: str = ".", max_entries: int = 50) -> str:
    """列出目录下的文件与子目录名称（仅文件名清单）。
    不要用本工具分析代码结构/接口：请用 glob_files + read_outline / list_symbols。

    Args:
        path: 目录路径，默认当前项目根
        max_entries: 最多返回多少条（默认 50）
    """
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (app_dir() / p).resolve()
    if not p.exists():
        return f"路径不存在: {p}"
    if not p.is_dir():
        return f"不是目录: {p}"
    max_entries = max(1, min(int(max_entries), 200))
    entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
    lines = []
    for i, e in enumerate(entries):
        if i >= max_entries:
            lines.append(f"…另有 {len(entries) - max_entries} 项未列出")
            break
        kind = "dir" if e.is_dir() else "file"
        lines.append(f"[{kind}] {e.name}")
    return "\n".join(lines) if lines else "（空目录）"


@tool
def append_note(content: str, summary: str = "") -> str:
    """保存一条【纯记事】（只存文字，不会响铃）。
    用于备忘、想法、资料等不需要到点提醒的内容。
    若用户要闹钟/到点提醒，请用 add_alarm，不要用本工具硬塞时间。

    Args:
        content: 完整记事正文
        summary: 列表用短标题，可空
    """
    from agent.notes_store import add_note

    try:
        item = add_note(
            content,
            summary=(summary or "").strip() or None,
            kind="note",
        )
    except Exception as e:
        return f"记事失败: {e}"
    return f"已记事（无闹钟）[{item['id']}] {item['summary']}"


@tool
def add_alarm(
    content: str,
    remind_at: str = "",
    delay_seconds: int = 0,
    alarm_mode: str = "once",
    repeat: str = "none",
    summary: str = "",
) -> str:
    """创建【闹钟】：到点在桌宠旁冒泡。与纯记事分开——只有明确要提醒时才用。
    - once：只响一次（会议、截止日期、N分钟后）
    - repeat：长期重复（每天吃药、工作日站会）；repeat=daily|weekly|weekdays|monthly

    Args:
        content: 闹钟对应事件说明
        remind_at: 绝对时间，如 2026-07-26 15:00 或 08:30
        delay_seconds: 多少秒后响（一次性常用）
        alarm_mode: once 或 repeat
        repeat: none/daily/weekly/weekdays/monthly；mode=repeat 时必填有效规则
        summary: 短标题，可空
    """
    from agent.notes_store import add_note

    delay = int(delay_seconds) if delay_seconds else None
    at = (remind_at or "").strip() or None
    if delay is None and at is None:
        return "闹钟需要时间：remind_at 或 delay_seconds"
    mode = (alarm_mode or "once").strip().lower()
    if mode not in ("once", "repeat"):
        mode = "once"
    rep = (repeat or "none").strip().lower()
    if mode == "repeat" and rep == "none":
        rep = "daily"
    try:
        item = add_note(
            content,
            summary=(summary or "").strip() or None,
            kind="alarm",
            alarm_mode=mode,
            remind_at=at,
            delay_seconds=delay,
            repeat=rep,
        )
    except Exception as e:
        return f"闹钟失败: {e}"
    if item.get("alarm_mode") == "repeat":
        return (
            f"已设长期闹钟 [{item['id']}] {item['summary']}；"
            f"规则={item['repeat']}，下次 {item['remind_at']}"
        )
    return f"已设一次性闹钟 [{item['id']}] {item['summary']}；将在 {item['remind_at']} 冒泡"


@tool
def list_notes(limit: int = 30, kind: str = "") -> str:
    """列出记事/闹钟简略目录。kind 可空=全部，或 note / alarm。

    Args:
        limit: 最多条数
        kind: 空 / note / alarm
    """
    from agent.notes_store import format_brief_list, list_notes as _list

    k = (kind or "").strip().lower() or None
    if k not in (None, "note", "alarm"):
        k = None
    return format_brief_list(_list(limit, kind=k))


@tool
def get_note(note_id: str) -> str:
    """按 id 读取一条记事/闹钟的完整内容。"""
    from agent.notes_store import format_note_detail, get_note as _get

    note = _get(note_id)
    if not note:
        return f"未找到 id={note_id}"
    return format_note_detail(note)


@tool
def delete_note(note_id: str) -> str:
    """彻底删除一条记事或闹钟（不可恢复）。用户说删除/去掉某条时调用。"""
    from agent.notes_store import delete_note as _del

    if _del(note_id):
        return f"已删除 [{note_id}]"
    return f"未找到 id={note_id}"


@tool
def read_notes(limit: int = 30) -> str:
    """查看全部记事简略列表。等同 list_notes。"""
    return list_notes.invoke({"limit": limit, "kind": ""})


@tool
def open_notes_viewer() -> str:
    """打开记事本面板（可点进详情、手动删除）。"""
    from agent.ui_bridge import get_bridge

    bridge = get_bridge()
    if bridge is None:
        return "界面未就绪；可用 list_notes，或右键「查看记事内容」"
    bridge.open_notes.emit()
    return "已打开记事本面板"


@tool
def add_reminder(
    content: str,
    remind_at: str = "",
    delay_seconds: int = 0,
    alarm_mode: str = "once",
    repeat: str = "none",
) -> str:
    """add_alarm 的别名。创建闹钟（不是纯记事）。"""
    return add_alarm.invoke(
        {
            "content": content,
            "remind_at": remind_at,
            "delay_seconds": delay_seconds,
            "alarm_mode": alarm_mode,
            "repeat": repeat,
            "summary": "",
        }
    )


@tool
def list_reminders(limit: int = 20) -> str:
    """列出仍开启的闹钟（含一次性待响与长期重复）。"""
    from agent.notes_store import list_notes as _list

    pending = [
        n
        for n in _list(200, kind="alarm")
        if n.get("alarm_enabled") and n.get("remind_at")
    ][: max(1, min(int(limit), 100))]
    if not pending:
        return "（当前没有开启的闹钟）"
    pending.sort(key=lambda x: x.get("remind_at") or "")
    lines = []
    for i in pending:
        mode = "重复" if i.get("alarm_mode") == "repeat" else "一次"
        extra = f"/{i.get('repeat')}" if i.get("alarm_mode") == "repeat" else ""
        lines.append(
            f"- [{i['id']}] ({mode}{extra}) {i['remind_at']}  {i.get('summary', '')}"
        )
    return "\n".join(lines)


@tool
def cancel_reminder(reminder_id: str) -> str:
    """关闭闹钟但保留正文为普通记事。若要连正文删掉请用 delete_note。"""
    from agent.notes_store import clear_reminder

    status = clear_reminder(reminder_id)
    if status == "ok":
        return f"已关闭闹钟，正文保留为记事 [{reminder_id}]"
    if status == "none":
        return "该条目没有可关闭的闹钟"
    return f"未找到 id={reminder_id}"


@tool
def remember(
    fact: str,
    store: Annotated[Any, InjectedStore()] = None,
) -> str:
    """把需要长期记住的事实写入 LangGraph Store（跨会话）。用户说「记住…」时用。

    Args:
        fact: 事实原文，如「用户叫 Lee，偏好简洁中文回复」
    """
    from agent.lg_runtime import MEMORY_NAMESPACE, get_store

    text = (fact or "").strip()
    if not text:
        return "未提供可记忆内容。"
    st = store if store is not None else get_store()
    key = uuid.uuid4().hex[:12]
    st.put(MEMORY_NAMESPACE, key, {"text": text})
    return f"已写入长期记忆 Store（key={key}）"


@tool
def recall_memories(
    query: str = "",
    store: Annotated[Any, InjectedStore()] = None,
) -> str:
    """查看/检索跨会话长期记忆（LangGraph Store）。

    Args:
        query: 可选关键词过滤；空则列出近期条目
    """
    from agent.lg_runtime import MEMORY_NAMESPACE, get_store

    st = store if store is not None else get_store()
    items = st.search(MEMORY_NAMESPACE, query=(query or None), limit=40)
    q = (query or "").strip().lower()
    lines: list[str] = []
    for it in items:
        val = getattr(it, "value", None) or {}
        text = str(val.get("text") or val.get("data") or "").strip()
        if not text:
            continue
        if q and q not in text.lower() and q not in str(getattr(it, "key", "")).lower():
            continue
        lines.append(f"- [{getattr(it, 'key', '')}] {text}")
    if not lines:
        return "（Store 中暂无匹配的长期记忆）"
    return "长期记忆（LangGraph Store）:\n" + "\n".join(lines)


@tool
def forget_memory(
    key: str,
    store: Annotated[Any, InjectedStore()] = None,
) -> str:
    """按 key 删除一条长期记忆（见 recall_memories 列出的 key）。"""
    from agent.lg_runtime import MEMORY_NAMESPACE, get_store

    k = (key or "").strip()
    if not k:
        return "请提供 key。"
    st = store if store is not None else get_store()
    st.delete(MEMORY_NAMESPACE, k)
    return f"已删除记忆 key={k}"


@tool
def clear_memories(store: Annotated[Any, InjectedStore()] = None) -> str:
    """清空全部跨会话长期记忆（Store）。短时对话上下文按会话 Checkpointer 管理，不受影响。"""
    from agent.lg_runtime import clear_long_term_store

    n = clear_long_term_store()
    return f"已清空长期记忆 Store（{n} 条）。"


@tool
def read_memory(kind: str = "all") -> str:
    """兼容旧名：查看长期记忆（等同 recall_memories）。kind 参数已忽略。"""
    return recall_memories.invoke({"query": ""})


@tool
def update_memory(instruction: str) -> str:
    """兼容旧名：把指令内容写入长期记忆 Store（等同 remember）。"""
    return remember.invoke({"fact": instruction})


@tool
def save_memory(content: str) -> str:
    """兼容旧名：写入长期记忆。"""
    return remember.invoke({"fact": content})


@tool
def list_memories(kind: str = "all", limit: int = 30) -> str:
    """兼容旧名：列出长期记忆。"""
    return recall_memories.invoke({"query": ""})


@tool
def reset_memories(which: str = "all") -> str:
    """兼容旧名：清空长期记忆 Store。which 参数已忽略（短时记忆随会话 Checkpointer）。"""
    return clear_memories.invoke({})


@tool
def open_memory_viewer() -> str:
    """打开记忆面板（查看 LangGraph Store / 说明 Checkpointer）。"""
    from agent.ui_bridge import get_bridge

    bridge = get_bridge()
    if bridge is None:
        return "界面未就绪；可用 recall_memories 查看"
    if hasattr(bridge, "open_memory"):
        bridge.open_memory.emit()
        return "已打开记忆面板"
    return "当前版本界面未接记忆面板"


# —— Goal 跨轮驱动 ——


@tool
def set_goal(objective: str, max_turns: int = 30) -> str:
    """设定跨轮 Goal。之后每轮对话都会带着该目标，直到完成/暂停/达上限。

    Args:
        objective: 目标描述，如「把附件合同关键条款整理成记事」
        max_turns: 最多自动计入的轮次上限（默认 30）
    """
    from agent.goal_store import set_goal as _set

    try:
        g = _set(objective, max_turns=max_turns)
    except ValueError as e:
        return str(e)
    return f"已设定 Goal（active，上限 {g['max_turns']} 轮）：{g['objective']}"


@tool
def get_goal() -> str:
    """查看当前 Goal 状态与进度。"""
    from agent.goal_store import format_status

    return format_status()


@tool
def pause_goal() -> str:
    """暂停当前 Goal（不再推进，直到 resume_goal）。"""
    from agent.goal_store import pause_goal as _pause

    g = _pause()
    return "已暂停 Goal。" if g else "没有可暂停的 active Goal。"


@tool
def resume_goal() -> str:
    """恢复已暂停的 Goal。"""
    from agent.goal_store import resume_goal as _resume

    g = _resume()
    return "已恢复 Goal。" if g else "没有可恢复的 paused Goal。"


@tool
def clear_goal() -> str:
    """清除当前 Goal。"""
    from agent.goal_store import clear_goal as _clear

    return "已清除 Goal。" if _clear() else "当前没有 Goal。"


@tool
def mark_goal_done(note: str = "") -> str:
    """标记当前 Goal 已完成。

    Args:
        note: 可选完成说明
    """
    from agent.goal_store import mark_completed

    g = mark_completed(note)
    return f"Goal 已完成。{note}".strip() if g else "当前没有 Goal。"


@tool
def report_goal_blocked(reason: str) -> str:
    """报告 Goal 受阻（缺信息/缺权限/外部依赖等）。连续多次会进入 blocked。

    Args:
        reason: 受阻原因
    """
    from agent.goal_store import report_blocked

    g = report_blocked(reason)
    if not g:
        return "当前没有 active Goal。"
    if g.get("status") == "blocked":
        return f"已记录受阻并标记 blocked：{g.get('last_block_reason')}"
    return f"已记录受阻（{g.get('blocked_attempts')} 次）：{g.get('last_block_reason')}"


# —— 确定性工作流 ——


@tool
def run_workflow(plan: str) -> str:
    """按确定性阶段串行执行工作流（每阶段独立 Agent 回合，结果写入 journal）。

    Args:
        plan: JSON，如 {"name":"改合同","steps":[{"phase":"检视","instruction":"..."},{"phase":"编辑","instruction":"..."}]}
             或简易多行：每行「阶段名|指令」
    """
    from agent.pet_agent import build_react_agent, run_agent
    from agent.tools import default_tools as _tools
    from agent.workflow_engine import format_run_report, run_pipeline

    def _invoke(prompt: str) -> str:
        # 独立 ReAct；去掉 run_workflow 防止嵌套爆炸
        inner = [t for t in _tools() if getattr(t, "name", "") != "run_workflow"]
        return run_agent(prompt, agent=build_react_agent(tools=inner))

    try:
        record = run_pipeline(plan, invoke=_invoke, persist=True)
    except Exception as e:
        return f"工作流失败: {e}"
    return format_run_report(record)


@tool
def list_workflows(limit: int = 8) -> str:
    """列出最近的工作流运行记录。"""
    from agent.workflow_engine import list_recent_runs

    return list_recent_runs(limit)


@tool
def list_doc_parsers() -> str:
    """查看文档解析引擎状态（PyMuPDF / 内置回退）。"""
    from agent.doc_parsers import engines_status_text

    return engines_status_text()


@tool
def parse_document(
    path: str,
    engine: str = "auto",
    pages: str = "",
    extra_args: str = "",
) -> str:
    """用 PyMuPDF 解析 PDF（抽文字，可按页）。适合数字 PDF；扫描件无文字层请用 describe_image。
    engine=auto|pymupdf|builtin。pages 例: \"1-5\" / \"3\"。

    Args:
        path: 文件路径
        engine: auto（默认）/ pymupdf / builtin
        pages: 页码范围（1-based），如 1-5
        extra_args: 兼容旧参数，如 pages=1-5
    """
    from pathlib import Path

    from agent.doc_parsers import format_parse_report, parse_document as _parse
    from agent.llm_client import app_dir

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (app_dir() / p).resolve()
    try:
        result = _parse(
            p,
            engine=engine or "auto",
            pages=pages or "",
            extra_args=extra_args or "",
        )
    except Exception as e:
        return f"解析失败: {e}"
    return format_parse_report(result)


@tool
def read_document(path: str, max_chars: int = 12000, pages: str = "") -> str:
    """读取本地文档文字。PDF 优先 PyMuPDF；也可 pages=\"1-5\"。txt/docx/xlsx 直接提取。"""
    from agent.doc_parsers import format_parse_report, parse_document as _parse
    from agent.file_extract import extract_text
    from agent.llm_client import app_dir

    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (app_dir() / p).resolve()
    if p.suffix.lower() == ".pdf":
        try:
            return format_parse_report(
                _parse(p, engine="auto", pages=pages or "", extra_args="")
            )
        except Exception as e:
            try:
                body = extract_text(p, max_chars=max_chars)
                return f"PyMuPDF 失败已回退（{e}）\n文件 {p.name}（{len(body)} 字）:\n{body}"
            except Exception as e2:
                return f"读取失败: {e2}"
    try:
        body = extract_text(p, max_chars=max_chars)
    except Exception as e:
        return f"读取失败: {e}"
    return f"文件 {p.name}（{len(body)} 字）:\n{body}"


@tool
def inspect_document(path: str) -> str:
    """检视文档结构（段落/样式/工作表/页数），编辑前建议先调用。"""
    from agent.doc_ops import inspect_document as _inspect

    try:
        return _inspect(path)
    except Exception as e:
        return f"检视失败: {e}"


@tool
def edit_word(
    path: str = "",
    action: str = "append",
    text: str = "",
    find: str = "",
    replace: str = "",
    style: str = "",
    font_name: str = "",
    font_size: float = 0,
    bold: bool = False,
    align: str = "",
    margin_cm: float = 0,
    output: str = "",
    inplace: bool = False,
) -> str:
    """编辑/排版 Word(.docx)。默认输出到 data/docs_out/ 副本。
    action: create | replace_all | append | format_all | set_margins
    """
    from agent.doc_ops import edit_word as _edit

    try:
        return _edit(
            path,
            action=action,
            text=text,
            find=find,
            replace=replace,
            style=style,
            font_name=font_name,
            font_size=font_size,
            bold=bold if bold else None,
            align=align,
            margin_cm=margin_cm,
            output=output,
            inplace=inplace,
        )
    except Exception as e:
        return f"Word 编辑失败: {e}"


@tool
def edit_excel(
    path: str = "",
    action: str = "write_cells",
    sheet: str = "",
    cells_json: str = "",
    text: str = "",
    font_name: str = "",
    font_size: float = 0,
    bold: bool = False,
    output: str = "",
    inplace: bool = False,
) -> str:
    """编辑/排版 Excel(.xlsx)。cells_json 如 {"A1":"姓名","B1":95}。
    action: create | write_cells | append_row | format_range
    """
    from agent.doc_ops import edit_excel as _edit

    try:
        return _edit(
            path,
            action=action,
            sheet=sheet,
            cells_json=cells_json,
            text=text,
            font_name=font_name,
            font_size=font_size,
            bold=bold if bold else None,
            output=output,
            inplace=inplace,
        )
    except Exception as e:
        return f"Excel 编辑失败: {e}"


@tool
def edit_pdf(
    path: str = "",
    action: str = "create",
    text: str = "",
    pages: str = "",
    output: str = "",
    title: str = "",
) -> str:
    """PDF 基础操作。action: create | extract_pages | merge。复杂排版用 run_document_code。"""
    from agent.doc_ops import edit_pdf as _edit

    try:
        return _edit(
            path,
            action=action,
            text=text,
            pages=pages,
            output=output,
            title=title,
        )
    except Exception as e:
        return f"PDF 操作失败: {e}"


@tool
def run_document_code(code: str, input_files_json: str = "") -> str:
    """用受限 Python 做复杂文档编辑/排版（docx/openpyxl/pypdf/reportlab）。
    预置 OUT_DIR、INPUT_FILES、Document、Workbook、load_workbook、PdfReader、canvas、Pt、Cm 等。
    输出请写入 OUT_DIR；可设 RESULT=路径。工具参数不够用时再用。
    """
    from agent.doc_code_runner import run_document_code as _run

    try:
        return _run(code, input_files_json=input_files_json)
    except Exception as e:
        return f"脚本失败: {e}"


@tool
def list_mcp() -> str:
    """查看外部 MCP 启用状态、各 server 与已加载工具名（config/mcp.yaml）。"""
    from agent.mcp_client import format_mcp_report, reload_mcp_tools

    try:
        # 若尚未加载则拉一次
        reload_mcp_tools(force=False)
        return format_mcp_report()
    except Exception as e:
        return f"读取 MCP 失败: {e}"


@tool
def reload_mcp() -> str:
    """热插拔：按当前 mcp.yaml / mcp.local.yaml 重新加载外部 MCP 工具。
    加载后需重建 Agent（下一轮对话或 UI「扩展」面板会触发）；返回加载报告。
    """
    from agent.mcp_client import format_mcp_report, reload_mcp_tools

    try:
        reload_mcp_tools(force=True)
        return format_mcp_report() + "\n\n请新开一轮对话或点「扩展→重新加载」以重建 Agent。"
    except Exception as e:
        return f"reload_mcp 失败: {e}"


@tool
def list_skills() -> str:
    """列出可用 Skills（skills/*/SKILL.md）。细则用 load_skill(name)。"""
    from agent.skills_store import format_skills_report

    try:
        return format_skills_report()
    except Exception as e:
        return f"读取 Skills 失败: {e}"


@tool
def load_skill(name: str) -> str:
    """加载指定 Skill 的完整说明正文，并按其指令执行后续步骤。"""
    from agent.skills_store import get_skill

    key = (name or "").strip()
    if not key:
        return "请提供 skill name（见 list_skills）。"
    sk = get_skill(key)
    if not sk:
        return f"未找到 skill「{key}」。先 list_skills。"
    return (
        f"# Skill: {sk.name}\n"
        f"description: {sk.description}\n"
        f"path: {sk.path}\n\n"
        f"{sk.full_text}"
    )


@tool
def list_model_providers() -> str:
    """列出全部可用模型接口与当前启用项（chat / asr / vision / image）。"""
    from agent.providers.registry import format_providers_report

    try:
        return format_providers_report()
    except Exception as e:
        return f"读取模型配置失败: {e}"


@tool
def set_chat_provider(provider_id: str, model: str = "", api_key: str = "") -> str:
    """切换对话所用的 Chat 模型 provider（如 deepseek / qwen / zhipu / moonshot /
    openai_chat / siliconflow / openrouter / ollama / custom_openai）。
    可选同时改 model 与 api_key（写入 models.local.yaml）。
    """
    from agent.providers.registry import set_active, set_provider_fields

    pid = (provider_id or "").strip()
    if not pid:
        return "请提供 provider_id。先用 list_model_providers 查看可选 id。"
    try:
        fields = {}
        if (model or "").strip():
            fields["model"] = model.strip()
        if (api_key or "").strip():
            fields["api_key"] = api_key.strip()
        notes = []
        if fields:
            notes.append(set_provider_fields(pid, **fields))
        notes.append(set_active("chat", pid))
        notes.append("下次对话将使用新模型（若程序缓存了 Agent，请新开一轮或重启）。")
        return "\n".join(notes)
    except Exception as e:
        return f"切换失败: {e}"


@tool
def register_openai_endpoint(
    base_url: str,
    model: str,
    api_key: str = "",
    provider_id: str = "custom_openai",
    set_as_chat: bool = True,
) -> str:
    """注册任意 OpenAI Chat Completions 兼容端点（通义/智谱/自建/中转站等）。
    base_url 例：https://api.xxx.com/v1 ；写入 models.local.yaml。
    """
    from agent.providers.registry import ensure_custom_openai

    try:
        return ensure_custom_openai(
            provider_id=provider_id or "custom_openai",
            base_url=base_url,
            model=model,
            api_key=api_key or "",
            set_as_chat=bool(set_as_chat),
        )
    except Exception as e:
        return f"注册失败: {e}"


@tool
def transcribe_audio(path: str, language: str = "") -> str:
    """语音识别：将本地音频转成文字。当前默认 doubao_asr（doubao-seed-2-0-lite）。"""
    from agent.providers import get_hub, reset_hub
    from agent.providers.base import ProviderError

    if not (path or "").strip():
        return "请提供音频文件路径。"
    try:
        reset_hub()
        text = get_hub().asr.transcribe(
            path.strip(),
            language=language.strip() or None,
        )
        return text or "(空识别结果)"
    except ProviderError as e:
        return str(e)
    except Exception as e:
        return f"语音识别失败: {e}"


@tool
def describe_image(path: str, prompt: str = "请描述这张图片的主要内容。") -> str:
    """图像识别/理解：描述本地图片或按 prompt 回答。默认 doubao_vision（Seed lite）。"""
    from agent.providers import get_hub, reset_hub
    from agent.providers.base import ProviderError

    if not (path or "").strip():
        return "请提供图片路径。"
    try:
        reset_hub()
        return get_hub().vision.describe(
            path.strip(),
            prompt=prompt or "请描述这张图片的主要内容。",
        )
    except ProviderError as e:
        return str(e)
    except Exception as e:
        return f"图像识别失败: {e}"


@tool
def process_image(
    task: str = "edit",
    path: str = "",
    prompt: str = "",
) -> str:
    """图像处理/生成（预留）：task 如 generate/edit/remove_bg/upscale。需启用 active.image。"""
    from agent.providers import get_hub
    from agent.providers.base import ProviderError

    try:
        out = get_hub().image.process(
            task=task or "edit",
            image_path=path.strip() or None,
            prompt=prompt.strip() or None,
        )
        return str(out)
    except ProviderError as e:
        return str(e)
    except Exception as e:
        return f"图像处理失败: {e}"


def default_tools() -> list:
    from agent.file_tools import coding_tools, session_tools

    return [
        get_clipboard,
        set_clipboard,
        open_path,
        open_app,
        open_terminal,
        run_command,
        refresh_app_index,
        list_apps,
        list_directory,
        *session_tools(),
        *coding_tools(),
        list_doc_parsers,
        parse_document,
        read_document,
        inspect_document,
        edit_word,
        edit_excel,
        edit_pdf,
        run_document_code,
        read_memory,
        update_memory,
        remember,
        recall_memories,
        forget_memory,
        clear_memories,
        reset_memories,
        save_memory,
        list_memories,
        open_memory_viewer,
        set_goal,
        get_goal,
        pause_goal,
        resume_goal,
        clear_goal,
        mark_goal_done,
        report_goal_blocked,
        run_workflow,
        list_workflows,
        append_note,
        add_alarm,
        list_notes,
        get_note,
        delete_note,
        read_notes,
        open_notes_viewer,
        add_reminder,
        list_reminders,
        cancel_reminder,
        list_model_providers,
        set_chat_provider,
        register_openai_endpoint,
        list_mcp,
        reload_mcp,
        list_skills,
        load_skill,
        transcribe_audio,
        describe_image,
        process_image,
    ]


