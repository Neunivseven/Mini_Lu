"""待机语录：默认配置 + 用户增删，待机时随机气泡展示。"""
from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import app_dir

DEFAULT_QUOTES = [
    "发呆也是一种工作状态～",
    "要不要起来伸个懒腰？",
    "今天也辛苦啦。",
    "喝口水再继续吧。",
    "我在这儿陪着你。",
    "偶尔停下看看窗外也好。",
    "事情一件一件来就好。",
    "你已经很努力了。",
]


def config_path() -> Path:
    return app_dir() / "config" / "quotes.yaml"


def data_path() -> Path:
    p = app_dir() / "data" / "quotes.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _read_yaml_defaults() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {
            "enabled": True,
            "interval_seconds": 12,
            "chance": 0.7,
            "display_ms": 9000,
            "quotes": list(DEFAULT_QUOTES),
        }
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    quotes = data.get("quotes") or DEFAULT_QUOTES
    if not isinstance(quotes, list):
        quotes = list(DEFAULT_QUOTES)
    quotes = [str(q).strip() for q in quotes if str(q).strip()]
    return {
        "enabled": bool(data.get("enabled", True)),
        "interval_seconds": max(8, int(data.get("interval_seconds") or 12)),
        "chance": float(data.get("chance") if data.get("chance") is not None else 0.7),
        "display_ms": max(3000, int(data.get("display_ms") or 9000)),
        "quotes": quotes or list(DEFAULT_QUOTES),
    }


def _empty_from_defaults() -> dict[str, Any]:
    d = _read_yaml_defaults()
    items = [
        {
            "id": f"def_{i:03d}",
            "text": t,
            "source": "default",
            "enabled": True,
        }
        for i, t in enumerate(d["quotes"])
    ]
    return {
        "enabled": d["enabled"],
        "interval_seconds": d["interval_seconds"],
        "chance": d["chance"],
        "display_ms": d["display_ms"],
        "items": items,
    }


def _load() -> dict[str, Any]:
    path = data_path()
    if not path.exists():
        data = _empty_from_defaults()
        _save(data)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = _empty_from_defaults()
        _save(data)
        return data
    if not isinstance(data, dict):
        data = _empty_from_defaults()
    items = data.get("items")
    if not isinstance(items, list) or not items:
        # 纯字符串列表格式
        if isinstance(data.get("quotes"), list):
            items = [
                {
                    "id": uuid.uuid4().hex[:10],
                    "text": str(t).strip(),
                    "source": "user",
                    "enabled": True,
                }
                for t in data["quotes"]
                if str(t).strip()
            ]
        else:
            seeded = _empty_from_defaults()
            data.update(
                {
                    "enabled": seeded["enabled"],
                    "interval_seconds": seeded["interval_seconds"],
                    "chance": seeded["chance"],
                    "display_ms": seeded["display_ms"],
                    "items": seeded["items"],
                }
            )
            _save(data)
            return data
    normalized = []
    for raw in items:
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                continue
            normalized.append(
                {
                    "id": uuid.uuid4().hex[:10],
                    "text": text,
                    "source": "user",
                    "enabled": True,
                }
            )
            continue
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "id": str(raw.get("id") or uuid.uuid4().hex[:10]),
                "text": text,
                "source": str(raw.get("source") or "user"),
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    defaults = _read_yaml_defaults()
    data["items"] = normalized
    data["enabled"] = bool(data.get("enabled", defaults["enabled"]))
    data["interval_seconds"] = max(
        8, int(data.get("interval_seconds") or defaults["interval_seconds"])
    )
    data["chance"] = float(
        data["chance"] if data.get("chance") is not None else defaults["chance"]
    )
    data["display_ms"] = max(
        3000, int(data.get("display_ms") or defaults["display_ms"])
    )
    return data


def _save(data: dict[str, Any]) -> None:
    data_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_settings() -> dict[str, Any]:
    data = _load()
    return {
        "enabled": data["enabled"],
        "interval_seconds": data["interval_seconds"],
        "chance": data["chance"],
        "display_ms": data["display_ms"],
    }


def set_enabled(enabled: bool) -> None:
    data = _load()
    data["enabled"] = bool(enabled)
    _save(data)


def update_settings(
    *,
    enabled: bool | None = None,
    interval_seconds: int | None = None,
    chance: float | None = None,
    display_ms: int | None = None,
) -> dict[str, Any]:
    data = _load()
    if enabled is not None:
        data["enabled"] = bool(enabled)
    if interval_seconds is not None:
        data["interval_seconds"] = max(8, int(interval_seconds))
    if chance is not None:
        data["chance"] = max(0.0, min(1.0, float(chance)))
    if display_ms is not None:
        data["display_ms"] = max(3000, int(display_ms))
    _save(data)
    return get_settings()


def list_quotes() -> list[dict[str, Any]]:
    return list(_load()["items"])


def add_quote(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("语录不能为空")
    data = _load()
    item = {
        "id": uuid.uuid4().hex[:10],
        "text": text,
        "source": "user",
        "enabled": True,
    }
    data["items"].append(item)
    _save(data)
    return item


def delete_quote(quote_id: str) -> bool:
    data = _load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.get("id") != quote_id]
    if len(data["items"]) == before:
        return False
    _save(data)
    return True


def set_quote_enabled(quote_id: str, enabled: bool) -> bool:
    data = _load()
    for item in data["items"]:
        if item.get("id") == quote_id:
            item["enabled"] = bool(enabled)
            _save(data)
            return True
    return False


def reset_to_defaults() -> dict[str, Any]:
    data = _empty_from_defaults()
    _save(data)
    return data


def enabled_texts() -> list[str]:
    return [
        i["text"]
        for i in _load()["items"]
        if i.get("enabled", True) and str(i.get("text") or "").strip()
    ]


def pick_quote() -> str | None:
    """按设置决定是否弹出；返回语录文本或 None。"""
    data = _load()
    if not data.get("enabled", True):
        return None
    texts = [
        i["text"]
        for i in data["items"]
        if i.get("enabled", True) and str(i.get("text") or "").strip()
    ]
    if not texts:
        return None
    chance = float(data.get("chance") or 0)
    if chance <= 0 or random.random() > chance:
        return None
    return random.choice(texts)
