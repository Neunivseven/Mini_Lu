"""
代码改动暂存：审核开启时立即写入新内容，旧内容缓存到目标文件同目录；
按「连续改动段」(hunk) 分别保留/放弃；全部段处理完后清除 cache。

线程安全：工具线程只写队列；UI 通过 UiBridge 刷新。
"""
from __future__ import annotations

import difflib
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

_lock = threading.RLock()
_pending: dict[str, dict[str, Any]] = {}
_review_enabled = True

_CACHE_SUFFIX = ".pet.before"


def set_review_enabled(on: bool) -> None:
    global _review_enabled
    _review_enabled = bool(on)


def is_review_enabled() -> bool:
    return _review_enabled


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _notify() -> None:
    try:
        from agent.ui_bridge import get_bridge

        bridge = get_bridge()
        if bridge is not None and hasattr(bridge, "edits_changed"):
            bridge.edits_changed.emit()
        if bridge is not None and hasattr(bridge, "open_agent_studio"):
            bridge.open_agent_studio.emit()
    except Exception:
        pass


def cache_path_for(target: str | Path) -> Path:
    p = Path(target)
    return p.parent / f"{p.name}{_CACHE_SUFFIX}"


def _write_side_cache(target: Path, before: str) -> Path:
    cp = cache_path_for(target)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(before if before is not None else "", encoding="utf-8", newline="\n")
    return cp


def _read_side_cache(cp: Path | None, fallback: str = "") -> str:
    if cp is None:
        return fallback
    try:
        if cp.is_file():
            return cp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return fallback


def _remove_side_cache(cp: Path | None) -> None:
    if cp is None:
        return
    try:
        Path(cp).unlink(missing_ok=True)
    except Exception:
        pass


def _lines(text: str) -> list[str]:
    if not text:
        return []
    return text.splitlines(keepends=True)


def compute_hunks(before: str, after: str) -> list[dict[str, Any]]:
    """按连续非 equal 段切分 hunk（一行或多行连片算一段）。"""
    a, b = _lines(before), _lines(after)
    hunks: list[dict[str, Any]] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        hunks.append(
            {
                "id": len(hunks),
                "tag": tag,
                "i1": i1,
                "i2": i2,
                "j1": j1,
                "j2": j2,
                "status": "pending",  # pending | kept | discarded
            }
        )
    return hunks


def rebuild_from_hunks(before: str, after: str, hunks: list[dict[str, Any]]) -> str:
    """
    按各段决定重建文件：
    - discarded → 用修改前行
    - pending / kept → 用修改后行（待确认时磁盘已是新内容）
    """
    a, b = _lines(before), _lines(after)
    by_span = {(h["i1"], h["i2"], h["j1"], h["j2"]): h for h in hunks}
    out: list[str] = []
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out.extend(a[i1:i2])
            continue
        h = by_span.get((i1, i2, j1, j2))
        status = (h or {}).get("status", "pending")
        if status == "discarded":
            out.extend(a[i1:i2])
        else:
            out.extend(b[j1:j2])
    return "".join(out)


def unified_diff(before: str, after: str, path: str = "file") -> str:
    a = _lines(before)
    b = _lines(after)
    diff = difflib.unified_diff(
        a,
        b,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        n=3,
    )
    text = "".join(diff)
    return text or "（无差异）"


def _pending_hunks(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for h in (item.get("hunks") or []) if h.get("status") == "pending"]


def _write_item_content(item: dict[str, Any], content: str) -> None:
    p = Path(item["path"])
    was_new = bool(item.get("was_new", False))
    hunks = item.get("hunks") or []
    all_discarded = bool(hunks) and all(h.get("status") == "discarded" for h in hunks)
    if was_new and all_discarded:
        if p.exists():
            p.unlink()
        return
    if was_new and content == "" and all_discarded:
        if p.exists():
            p.unlink()
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8", newline="\n")


def _close_edit(item: dict[str, Any]) -> None:
    """全部段已决定：清 cache、标记完成。"""
    p = Path(item["path"])
    cache_p = Path(item["cache_path"]) if item.get("cache_path") else cache_path_for(p)
    hunks = item.get("hunks") or []
    all_discarded = bool(hunks) and all(h.get("status") == "discarded" for h in hunks)
    content = rebuild_from_hunks(
        item.get("before") or "",
        item.get("after") or "",
        hunks,
    )
    try:
        _write_item_content(item, content)
    except Exception:
        pass
    _remove_side_cache(cache_p)
    item["status"] = "rejected" if all_discarded else "applied"
    item["closed_at"] = _now()
    try:
        from agent.file_workspace import mark_read, save_backup

        before = item.get("before") or ""
        if before and not all_discarded:
            save_backup(p, before)
        if p.exists():
            mark_read(p, content)
    except Exception:
        pass


def stage_edit(
    path: str | Path,
    before: str,
    after: str,
    *,
    summary: str = "",
) -> str:
    """
    审核开启：缓存旧内容 → 立即写入新内容 → 按 hunk 入队等待确认。
    审核关闭：立即写盘。
    """
    p = Path(str(path))
    before = before if before is not None else ""
    after = after if after is not None else ""
    if before == after:
        return "内容无变化，未暂存"

    if not is_review_enabled():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(after, encoding="utf-8", newline="\n")
        try:
            from agent.file_workspace import mark_read

            mark_read(p, after)
        except Exception:
            pass
        return f"已直接写入 {p}"

    with _lock:
        existing_id = None
        was_new = not p.exists() and before == ""
        cache_p: Path | None = None
        for eid, item in _pending.items():
            if item.get("status") == "pending" and Path(item["path"]).resolve() == p.resolve():
                existing_id = eid
                before = item.get("before") if item.get("before") is not None else before
                was_new = bool(item.get("was_new", False))
                raw_cp = item.get("cache_path")
                cache_p = Path(raw_cp) if raw_cp else cache_path_for(p)
                break

        if existing_id is None:
            try:
                cache_p = _write_side_cache(p, before)
            except Exception as e:
                return f"写入旁路缓存失败: {e}"
        elif cache_p is not None and not cache_p.exists():
            try:
                cache_p = _write_side_cache(p, before)
            except Exception:
                pass

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(after, encoding="utf-8", newline="\n")
        except Exception as e:
            return f"写入失败: {e}"

        try:
            from agent.file_workspace import mark_read

            mark_read(p, after)
        except Exception:
            pass

        hunks = compute_hunks(before, after)
        eid = existing_id or uuid.uuid4().hex[:10]
        _pending[eid] = {
            "id": eid,
            "path": str(p.resolve()),
            "before": before,
            "after": after,
            "summary": (summary or "代码修改").strip(),
            "status": "pending",
            "ts": _now(),
            "diff": unified_diff(before, after, p.name),
            "cache_path": str(cache_p) if cache_p else str(cache_path_for(p)),
            "was_new": was_new,
            "hunks": hunks,
        }
    n = len(hunks)
    _notify()
    return (
        f"已写入并待确认 [{eid}] → {p.name}（{n} 段连续改动，逐段保留/放弃）。"
        f"摘要: {summary or 'edit'}"
    )


def list_pending() -> list[dict[str, Any]]:
    with _lock:
        items = [dict(v) for v in _pending.values() if v.get("status") == "pending"]
        # 深拷贝 hunks，避免 UI 误改
        for it in items:
            it["hunks"] = [dict(h) for h in (it.get("hunks") or [])]
    items.sort(key=lambda x: x.get("ts") or "")
    return items


def get_edit(edit_id: str) -> dict[str, Any] | None:
    with _lock:
        item = _pending.get(edit_id)
        if not item:
            return None
        out = dict(item)
        out["hunks"] = [dict(h) for h in (item.get("hunks") or [])]
        return out


def decide_hunk(edit_id: str, hunk_id: int, *, keep: bool) -> str:
    """对单个连续改动段：保留或放弃；写回磁盘；若无剩余段则结束整文件审核。"""
    with _lock:
        item = _pending.get(edit_id)
        if not item or item.get("status") != "pending":
            return f"未找到待确认改动: {edit_id}"
        hunks = item.get("hunks") or []
        target = None
        for h in hunks:
            if int(h.get("id", -1)) == int(hunk_id):
                target = h
                break
        if target is None:
            return f"未找到改动段: {hunk_id}"
        if target.get("status") != "pending":
            return f"该段已处理（{target.get('status')}）"

        target["status"] = "kept" if keep else "discarded"
        content = rebuild_from_hunks(
            item.get("before") or "",
            item.get("after") or "",
            hunks,
        )
        try:
            _write_item_content(item, content)
        except Exception as e:
            target["status"] = "pending"
            return f"写回失败: {e}"

        left = _pending_hunks(item)
        path = item.get("path")
        if not left:
            _close_edit(item)
            _notify()
            action = "保留" if keep else "放弃"
            return f"已{action}最后一段，文件确认完成 → {path}"

        try:
            from agent.file_workspace import mark_read

            p = Path(path)
            if p.exists():
                mark_read(p, content)
        except Exception:
            pass
        _notify()
        action = "保留" if keep else "放弃"
        return f"已{action}第 {int(hunk_id) + 1} 段，剩余 {len(left)} 段 → {Path(path).name}"


def first_pending_hunk_id(edit_id: str) -> int | None:
    with _lock:
        item = _pending.get(edit_id)
        if not item:
            return None
        for h in item.get("hunks") or []:
            if h.get("status") == "pending":
                return int(h["id"])
    return None


def apply_edit(edit_id: str) -> str:
    """将剩余未决段全部保留并结束。"""
    with _lock:
        item = _pending.get(edit_id)
        if not item or item.get("status") != "pending":
            return f"未找到待确认改动: {edit_id}"
        for h in item.get("hunks") or []:
            if h.get("status") == "pending":
                h["status"] = "kept"
        _close_edit(item)
        path = item.get("path")
    _notify()
    return f"已全部保留 → {path}"


def reject_edit(edit_id: str) -> str:
    """将剩余未决段全部放弃并结束（已保留的段不动）。"""
    with _lock:
        item = _pending.get(edit_id)
        if not item or item.get("status") != "pending":
            return f"未找到待确认改动: {edit_id}"
        for h in item.get("hunks") or []:
            if h.get("status") == "pending":
                h["status"] = "discarded"
        _close_edit(item)
        path = item.get("path")
    _notify()
    return f"已放弃剩余改动 → {path}"


def apply_all() -> str:
    ids = [e["id"] for e in list_pending()]
    if not ids:
        return "没有待确认的改动"
    return "\n".join(apply_edit(i) for i in ids)


def reject_all() -> str:
    ids = [e["id"] for e in list_pending()]
    if not ids:
        return "没有待确认的改动"
    return "\n".join(reject_edit(i) for i in ids)


def pending_count() -> int:
    return len(list_pending())
