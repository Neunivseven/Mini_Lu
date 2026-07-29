"""UI 字体缩放：Ctrl + 滚轮。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

_MIN = 0.75
_MAX = 1.75
_STEP = 0.1
_factor = 1.0


class _Hub(QObject):
    changed = Signal(float)  # 当前倍率


hub = _Hub()


def factor() -> float:
    return _factor


def pt(base: float | int) -> int:
    """按当前缩放换算字号（pt），下限 8。"""
    return max(8, int(round(float(base) * _factor)))


def px(base: float | int) -> str:
    """CSS font-size（如 12.5px），随倍率缩放，下限 8。"""
    v = max(8.0, round(float(base) * _factor, 1))
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}px"
    return f"{v}px"


def bump(direction: int) -> bool:
    """direction>0 放大，<0 缩小。有变化返回 True。"""
    global _factor
    if direction == 0:
        return False
    step = _STEP if direction > 0 else -_STEP
    nxt = round(_factor + step, 2)
    nxt = max(_MIN, min(_MAX, nxt))
    if abs(nxt - _factor) < 1e-6:
        return False
    _factor = nxt
    hub.changed.emit(_factor)
    return True


def reset() -> None:
    global _factor
    if abs(_factor - 1.0) < 1e-6:
        return
    _factor = 1.0
    hub.changed.emit(_factor)
