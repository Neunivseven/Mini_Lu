"""结构化记事 + 闹钟：普通记事不必提醒；闹钟分一次性 / 长期重复。"""
from __future__ import annotations

import json
import re
import uuid
from calendar import monthrange
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agent.llm_client import app_dir
from agent.reminders import parse_when

# kind: note=纯记事（不响铃） / alarm=闹钟（到点冒泡）
# alarm_mode: none | once | repeat
REPEAT_RULES = ("daily", "weekly", "weekdays", "monthly")


def notes_path() -> Path:
    p = app_dir() / "data" / "notes.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def legacy_md_path() -> Path:
    return app_dir() / "data" / "notes.md"


def _empty() -> dict[str, Any]:
    return {"items": []}


def _load() -> dict[str, Any]:
    path = notes_path()
    if not path.exists():
        data = _migrate_from_md()
        _save(data)
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = _empty()
    if not isinstance(data, dict):
        data = _empty()
    if not isinstance(data.get("items"), list):
        data["items"] = []
    # 懒迁移字段
    changed = False
    normalized = []
    for raw in data["items"]:
        item, dirty = _normalize_item(raw)
        normalized.append(item)
        changed = changed or dirty
    data["items"] = normalized
    if changed:
        _save(data)
    return data


def _save(data: dict[str, Any]) -> None:
    notes_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _make_summary(content: str, summary: str | None = None, max_len: int = 36) -> str:
    text = (summary or "").strip()
    if not text:
        text = (content or "").strip().splitlines()[0] if content.strip() else "（无标题）"
    text = re.sub(r"\s+", " ", text)
    if len(text) > max_len:
        return text[: max_len - 1] + "…"
    return text


def _normalize_item(raw: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """补齐 kind / alarm_mode / repeat / alarm_enabled。"""
    item = dict(raw)
    dirty = False

    kind = item.get("kind")
    if kind not in ("note", "alarm"):
        kind = "alarm" if item.get("remind_at") else "note"
        item["kind"] = kind
        dirty = True

    mode = item.get("alarm_mode")
    if mode not in ("none", "once", "repeat"):
        if kind == "alarm" and item.get("remind_at"):
            mode = "once"
        else:
            mode = "none"
        item["alarm_mode"] = mode
        dirty = True

    repeat = item.get("repeat") or "none"
    if repeat not in ("none",) + REPEAT_RULES:
        repeat = "none"
        item["repeat"] = repeat
        dirty = True
    elif "repeat" not in item:
        item["repeat"] = repeat
        dirty = True

    if "alarm_enabled" not in item:
        if kind == "alarm" and item.get("remind_at") and not item.get("reminded"):
            item["alarm_enabled"] = True
        elif kind == "alarm" and mode == "repeat" and item.get("remind_at"):
            item["alarm_enabled"] = True
        else:
            item["alarm_enabled"] = bool(
                kind == "alarm" and item.get("remind_at") and not item.get("reminded")
            )
        dirty = True

    if "reminded" not in item:
        item["reminded"] = False
        dirty = True

    # 已响完的一次性：降为纯记事展示
    if (
        item.get("reminded")
        and not item.get("alarm_enabled")
        and item.get("kind") == "alarm"
        and (item.get("alarm_mode") or "once") != "repeat"
    ):
        item["kind"] = "note"
        item["alarm_mode"] = "none"
        dirty = True

    return item, dirty


def _migrate_from_md() -> dict[str, Any]:
    md = legacy_md_path()
    items: list[dict[str, Any]] = []
    if not md.exists():
        return {"items": items}
    text = md.read_text(encoding="utf-8")
    parts = re.split(r"^##\s+", text, flags=re.M)
    for part in parts:
        part = part.strip()
        if not part or part.startswith("# 桌宠记事"):
            continue
        lines = part.splitlines()
        head = lines[0].strip()
        body = "\n".join(lines[1:]).strip() if len(lines) > 1 else head
        created = (
            head
            if re.match(r"\d{4}-\d{2}-\d{2}", head)
            else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        if re.match(r"\d{4}-\d{2}-\d{2}", head) and body:
            content = body
        else:
            content = part
            created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if len(created) <= 16:
            created = created + ":00"
        items.append(
            {
                "id": uuid.uuid4().hex[:10],
                "kind": "note",
                "summary": _make_summary(content),
                "content": content,
                "created": created,
                "remind_at": None,
                "reminded": False,
                "alarm_mode": "none",
                "repeat": "none",
                "alarm_enabled": False,
            }
        )
    return {"items": items}


def _add_months(dt: datetime, months: int = 1) -> datetime:
    y = dt.year + (dt.month - 1 + months) // 12
    m = (dt.month - 1 + months) % 12 + 1
    last = monthrange(y, m)[1]
    d = min(dt.day, last)
    return dt.replace(year=y, month=m, day=d)


def advance_next_at(at: datetime, repeat: str) -> datetime:
    """按重复规则计算下一次响铃时间（至少推到「现在之后」由调用方循环）。"""
    if repeat == "weekly":
        return at + timedelta(weeks=1)
    if repeat == "weekdays":
        nxt = at + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    if repeat == "monthly":
        return _add_months(at, 1)
    # daily 默认
    return at + timedelta(days=1)


def add_note(
    content: str,
    *,
    summary: str | None = None,
    kind: str = "note",
    alarm_mode: str = "none",
    remind_at: str | None = None,
    delay_seconds: int | None = None,
    repeat: str = "none",
) -> dict[str, Any]:
    """
    kind=note：纯记事，不响铃（忽略时间参数）。
    kind=alarm：闹钟；alarm_mode=once|repeat，需提供时间。
    """
    content = (content or "").strip()
    if not content:
        raise ValueError("内容为空")

    kind = (kind or "note").strip().lower()
    if kind not in ("note", "alarm"):
        kind = "note"

    alarm_mode = (alarm_mode or "none").strip().lower()
    repeat = (repeat or "none").strip().lower()
    if repeat not in ("none",) + REPEAT_RULES:
        raise ValueError(f"不支持的重复规则: {repeat}（可用 daily/weekly/weekdays/monthly）")

    remind_value = None
    enabled = False
    reminded = False

    if kind == "alarm":
        if alarm_mode not in ("once", "repeat"):
            # 有时间则默认 once；重复规则则 repeat
            alarm_mode = "repeat" if repeat != "none" else "once"
        if alarm_mode == "repeat" and repeat == "none":
            repeat = "daily"
        if not delay_seconds and not (remind_at and str(remind_at).strip()):
            raise ValueError("闹钟必须指定 remind_at 或 delay_seconds")
        when = parse_when(
            remind_at=remind_at if remind_at else None,
            delay_seconds=int(delay_seconds) if delay_seconds else None,
        )
        remind_value = when.strftime("%Y-%m-%d %H:%M:%S")
        enabled = True
    else:
        alarm_mode = "none"
        repeat = "none"

    item = {
        "id": uuid.uuid4().hex[:10],
        "kind": kind,
        "summary": _make_summary(content, summary),
        "content": content,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "remind_at": remind_value,
        "reminded": reminded,
        "alarm_mode": alarm_mode,
        "repeat": repeat,
        "alarm_enabled": enabled,
    }
    data = _load()
    data["items"].insert(0, item)
    _save(data)
    return item


def list_notes(limit: int = 50, *, kind: str | None = None) -> list[dict[str, Any]]:
    items = list(_load()["items"])
    if kind in ("note", "alarm"):
        items = [i for i in items if i.get("kind") == kind]
    return items[: max(1, min(int(limit), 200))]


def get_note(note_id: str) -> dict[str, Any] | None:
    rid = (note_id or "").strip()
    for item in _load()["items"]:
        if item.get("id") == rid:
            return dict(item)
    return None


def delete_note(note_id: str) -> bool:
    data = _load()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.get("id") != note_id]
    if len(data["items"]) == before:
        return False
    _save(data)
    return True


def clear_reminder(note_id: str) -> str:
    """关闭闹钟，保留记事（kind 可保留为 alarm 但禁用，或降为 note）。"""
    data = _load()
    for item in data["items"]:
        if item.get("id") != (note_id or "").strip():
            continue
        if item.get("kind") != "alarm" and not item.get("remind_at"):
            return "none"
        if not item.get("alarm_enabled") and item.get("reminded"):
            return "none"
        item["alarm_enabled"] = False
        item["remind_at"] = None
        item["alarm_mode"] = "none"
        item["repeat"] = "none"
        item["kind"] = "note"
        _save(data)
        return "ok"
    return "missing"


def pop_due_notes(now: datetime | None = None) -> list[dict[str, Any]]:
    """取出到期闹钟；一次性标记完成，重复则推进下次时间。"""
    now = now or datetime.now()
    data = _load()
    due: list[dict[str, Any]] = []
    changed = False
    for item in data["items"]:
        if item.get("kind") != "alarm":
            continue
        if not item.get("alarm_enabled"):
            continue
        if not item.get("remind_at"):
            continue
        try:
            at = datetime.strptime(item["remind_at"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        if at > now:
            continue

        fired = dict(item)
        due.append(fired)
        changed = True
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")
        item["reminded_at"] = stamp

        mode = item.get("alarm_mode") or "once"
        if mode == "repeat" and (item.get("repeat") or "none") != "none":
            nxt = at
            # 连续推进直到严格大于 now（避免进程挂起后连响）
            guard = 0
            while nxt <= now and guard < 400:
                nxt = advance_next_at(nxt, item.get("repeat") or "daily")
                guard += 1
            item["remind_at"] = nxt.strftime("%Y-%m-%d %H:%M:%S")
            item["reminded"] = False
            item["alarm_enabled"] = True
        else:
            # 一次性：响过后保留记事正文，关闭闹钟
            item["reminded"] = True
            item["alarm_enabled"] = False
            item["kind"] = "note"
            item["alarm_mode"] = "none"
    if changed:
        _save(data)
    return due


def _alarm_label(n: dict[str, Any]) -> str:
    if n.get("kind") != "alarm" or not n.get("alarm_enabled"):
        if n.get("reminded") and n.get("reminded_at"):
            return " · 已响过"
        return ""
    at = n.get("remind_at") or ""
    short = at[5:16] if len(at) >= 16 else at
    mode = n.get("alarm_mode") or "once"
    if mode == "repeat":
        rule = {
            "daily": "每天",
            "weekly": "每周",
            "weekdays": "工作日",
            "monthly": "每月",
        }.get(n.get("repeat") or "", "重复")
        return f" · 闹钟 {rule} {short}"
    return f" · 闹钟 {short}"


def format_brief_list(items: list[dict[str, Any]] | None = None) -> str:
    items = items if items is not None else list_notes()
    if not items:
        return "（暂无记事）"
    lines = []
    for i, n in enumerate(items, 1):
        active = n.get("kind") == "alarm" and n.get("alarm_enabled")
        tag = "闹钟" if active else "记事"
        lines.append(
            f"{i}. [{n['id']}] ({tag}) {n.get('summary', '')}{_alarm_label(n)}"
        )
    return "\n".join(lines)



def format_note_detail(note: dict[str, Any]) -> str:
    kind = "闹钟" if note.get("kind") == "alarm" and note.get("alarm_enabled") else "记事"
    lines = [
        f"类型：{kind}",
        f"摘要：{note.get('summary', '')}",
        f"创建：{note.get('created', '')}",
    ]
    if note.get("kind") == "alarm" or note.get("remind_at") or note.get("reminded"):
        mode = note.get("alarm_mode") or "none"
        if mode == "repeat":
            lines.append(
                f"闹钟：重复 {note.get('repeat')}，下次 {note.get('remind_at')}，"
                f"{'开启' if note.get('alarm_enabled') else '已关'}"
            )
        elif note.get("remind_at") and note.get("alarm_enabled"):
            lines.append(f"闹钟：一次性 {note.get('remind_at')}")
        elif note.get("reminded"):
            lines.append(f"闹钟：已响过（{note.get('reminded_at', '')}）")
        else:
            lines.append("闹钟：无")
    else:
        lines.append("闹钟：无（纯记事）")
    lines.append("---")
    lines.append(note.get("content") or "")
    return "\n".join(lines)
