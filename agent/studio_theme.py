"""工作台色彩主题。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.llm_client import config_write_path


@dataclass(frozen=True)
class StudioTheme:
    id: str
    label: str
    cloth: str  # 主色 / 强调
    bg: str
    surface: str
    panel: str
    ink: str
    muted: str
    border: str
    nav_bg: str
    nav_fg: str
    nav_hover: str
    ws_bg: str
    ws_border: str
    list_sel: str
    list_hover: str
    ghost_bg: str
    ghost_hover: str
    split_handle: str
    is_dark: bool = False


THEMES: dict[str, StudioTheme] = {
    "white": StudioTheme(
        id="white",
        label="白色",
        cloth="#3D7EA6",
        bg="#F3F6FA",
        surface="#E8EEF5",
        panel="#FFFFFF",
        ink="#1E293B",
        muted="#64748B",
        border="#C5D0DC",
        nav_bg="#EEF2F7",
        nav_fg="#334155",
        nav_hover="#DCE4EE",
        ws_bg="#DCEAF4",
        ws_border="#B6D0E2",
        list_sel="#D7E8F4",
        list_hover="#EEF4F9",
        ghost_bg="#E8EEF5",
        ghost_hover="#D9E2EC",
        split_handle="#D5DEE8",
    ),
    "silver": StudioTheme(
        id="silver",
        label="银色",
        cloth="#5B6B7C",
        bg="#ECEFF2",
        surface="#E2E6EA",
        panel="#F7F8FA",
        ink="#1F2933",
        muted="#6B7280",
        border="#C5CAD1",
        nav_bg="#E4E8EC",
        nav_fg="#374151",
        nav_hover="#D5DAE0",
        ws_bg="#DDE3E9",
        ws_border="#C0C8D0",
        list_sel="#D5DCE4",
        list_hover="#EBEEF2",
        ghost_bg="#E2E6EA",
        ghost_hover="#D0D5DB",
        split_handle="#CBD2D9",
    ),
    "warm": StudioTheme(
        id="warm",
        label="暖色",
        cloth="#C4784A",
        bg="#F7F1EA",
        surface="#EFE6DB",
        panel="#FFFBF7",
        ink="#3B2F2A",
        muted="#8A7366",
        border="#D9C8B6",
        nav_bg="#F0E6DA",
        nav_fg="#5C4033",
        nav_hover="#E5D5C4",
        ws_bg="#EADCCB",
        ws_border="#D4C0A8",
        list_sel="#E8D5C0",
        list_hover="#F3EBE2",
        ghost_bg="#EFE6DB",
        ghost_hover="#E0D2C2",
        split_handle="#D9C8B6",
    ),
    "slate": StudioTheme(
        id="slate",
        label="暗灰",
        cloth="#7AA2C4",
        bg="#2B3038",
        surface="#343A44",
        panel="#1F242B",
        ink="#E8EDF4",
        muted="#9AA6B5",
        border="#4A5563",
        nav_bg="#252A32",
        nav_fg="#D1D9E4",
        nav_hover="#3A424E",
        ws_bg="#2F3640",
        ws_border="#4A5563",
        list_sel="#3D4F63",
        list_hover="#343C48",
        ghost_bg="#343A44",
        ghost_hover="#404854",
        split_handle="#4A5563",
        is_dark=True,
    ),
    "black": StudioTheme(
        id="black",
        label="黑色",
        cloth="#5B9FD4",
        bg="#121418",
        surface="#1A1D24",
        panel="#0E1014",
        ink="#E6EAF0",
        muted="#8B95A5",
        border="#2C3340",
        nav_bg="#0A0C10",
        nav_fg="#C5CDD8",
        nav_hover="#22262F",
        ws_bg="#161A20",
        ws_border="#2C3340",
        list_sel="#1E3A55",
        list_hover="#1A222C",
        ghost_bg="#1A1D24",
        ghost_hover="#262B35",
        split_handle="#2C3340",
        is_dark=True,
    ),
}


def theme_path() -> Path:
    return config_write_path("ui_theme.yaml")


def load_theme_id() -> str:
    p = theme_path()
    if not p.is_file():
        return "white"
    try:
        import yaml

        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        tid = str((data or {}).get("studio_theme") or "white").strip()
        return tid if tid in THEMES else "white"
    except Exception:
        return "white"


def save_theme_id(theme_id: str) -> None:
    tid = theme_id if theme_id in THEMES else "white"
    try:
        import yaml

        p = theme_path()
        data: dict[str, Any] = {}
        if p.is_file():
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(data, dict):
                data = {}
        data["studio_theme"] = tid
        p.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    except Exception:
        pass


def get_theme(theme_id: str | None = None) -> StudioTheme:
    tid = theme_id or load_theme_id()
    return THEMES.get(tid) or THEMES["white"]
