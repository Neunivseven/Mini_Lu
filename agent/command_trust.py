"""终端命令信任列表：匹配则跳过人工确认。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import app_dir


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
        try:
            if re.search(p, cmd, flags=re.IGNORECASE):
                return True
        except re.error:
            if cmd.lower().startswith(p.lower()):
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
    pat = (pattern or "").strip()
    if not pat:
        return
    data = _read()
    pats = [str(x) for x in (data.get("trusted_patterns") or [])]
    if pat not in pats:
        pats.append(pat)
    data["trusted_patterns"] = pats
    _write(data)


def clear_trust() -> None:
    _write({"trusted_patterns": [], "trusted_exact": []})
