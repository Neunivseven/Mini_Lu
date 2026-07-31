"""Python 语义分析：jedi 封装（精确引用查找 / 跨文件重命名）。

与 TSA 的分工：TSA 管结构大纲与近似调用图（全语言）；
本模块只针对 Python，走类型推断，引用/改名是精确结果。
jedi 未安装时优雅降级（is_available() 为 False）。
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_REFS = 60
_available: bool | None = None


def is_available() -> bool:
    global _available
    if _available is None:
        try:
            import jedi  # noqa: F401

            _available = True
        except Exception:
            _available = False
    return bool(_available)


def locate_name(text: str, name: str, prefer_line: int = 0) -> tuple[int, int] | None:
    """定位符号名出现位置，返回 (1-based 行, 0-based 列)。"""
    rx = re.compile(rf"\b{re.escape(name)}\b")
    lines = text.splitlines()
    if prefer_line and 1 <= prefer_line <= len(lines):
        m = rx.search(lines[prefer_line - 1])
        if m:
            return prefer_line, m.start()
    for i, ln in enumerate(lines, 1):
        m = rx.search(ln)
        if m:
            return i, m.start()
    return None


def _script(path: Path, text: str, root: Path):
    import jedi

    project = jedi.Project(path=str(root))
    return jedi.Script(code=text, path=str(path), project=project)


def find_references(
    path: Path,
    text: str,
    root: Path,
    name: str,
    line: int = 0,
) -> list[dict[str, Any]] | str:
    """返回引用列表（仅项目内），失败返回错误字符串。"""
    if not is_available():
        return "jedi 未安装（pip install jedi），请改用 find_callers / grep_files。"
    pos = locate_name(text, name, prefer_line=line)
    if pos is None:
        return f"文件中未出现 {name!r}，请核对符号名或指定 line。"
    row, col = pos
    try:
        refs = _script(path, text, root).get_references(row, col, include_builtins=False)
    except Exception as e:
        logger.debug("jedi get_references 失败: %s", e)
        return f"jedi 解析失败: {e}"

    root_res = root.resolve()
    out: list[dict[str, Any]] = []
    for r in refs:
        mp = getattr(r, "module_path", None)
        if mp is None:
            continue
        try:
            rel = Path(mp).resolve().relative_to(root_res)
        except Exception:
            continue  # 项目外（stdlib / site-packages）不列出
        out.append(
            {
                "file": str(rel),
                "line": int(r.line or 0),
                "column": int(r.column or 0),
                "is_definition": bool(r.is_definition()),
                "code": (r.get_line_code() or "").strip()[:160],
            }
        )
        if len(out) >= _MAX_REFS:
            break
    return out


def rename(
    path: Path,
    text: str,
    root: Path,
    name: str,
    new_name: str,
    line: int = 0,
) -> dict[str, Any] | str:
    """计算重命名结果。返回 {"files": {绝对路径: 新全文}}，失败返回错误字符串。"""
    if not is_available():
        return "jedi 未安装（pip install jedi），请改用 grep_files + edit_file 手工替换。"
    if not new_name.isidentifier():
        return f"新名字不合法: {new_name!r}"
    pos = locate_name(text, name, prefer_line=line)
    if pos is None:
        return f"文件中未出现 {name!r}，请核对符号名或指定 line。"
    row, col = pos
    try:
        refactoring = _script(path, text, root).rename(row, col, new_name=new_name)
        renames = list(refactoring.get_renames() or [])
        if renames:
            return (
                "该重命名涉及文件/模块改名，暂不支持自动执行；"
                "请手工移动文件后再改引用。"
            )
        changed = refactoring.get_changed_files() or {}
    except Exception as e:
        logger.debug("jedi rename 失败: %s", e)
        return f"jedi 重命名失败: {e}"

    root_res = root.resolve()
    files: dict[Path, str] = {}
    for fp, cf in changed.items():
        ap = Path(fp).resolve()
        try:
            ap.relative_to(root_res)
        except Exception:
            return f"重命名会改动项目外文件（{ap}），已中止。"
        try:
            files[ap] = cf.get_new_code()
        except Exception as e:
            return f"生成新代码失败（{ap.name}）: {e}"
    if not files:
        return "没有需要改动的文件（符号可能未被解析到）。"
    return {"files": files}
