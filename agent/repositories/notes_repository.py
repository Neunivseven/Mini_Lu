"""记事 / 闹钟持久化端口；实现委托 notes_store。"""
from __future__ import annotations

from datetime import datetime
from typing import Any


class NotesRepository:
    """隔离 notes.json 读写细节。"""

    def add_note(self, **kwargs: Any) -> dict[str, Any]:
        from agent import notes_store

        return notes_store.add_note(**kwargs)

    def list_notes(
        self, limit: int = 50, *, kind: str | None = None
    ) -> list[dict[str, Any]]:
        from agent import notes_store

        return notes_store.list_notes(limit, kind=kind)

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        from agent import notes_store

        return notes_store.get_note(note_id)

    def delete_note(self, note_id: str) -> bool:
        from agent import notes_store

        return notes_store.delete_note(note_id)

    def clear_reminder(self, note_id: str) -> str:
        from agent import notes_store

        return notes_store.clear_reminder(note_id)

    def pop_due(self, now: datetime | None = None) -> list[dict[str, Any]]:
        from agent import notes_store

        return notes_store.pop_due_notes(now)

    def next_due_at(self, now: datetime | None = None) -> datetime | None:
        from agent import notes_store

        return notes_store.next_due_alarm_at(now)

    def format_brief(self, items: list[dict[str, Any]] | None = None) -> str:
        from agent import notes_store

        return notes_store.format_brief_list(items)

    def format_detail(self, note: dict[str, Any]) -> str:
        from agent import notes_store

        return notes_store.format_note_detail(note)


_REPO: NotesRepository | None = None


def get_notes_repository() -> NotesRepository:
    global _REPO
    if _REPO is None:
        _REPO = NotesRepository()
    return _REPO
