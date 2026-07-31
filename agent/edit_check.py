"""编辑后即时校验：写入/暂存后立刻检查语法，把问题回传给 Agent 自纠错。

层级（按文件类型选用，全部失败静默跳过，不阻塞写入）：
- Python: ast.parse（报错行列最准）+ 可选 ruff（装了才跑）
- 其它代码: tree-sitter 重解析，报 ERROR / missing 节点
- json / yaml: 标准库解析
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MAX_ISSUES = 5
_RUFF_TIMEOUT = 8
_RUFF_MAX_LINES = 8

# 扩展名 → tree-sitter 语言名（TSA loader 可加载的）
_TS_LANG_BY_EXT = {
    ".c": "c",
    ".h": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
}

_ruff_path: str | None | bool = False  # False=未探测; None=不可用


def _find_ruff() -> str | None:
    global _ruff_path
    if _ruff_path is not False:
        return _ruff_path  # type: ignore[return-value]
    import shutil
    import sys

    cand = shutil.which("ruff")
    if not cand:
        # 同环境 bin 目录（conda/venv 下 which 可能因 PATH 缺失）
        exe_dir = Path(sys.executable).parent
        p = exe_dir / "ruff"
        cand = str(p) if p.is_file() else None
    _ruff_path = cand
    return cand


def _check_python_ast(text: str) -> list[str]:
    import ast

    try:
        ast.parse(text)
        return []
    except SyntaxError as e:
        loc = f"L{e.lineno}" + (f":{e.offset}" if e.offset else "")
        return [f"{loc} SyntaxError: {e.msg}"]
    except Exception as e:
        return [f"解析失败: {e}"]


def _check_python_ruff(path: Path, text: str) -> list[str]:
    ruff = _find_ruff()
    if not ruff:
        return []
    import os
    import subprocess

    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            [
                ruff,
                "check",
                "--isolated",  # 不读环境里的 ruff 配置，避免风格类噪音
                "--select=E9,F63,F7,F82",  # 只查语法错误 / 未定义名等真错误
                "--no-cache",
                "--quiet",
                "--output-format=concise",
                "--stdin-filename",
                str(path),
                "-",
            ],
            input=text,
            capture_output=True,
            text=True,
            timeout=_RUFF_TIMEOUT,
            env=env,
        )
    except Exception as e:
        logger.debug("ruff 执行失败: %s", e)
        return []
    if proc.returncode == 0:
        return []
    import re

    plain = re.sub(r"\x1b\[[0-9;]*m", "", proc.stdout or "")
    lines = [ln.strip() for ln in plain.splitlines() if ln.strip()]
    out: list[str] = []
    for ln in lines[:_RUFF_MAX_LINES]:
        # concise 格式: path:line:col: CODE message → 去掉路径前缀省 token
        cut = ln.replace(str(path), "").lstrip(":")
        out.append(f"L{cut}" if cut and cut[0].isdigit() else (cut or ln))
    if len(lines) > _RUFF_MAX_LINES:
        out.append(f"…共 {len(lines)} 条（ruff）")
    return out


def _check_tree_sitter(lang_name: str, text: str) -> list[str]:
    try:
        from tree_sitter_analyzer.language_loader import loader

        lang = loader.load_language(lang_name)
        if lang is None:
            return []
        import tree_sitter as ts

        tree = ts.Parser(lang).parse(text.encode("utf-8", errors="replace"))
    except Exception as e:
        logger.debug("tree-sitter 校验跳过（%s）: %s", lang_name, e)
        return []
    root = tree.root_node
    if not root.has_error:
        return []

    issues: list[str] = []

    def walk(node) -> None:
        if len(issues) >= _MAX_ISSUES:
            return
        if node.type == "ERROR":
            row, col = node.start_point
            issues.append(f"L{row + 1}:{col + 1} 解析错误（ERROR 节点）")
            return  # 不深入 ERROR 内部
        if node.is_missing:
            row, col = node.start_point
            issues.append(f"L{row + 1}:{col + 1} 缺少 {node.type!r}")
            return
        if not node.has_error:
            return
        for child in node.children:
            walk(child)

    walk(root)
    if not issues:
        issues.append("存在解析错误（位置未定位）")
    return issues


def _check_json(text: str) -> list[str]:
    import json

    try:
        json.loads(text)
        return []
    except Exception as e:
        return [f"JSON 无效: {e}"]


def _check_yaml(text: str) -> list[str]:
    try:
        import yaml

        yaml.safe_load(text)
        return []
    except Exception as e:
        msg = str(e).splitlines()[0] if str(e) else "解析失败"
        return [f"YAML 无效: {msg}"]


def run_edit_checks(path: Path, text: str) -> str:
    """对写入后的完整内容做语法检查。

    返回附加到工具结果末尾的短报告；无检查器命中时返回空串。
    """
    try:
        ext = path.suffix.lower()
        checker = ""
        issues: list[str] = []
        if ext in (".py", ".pyw"):
            checker = "python"
            issues = _check_python_ast(text)
            if not issues:
                ruff_issues = _check_python_ruff(path, text)
                if ruff_issues:
                    checker = "python+ruff"
                    issues = ruff_issues
        elif ext in _TS_LANG_BY_EXT:
            checker = _TS_LANG_BY_EXT[ext]
            issues = _check_tree_sitter(checker, text)
        elif ext == ".json":
            checker = "json"
            issues = _check_json(text)
        elif ext in (".yaml", ".yml"):
            checker = "yaml"
            issues = _check_yaml(text)
        else:
            return ""

        if not issues:
            return f"\n语法检查通过（{checker}）"
        body = "\n".join(f"  {i}" for i in issues[: _MAX_ISSUES + _RUFF_MAX_LINES])
        return (
            f"\n⚠️ 语法检查（{checker}）发现问题，请立即修复后再继续：\n{body}"
        )
    except Exception as e:
        logger.debug("edit_check 异常: %s", e)
        return ""
