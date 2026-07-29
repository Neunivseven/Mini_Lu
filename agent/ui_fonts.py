"""跨平台 UI 字体：Linux 回退到 Noto / DejaVu，避免微软雅黑缺失。"""
from __future__ import annotations

import sys
from functools import lru_cache

from PySide6.QtGui import QFont, QFontDatabase


@lru_cache(maxsize=1)
def ui_font_family() -> str:
    if sys.platform.startswith("win"):
        preferred = (
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Segoe UI",
        )
    elif sys.platform == "darwin":
        preferred = ("PingFang SC", "Hiragino Sans GB", "Helvetica Neue")
    else:
        preferred = (
            "Noto Sans CJK SC",
            "Noto Sans CJK",
            "WenQuanYi Micro Hei",
            "Source Han Sans SC",
            "Droid Sans Fallback",
            "DejaVu Sans",
            "Sans Serif",
        )
    available = set(QFontDatabase.families())
    for name in preferred:
        if name in available:
            return name
    return QFont().defaultFamily() or "Sans Serif"


@lru_cache(maxsize=1)
def mono_font_family() -> str:
    if sys.platform.startswith("win"):
        preferred = ("Cascadia Code", "JetBrains Mono", "Consolas", "Courier New")
    elif sys.platform == "darwin":
        preferred = ("Menlo", "JetBrains Mono", "Monaco", "Courier")
    else:
        preferred = (
            "JetBrains Mono",
            "Fira Code",
            "DejaVu Sans Mono",
            "Noto Sans Mono",
            "Liberation Mono",
            "Monospace",
        )
    available = set(QFontDatabase.families())
    for name in preferred:
        if name in available:
            return name
    return "monospace"


def ui_font(point_size: int = 10, weight: QFont.Weight | int | None = None) -> QFont:
    f = QFont(ui_font_family(), point_size)
    if weight is not None:
        f.setWeight(weight if isinstance(weight, QFont.Weight) else QFont.Weight(weight))
    return f


def mono_font(point_size: int = 11) -> QFont:
    return QFont(mono_font_family(), point_size)


def apply_app_defaults(app) -> None:
    """给 QApplication 设默认 UI 字体。"""
    app.setFont(ui_font(10))
