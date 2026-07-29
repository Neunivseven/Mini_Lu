"""工作台用户偏好：布局习惯与出厂默认，可重置。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir

# 出厂默认（勿被用户文件覆盖；重置时写回）
DEFAULT_LAYOUT: dict[str, Any] = {
    "main_split": [180, 0, 380, 400, 200, 0],
    "chat_split": [420, 160],
    "files_split": [520, 0],
    "left_collapsed": False,
    "files_collapsed": False,
    "window_size": [1280, 780],
    "models_tab_open": False,
}


def prefs_path() -> Path:
    d = app_dir() / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d / "studio_prefs.yaml"


def factory_layout() -> dict[str, Any]:
    return deepcopy(DEFAULT_LAYOUT)


def _merge_layout(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = factory_layout()
    if not isinstance(raw, dict):
        return out
    for key, default in DEFAULT_LAYOUT.items():
        if key not in raw:
            continue
        val = raw[key]
        if isinstance(default, list):
            if isinstance(val, list) and len(val) == len(default):
                try:
                    out[key] = [max(0, int(x)) for x in val]
                except Exception:
                    pass
        elif isinstance(default, bool):
            out[key] = bool(val)
        else:
            out[key] = val
    return out


def load_layout() -> dict[str, Any]:
    p = prefs_path()
    if not p.is_file():
        return factory_layout()
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return factory_layout()
        return _merge_layout(data.get("layout"))
    except Exception:
        return factory_layout()


def save_layout(layout: dict[str, Any]) -> None:
    """保存用户布局；保留文件中其它字段。"""
    merged = _merge_layout(layout)
    try:
        import yaml

        p = prefs_path()
        data: dict[str, Any] = {}
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                data = raw
        data["layout"] = merged
        # 使用痕迹：最近一次保存时间，便于排查习惯是否生效
        from datetime import datetime, timezone

        data["layout_saved_at"] = datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"
        )
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def reset_layout() -> dict[str, Any]:
    """恢复出厂布局并写回偏好文件。"""
    layout = factory_layout()
    save_layout(layout)
    return layout


def append_usage_event(event: str, detail: dict[str, Any] | None = None) -> None:
    """追加轻量使用痕迹（滚动保留最近 80 条）。"""
    try:
        import yaml
        from datetime import datetime, timezone

        p = prefs_path()
        data: dict[str, Any] = {}
        if p.is_file():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                data = raw
        log = data.get("usage_log")
        if not isinstance(log, list):
            log = []
        entry: dict[str, Any] = {
            "at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "event": str(event),
        }
        if detail:
            entry["detail"] = detail
        log.append(entry)
        data["usage_log"] = log[-80:]
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass
