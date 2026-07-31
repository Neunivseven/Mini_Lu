"""本地提醒：作为 notes_store 之上的查询/到期视图（不持有独立 JSON）。

历史 ``data/reminders.json`` 会在首次加载时迁入 ``notes.json``，之后只读写记事库。
时间解析 ``parse_when`` 仍由此模块导出，供 notes_store 与工具复用。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.llm_client import data_dir


def _legacy_reminders_path() -> Path:
    p = data_dir() / "reminders.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


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
                if fmt in ("%Y-%m-%d",) or (fmt == "%H:%M" and dt <= now):
                    if fmt == "%Y-%m-%d":
                        dt = dt.replace(hour=9, minute=0, second=0)
                    elif fmt == "%H:%M" and dt <= now:
                        dt = dt + timedelta(days=1)
                return dt
            except ValueError:
                continue
    raise ValueError(f"无法解析时间: {remind_at}")


_MIGRATE_LOCK = False


def migrate_legacy_reminders(*, force: bool = False) -> int:
    """将旧 reminders.json 迁入 notes.json；成功后备份并停用旧文件。

    Returns:
        迁入条数。
    """
    global _MIGRATE_LOCK
    if _MIGRATE_LOCK:
        return 0
    path = _legacy_reminders_path()
    if not path.is_file():
        return 0
    marker = path.with_suffix(".json.migrated")
    if marker.is_file() and not force:
        return 0

    _MIGRATE_LOCK = True
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"items": []}
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []

        from agent import notes_store

        existing = {
            (str(n.get("content") or "").strip(), str(n.get("remind_at") or "").strip())
            for n in notes_store.list_notes(200)
        }
        moved = 0
        for raw in items:
            if not isinstance(raw, dict):
                continue
            if raw.get("done") or raw.get("cancelled"):
                continue
            content = str(raw.get("content") or "").strip()
            at = str(raw.get("at") or "").strip()
            if not content or not at:
                continue
            key = (content, at)
            if key in existing:
                continue
            try:
                notes_store.add_note(
                    content,
                    kind="alarm",
                    alarm_mode="once",
                    remind_at=at,
                )
                existing.add(key)
                moved += 1
            except Exception:
                continue

        try:
            bak = path.with_suffix(".json.bak")
            if path.is_file():
                if not bak.exists():
                    path.replace(bak)
                else:
                    path.unlink(missing_ok=True)
            marker.write_text(
                f"migrated_at={datetime.now().isoformat(timespec='seconds')} count={moved}\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return moved
    finally:
        _MIGRATE_LOCK = False


def add_reminder(
    content: str,
    *,
    remind_at: str | None = None,
    delay_seconds: int | None = None,
) -> dict[str, Any]:
    """兼容旧 API：写入 notes_store 一次性闹钟。"""
    migrate_legacy_reminders()
    from agent.notes_store import add_note

    return add_note(
        content,
        kind="alarm",
        alarm_mode="once",
        remind_at=remind_at,
        delay_seconds=delay_seconds,
    )


def list_pending(limit: int = 20) -> list[dict[str, Any]]:
    """兼容旧 API：列出仍开启的闹钟（视图）。"""
    migrate_legacy_reminders()
    from agent.notes_store import list_notes

    pending = [
        n
        for n in list_notes(200, kind="alarm")
        if n.get("alarm_enabled") and n.get("remind_at")
    ]
    pending.sort(key=lambda x: x.get("remind_at") or "")
    # 字段别名：at ← remind_at
    out: list[dict[str, Any]] = []
    for n in pending[: max(1, min(int(limit), 100))]:
        row = dict(n)
        row.setdefault("at", n.get("remind_at"))
        row.setdefault("done", False)
        out.append(row)
    return out


def cancel_reminder(reminder_id: str) -> bool:
    """兼容旧 API：关闭闹钟，保留记事正文。"""
    migrate_legacy_reminders()
    from agent.notes_store import clear_reminder

    return clear_reminder(reminder_id) == "ok"


def pop_due(now: datetime | None = None) -> list[dict[str, Any]]:
    """兼容旧 API：到期检查 → 委托 notes_store.pop_due_notes。"""
    migrate_legacy_reminders()
    from agent.notes_store import pop_due_notes

    due = pop_due_notes(now)
    # 补充 content / at 字段
    out: list[dict[str, Any]] = []
    for n in due:
        row = dict(n)
        row.setdefault("at", n.get("remind_at"))
        row.setdefault("content", n.get("content") or n.get("summary") or "提醒")
        out.append(row)
    return out
