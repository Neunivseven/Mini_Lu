"""
终端能力：
1) open_terminal — 打开交互式终端窗口
2) run_command — Agent 在工作区执行命令并回收 stdout/stderr（编译/测试闭环）
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_DEFAULT_TIMEOUT = 60
_MAX_TIMEOUT = 600
_MAX_OUTPUT_CHARS = 12000

_BLOCK_PATTERNS = (
    "format ",
    "format.com",
    "rm -rf /",
    "rm -rf /*",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    "remove-item -recurse -force c:\\",
    "shutdown",
    "restart-computer",
    "stop-computer",
    "diskpart",
    "cipher /w",
    ":(){:|:&};:",
)


def _resolve_cwd(cwd: str = "") -> Path:
    raw = (cwd or "").strip()
    if raw:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            try:
                from agent.file_workspace import get_active_root
                from agent.llm_client import app_dir

                base = get_active_root() or app_dir()
                p = (base / p).resolve()
            except Exception:
                p = p.resolve()
        else:
            p = p.resolve()
    else:
        try:
            from agent.file_workspace import get_active_root
            from agent.llm_client import app_dir

            p = get_active_root() or app_dir()
        except Exception:
            p = Path.cwd()
    if p.is_file():
        p = p.parent
    if not p.is_dir():
        raise FileNotFoundError(f"目录不存在: {p}")
    return p


def _which(name: str) -> str | None:
    return shutil.which(name)


def _detect_shell(preferred: str = "auto") -> tuple[str, str]:
    """返回 (kind, executable)。开窗用。"""
    pref = (preferred or "auto").strip().lower()
    if pref in ("wt", "windows-terminal", "windowsterminal"):
        exe = _which("wt") or _which("wt.exe")
        if exe:
            return "wt", exe
        raise FileNotFoundError("未找到 Windows Terminal (wt.exe)")
    if pref in ("pwsh", "powershell7", "ps7"):
        exe = _which("pwsh") or _which("pwsh.exe")
        if exe:
            return "pwsh", exe
        raise FileNotFoundError("未找到 PowerShell 7 (pwsh)")
    if pref in ("powershell", "ps", "ps5"):
        exe = (
            _which("powershell")
            or _which("powershell.exe")
            or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        )
        if Path(exe).is_file() or _which("powershell"):
            return "powershell", exe if Path(exe).is_file() else (_which("powershell") or exe)
        raise FileNotFoundError("未找到 Windows PowerShell")
    if pref in ("cmd", "command", "commandprompt"):
        exe = _which("cmd") or _which("cmd.exe") or r"C:\Windows\System32\cmd.exe"
        return "cmd", exe
    if pref in ("bash", "git-bash"):
        for c in (
            _which("bash"),
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ):
            if c and Path(c).is_file():
                return "bash", c
        raise FileNotFoundError("未找到 bash（可安装 Git for Windows）")

    if sys.platform.startswith("win"):
        wt = _which("wt") or _which("wt.exe")
        if wt:
            return "wt", wt
        pwsh = _which("pwsh") or _which("pwsh.exe")
        if pwsh:
            return "pwsh", pwsh
        ps = _which("powershell") or _which("powershell.exe")
        if ps:
            return "powershell", ps
        return "cmd", _which("cmd") or r"C:\Windows\System32\cmd.exe"
    if sys.platform == "darwin":
        return "terminal", "/usr/bin/open"
    for name in ("gnome-terminal", "konsole", "xfce4-terminal", "x-terminal-emulator", "xterm"):
        exe = _which(name)
        if exe:
            return name, exe
    raise FileNotFoundError("未找到可用的终端程序")


def _detect_exec_shell(preferred: str = "auto") -> tuple[str, str]:
    """执行命令用的 shell（不用 wt 开窗）。"""
    pref = (preferred or "auto").strip().lower()
    if pref in ("auto", "", "wt", "windows-terminal"):
        if sys.platform.startswith("win"):
            pwsh = _which("pwsh") or _which("pwsh.exe")
            if pwsh:
                return "pwsh", pwsh
            ps = _which("powershell") or _which("powershell.exe")
            if ps:
                return "powershell", ps
            return "cmd", _which("cmd") or r"C:\Windows\System32\cmd.exe"
        bash = _which("bash") or "/bin/bash"
        return "bash", bash
    return _detect_shell(pref)


def _build_argv(kind: str, exe: str, cwd: Path, title: str = "") -> list[str]:
    title = (title or "").strip() or f"Mini_Lu · {cwd.name}"
    if kind == "wt":
        inner = _which("pwsh") or _which("powershell") or "powershell"
        return [exe, "-d", str(cwd), inner]
    if kind in ("pwsh", "powershell"):
        args = [exe, "-NoExit", "-NoLogo"]
        cmd = f"Set-Location -LiteralPath '{str(cwd).replace(chr(39), chr(39)+chr(39))}';"
        if title:
            cmd = f"$Host.UI.RawUI.WindowTitle = '{title.replace(chr(39), '')}'; " + cmd
        args += ["-Command", cmd]
        return args
    if kind == "cmd":
        return [exe, "/k", f"cd /d {cwd} & title {title}"]
    if kind == "bash":
        return [exe, "--login", "-i"]
    if kind == "terminal" and sys.platform == "darwin":
        return [exe, "-a", "Terminal", str(cwd)]
    if kind == "gnome-terminal":
        return [exe, f"--working-directory={cwd}"]
    if kind == "konsole":
        return [exe, "--workdir", str(cwd)]
    if kind == "xfce4-terminal":
        return [exe, f"--working-directory={cwd}"]
    if kind in ("x-terminal-emulator", "xterm"):
        # 通用兜底：进目录后开交互 shell
        return [exe, "-e", f"bash -lc 'cd {cwd!s} && exec bash'"]
    return [exe]


def open_terminal(
    *,
    cwd: str = "",
    shell: str = "auto",
    title: str = "",
) -> dict[str, Any]:
    try:
        work = _resolve_cwd(cwd)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    try:
        kind, exe = _detect_shell(shell)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    argv = _build_argv(kind, exe, work, title=title)
    try:
        kwargs: dict[str, Any] = {
            "cwd": str(work) if kind != "wt" else None,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
            "close_fds": True,
        }
        if kwargs.get("cwd") is None:
            kwargs.pop("cwd", None)
        if sys.platform.startswith("win"):
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            if kind == "wt":
                flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            kwargs["creationflags"] = flags
            kwargs["start_new_session"] = True
        else:
            kwargs["start_new_session"] = True

        subprocess.Popen(argv, **kwargs)
    except Exception as e:
        return {"ok": False, "error": f"启动失败: {e}", "argv": argv}

    return {
        "ok": True,
        "shell": kind,
        "exe": exe,
        "cwd": str(work),
        "argv": argv,
        "message": f"已启动终端（{kind}）\n目录: {work}",
    }


def format_open_terminal(result: dict[str, Any]) -> str:
    if not result:
        return "启动失败"
    if not result.get("ok"):
        return f"启动终端失败: {result.get('error') or result}"
    return str(result.get("message") or f"已启动终端 @ {result.get('cwd')}")


def _blocked(command: str) -> str | None:
    low = (command or "").strip().lower()
    if not low:
        return "命令为空"
    for pat in _BLOCK_PATTERNS:
        if pat in low:
            return f"出于安全拒绝执行（匹配危险模式: {pat.strip()}）"
    return None


def _build_exec_argv(kind: str, exe: str, command: str) -> list[str]:
    if kind in ("pwsh", "powershell"):
        preamble = (
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
            "$OutputEncoding = [Console]::OutputEncoding; "
        )
        return [
            exe,
            "-NoProfile",
            "-NoLogo",
            "-NonInteractive",
            "-Command",
            preamble + command,
        ]
    if kind == "cmd":
        return [exe, "/d", "/c", command]
    return [exe, "-lc", command]


def _decode_bytes(data: bytes) -> str:
    if not data:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _clip(text: str, limit: int = _MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    half = limit // 2
    return text[:half] + "\n…(输出过长已截断)…\n" + text[-half:], True


def run_command(
    command: str,
    *,
    cwd: str = "",
    shell: str = "auto",
    timeout_seconds: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """在工作区执行命令，捕获 stdout/stderr/exit_code。"""
    command = (command or "").strip()
    blocked = _blocked(command)
    if blocked:
        return {"ok": False, "error": blocked, "command": command}

    try:
        work = _resolve_cwd(cwd)
    except Exception as e:
        return {"ok": False, "error": str(e), "command": command}

    try:
        kind, exe = _detect_exec_shell(shell)
    except Exception as e:
        return {"ok": False, "error": str(e), "command": command}

    timeout = float(timeout_seconds or _DEFAULT_TIMEOUT)
    timeout = max(1.0, min(timeout, float(_MAX_TIMEOUT)))
    argv = _build_exec_argv(kind, exe, command)

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")

    kwargs: dict[str, Any] = {
        "cwd": str(work),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    t0 = time.perf_counter()
    try:
        proc = subprocess.run(argv, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as e:
        out, _ = _clip(_decode_bytes(e.stdout or b""))
        err, _ = _clip(_decode_bytes(e.stderr or b""))
        return {
            "ok": False,
            "error": f"超时（>{timeout:.0f}s）",
            "command": command,
            "shell": kind,
            "cwd": str(work),
            "exit_code": None,
            "timed_out": True,
            "stdout": out,
            "stderr": err,
            "elapsed_seconds": round(time.perf_counter() - t0, 3),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "command": command,
            "shell": kind,
            "cwd": str(work),
        }

    stdout, so_cut = _clip(_decode_bytes(proc.stdout or b""))
    stderr, se_cut = _clip(_decode_bytes(proc.stderr or b""))
    code = int(proc.returncode)
    return {
        "ok": code == 0,
        "command": command,
        "shell": kind,
        "cwd": str(work),
        "exit_code": code,
        "timed_out": False,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": so_cut or se_cut,
        "elapsed_seconds": round(time.perf_counter() - t0, 3),
        "error": None if code == 0 else f"退出码 {code}",
    }


def format_run_command(result: dict[str, Any]) -> str:
    if not result:
        return "执行失败"
    lines = [
        f"$ {result.get('command')}",
        f"cwd={result.get('cwd')} shell={result.get('shell')} "
        f"exit={result.get('exit_code')} elapsed={result.get('elapsed_seconds')}s",
    ]
    if result.get("timed_out"):
        lines.append("状态: 超时")
    elif result.get("ok"):
        lines.append("状态: 成功")
    else:
        lines.append(f"状态: 失败 — {result.get('error') or ''}")
    out = (result.get("stdout") or "").rstrip()
    err = (result.get("stderr") or "").rstrip()
    if out:
        lines.append("--- stdout ---")
        lines.append(out)
    if err:
        lines.append("--- stderr ---")
        lines.append(err)
    if result.get("truncated"):
        lines.append("（输出已截断）")
    if not out and not err and result.get("ok"):
        lines.append("（无输出）")
    return "\n".join(lines)
