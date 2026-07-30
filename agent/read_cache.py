"""轻量进程内读缓存：短 TTL，写操作后按前缀失效。"""
from __future__ import annotations

import time
from threading import Lock
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_DEFAULT_TTL = 2.0
_lock = Lock()
_store: dict[str, tuple[float, Any]] = {}


def get(key: str, *, ttl: float = _DEFAULT_TTL) -> Any | None:
    now = time.monotonic()
    with _lock:
        item = _store.get(key)
        if not item:
            return None
        ts, val = item
        if now - ts > ttl:
            _store.pop(key, None)
            return None
        return val


def put(key: str, value: Any) -> None:
    with _lock:
        _store[key] = (time.monotonic(), value)


def invalidate(prefix: str = "") -> None:
    """清空全部，或删除 key 以 prefix 开头的条目。"""
    with _lock:
        if not prefix:
            _store.clear()
            return
        dead = [k for k in _store if k.startswith(prefix)]
        for k in dead:
            _store.pop(k, None)


def cached_call(
    key: str,
    factory: Callable[[], T],
    *,
    ttl: float = _DEFAULT_TTL,
) -> T:
    hit = get(key, ttl=ttl)
    if hit is not None:
        return hit  # type: ignore[return-value]
    val = factory()
    put(key, val)
    return val
