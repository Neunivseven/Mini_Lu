"""本地提醒存储与到期查询（JSON）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir


def _reminders_path() -> Path:
    p = app_dir() / "data" / "reminders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text('{"items": []}\n', encoding="utf-8")
    return p


def _load() -> dict[str, Any]:
    path = _reminders_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {"items": []}
    if not isinstance(data, dict):
        data = {"items": []}
    items = data.get("items")
    if not isinstance(items, list):
        data["items"] = []
    return data


def _save(data: dict[str, Any]) -> None:
    path = _reminders_path()
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_when(
    *,
    remind_at: str | None = None,
    delay_seconds: int | None = None,
) -> datetime:
    """解析提醒时间：绝对时间或相对秒数。"""
    now = datetime.now()
    if delay_seconds is not None:
        sec = int(delay_seconds)
        if sec < 1:
            raise ValueError("delay_seconds 至少为 1")
        if sec > 366 * 24 * 3600:
            raise ValueError("延迟过长（超过约一年）")
        return now + timedelta(seconds=sec)

    text = (remind_at or "").strip()
    if not text:
        raise ValueError("请提供 remind_at（如 2026-07-26 15:00）或 delay_seconds")

    # 支持常见格式
    candidates = [
        text,
        text.replace("/", "-"),
        text.replace("T", " "),
    ]
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m-%d %H:%M",
        "%H:%M",
    )
    for cand in candidates:
        for fmt in formats:
            try:
                dt = datetime.strptime(cand, fmt)
                if fmt == "%H:%M":
                    dt = dt.replace(year=now.year, month=now.month, day=now.day)
                elif fmt == "%m-%d %H:%M":
                    dt = dt.replace(year=now.year)
                if fmt in ("%Y-%m-%d",) or (
                    fmt == "%H:%M" and dt <= now
                ):
                    # 仅日期 → 当天 09:00；仅时刻已过 → 明天
                    if fmt == "%Y-%m-%d":
                        dt = dt.replace(hour=9, minute=0, second=0)
                    elif fmt == "%H:%M" and dt <= now:
                        dt = dt + timedelta(days=1)
                return dt
            except ValueError:
                continue
    raise ValueError(f"无法解析时间: {remind_at}")


def add_reminder(
    content: str,
    *,
    remind_at: str | None = None,
    delay_seconds: int | None = None,
) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise ValueError("提醒内容为空")
    when = parse_when(remind_at=remind_at, delay_seconds=delay_seconds)
    item = {
        "id": uuid.uuid4().hex[:10],
        "content": content,
        "at": when.strftime("%Y-%m-%d %H:%M:%S"),
        "done": False,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    data = _load()
    data["items"].append(item)
    _save(data)
    return item


def list_pending(limit: int = 20) -> list[dict[str, Any]]:
    data = _load()
    pending = [i for i in data["items"] if not i.get("done")]
    pending.sort(key=lambda x: x.get("at") or "")
    return pending[: max(1, min(int(limit), 100))]


def cancel_reminder(reminder_id: str) -> bool:
    rid = (reminder_id or "").strip()
    if not rid:
        return False
    data = _load()
    found = False
    for item in data["items"]:
        if item.get("id") == rid and not item.get("done"):
            item["done"] = True
            item["cancelled"] = True
            found = True
            break
    if found:
        _save(data)
    return found


def pop_due(now: datetime | None = None) -> list[dict[str, Any]]:
    """取出所有已到期且未完成的提醒，并标记为 done。"""
    now = now or datetime.now()
    data = _load()
    due: list[dict[str, Any]] = []
    changed = False
    for item in data["items"]:
        if item.get("done"):
            continue
        try:
            at = datetime.strptime(item["at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if at <= now:
            item["done"] = True
            item["fired_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
            due.append(dict(item))
            changed = True
    if changed:
        _save(data)
    return due
