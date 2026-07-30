"""终端命令信任列表：匹配则跳过人工确认。

默认只应使用「完整命令」精确信任（UI「总是允许」即如此）。
正则 / 模式信任仅建议手改配置文件，且过宽模式会被拒绝或忽略。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import app_dir

# 视为过宽、不可用于自动放行的模式
_BROAD_EXACT = {
    ".*",
    ".+",
    "^",
    "$",
    ".*$",
    "^.*",
    "^.*$",
    ".?",
    "[\\s\\S]*",
    "[\\d\\D]*",
}


def _path() -> Path:
    return app_dir() / "config" / "command_trust.local.yaml"


def _read() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {"trusted_patterns": [], "trusted_exact": []}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {"trusted_patterns": [], "trusted_exact": []}
    if not isinstance(data, dict):
        return {"trusted_patterns": [], "trusted_exact": []}
    data.setdefault("trusted_patterns", [])
    data.setdefault("trusted_exact", [])
    return data


def _write(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def list_trust() -> dict[str, list[str]]:
    d = _read()
    return {
        "trusted_patterns": [str(x) for x in (d.get("trusted_patterns") or [])],
        "trusted_exact": [str(x) for x in (d.get("trusted_exact") or [])],
    }


def pattern_risk(pattern: str) -> str | None:
    """若模式过宽返回风险说明，否则 None。"""
    p = (pattern or "").strip()
    if not p:
        return "空模式"
    if p in _BROAD_EXACT:
        return "会匹配几乎任意命令"
    # 无锚点的短词：re.search("pip") 会命中任何含 pip 的命令
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,12}", p) and not p.startswith("^"):
        return f"无锚点短词「{p}」易在命令中间误匹配；请用 ^git status 这类前缀，或改用精确信任"
    # 仅 ^ 加短词且无后续约束
    m = re.fullmatch(r"\^([A-Za-z0-9_.-]{1,8})$", p)
    if m and len(m.group(1)) <= 3:
        return f"前缀过短「{p}」仍可能放行过多命令"
    try:
        compiled = re.compile(p)
    except re.error:
        # 非法正则时按前缀；过短前缀同样危险
        if len(p) < 6:
            return f"非法正则且前缀过短「{p}」"
        return None
    # 空匹配 / 匹配空串
    if compiled.search(""):
        return "模式可匹配空串，过于宽泛"
    # 对一组样本：若多数「无关」命令也被匹配则拒
    probes = (
        "echo hello",
        "rm -rf /",
        "curl http://evil.example",
        "python -c 'print(1)'",
        "sudo reboot",
    )
    hits = sum(1 for s in probes if compiled.search(s))
    if hits >= 3:
        return "模式对多种无关命令均命中，过于宽泛"
    return None


def is_trusted(command: str) -> bool:
    cmd = (command or "").strip()
    if not cmd:
        return False
    data = _read()
    for ex in data.get("trusted_exact") or []:
        if cmd == str(ex).strip():
            return True
    for pat in data.get("trusted_patterns") or []:
        p = str(pat).strip()
        if not p:
            continue
        # 已写入的过宽模式一律忽略（防御配置误改）
        if pattern_risk(p):
            continue
        try:
            if re.search(p, cmd, flags=re.IGNORECASE):
                return True
        except re.error:
            if len(p) >= 6 and cmd.lower().startswith(p.lower()):
                return True
    return False


def trust_exact(command: str) -> None:
    cmd = (command or "").strip()
    if not cmd:
        return
    data = _read()
    exact = [str(x) for x in (data.get("trusted_exact") or [])]
    if cmd not in exact:
        exact.append(cmd)
    data["trusted_exact"] = exact
    _write(data)


def trust_pattern(pattern: str) -> None:
    """写入正则信任；过宽模式拒绝（请改用 trust_exact 或收窄正则）。"""
    pat = (pattern or "").strip()
    if not pat:
        return
    risk = pattern_risk(pat)
    if risk:
        raise ValueError(
            f"拒绝写入过宽信任模式「{pat}」：{risk}。"
            "UI「总是允许」只信任完整命令；模式请手改配置并确保足够具体（如 ^git status）。"
        )
    data = _read()
    pats = [str(x) for x in (data.get("trusted_patterns") or [])]
    if pat not in pats:
        pats.append(pat)
    data["trusted_patterns"] = pats
    _write(data)


def clear_trust() -> None:
    _write({"trusted_patterns": [], "trusted_exact": []})
