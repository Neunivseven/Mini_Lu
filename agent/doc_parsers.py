"""
文档解析：用 PyMuPDF（fitz）提取 PDF 文本/简单 Markdown。
可随 exe 打包，无需 Marker / MinerU / 独立 conda 环境。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import config_read_path, user_dir

SUPPORTED_PARSE = {
    ".pdf",
    ".xps",
    ".epub",
    ".mobi",
    ".fb2",
    ".cbz",
    ".svg",
    ".txt",
}


def _cfg_path() -> Path:
    return config_read_path("doc_parsers.yaml")


def load_parser_config() -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "default_engine": "pymupdf",
        "output_dir": "data/doc_parse",
        "max_chars": 20000,
        "max_pages": 100,
        # text | markdown | blocks（blocks 更保版面顺序）
        "text_mode": "text",
        "page_header": True,
    }
    path = _cfg_path()
    if not path.exists():
        return defaults
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return defaults
    if not isinstance(raw, dict):
        return defaults
    defaults.update({k: v for k, v in raw.items() if v is not None})
    return defaults


def output_root() -> Path:
    cfg = load_parser_config()
    p = Path(str(cfg.get("output_dir") or "data/doc_parse"))
    if not p.is_absolute():
        p = user_dir() / p  # 输出属于用户数据，放用户目录
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pymupdf_ok() -> tuple[bool, str]:
    try:
        import fitz  # noqa: F401

        return True, "pymupdf"
    except ImportError:
        return False, "未安装 pymupdf（pip install pymupdf）"


def detect_engines() -> dict[str, Any]:
    ok, note = _pymupdf_ok()
    cfg = load_parser_config()
    return {
        "pymupdf": ok,
        "pymupdf_note": note,
        "default_engine": cfg.get("default_engine") or "pymupdf",
        "builtin_fallback": True,
    }


def _clip(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    max_chars = max(2000, min(int(max_chars), 100000))
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + f"\n\n…(已截断，原文约 {len(text)} 字)"


def _parse_page_range(pages: str, page_count: int) -> tuple[int, int] | None:
    """
    解析页码范围（1-based）。例: "1-5" / "3" / "10-"。
    返回 0-based inclusive (start, end)。
    """
    pages = (pages or "").strip()
    if not pages:
        return None
    if pages.endswith("-") and pages[:-1].isdigit():
        first = int(pages[:-1])
        if first < 1:
            return None
        return first - 1, page_count - 1
    if "-" in pages:
        a, b = pages.split("-", 1)
        if not a.isdigit() or not b.isdigit():
            return None
        first, last = int(a), int(b)
        if first < 1 or last < first:
            return None
        return first - 1, min(last, page_count) - 1
    if pages.isdigit():
        p = int(pages)
        if p < 1 or p > page_count:
            return None
        return p - 1, p - 1
    return None


def _page_text(page: Any, mode: str) -> str:
    mode = (mode or "text").lower()
    if mode == "markdown":
        try:
            return page.get_text("markdown") or ""
        except Exception:
            pass
    if mode == "blocks":
        try:
            blocks = page.get_text("blocks") or []
            lines = []
            for b in blocks:
                if len(b) >= 5 and isinstance(b[4], str) and b[4].strip():
                    lines.append(b[4].strip())
            return "\n".join(lines)
        except Exception:
            pass
    return page.get_text("text") or ""


def parse_with_pymupdf(
    path: str | Path,
    *,
    pages: str = "",
    max_chars: int | None = None,
) -> dict[str, Any]:
    ok, note = _pymupdf_ok()
    if not ok:
        raise RuntimeError(note)

    import fitz

    cfg = load_parser_config()
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在: {src}")

    mode = str(cfg.get("text_mode") or "text")
    use_header = bool(cfg.get("page_header", True))
    limit_pages = max(1, int(cfg.get("max_pages") or 100))
    max_chars = int(max_chars if max_chars is not None else cfg.get("max_chars") or 20000)

    doc = fitz.open(str(src))
    try:
        page_count = doc.page_count
        if page_count <= 0:
            raise ValueError(f"PDF 无有效页面: {src.name}")

        rng = _parse_page_range(pages, page_count)
        if rng is None:
            start, end = 0, min(page_count, limit_pages) - 1
            if page_count > limit_pages:
                truncated_note = f"仅解析前 {limit_pages} 页（共 {page_count} 页）；可用 pages= 指定范围"
            else:
                truncated_note = ""
        else:
            start, end = rng
            truncated_note = ""

        chunks: list[str] = []
        for i in range(start, end + 1):
            page = doc.load_page(i)
            t = _page_text(page, mode).strip()
            if not t:
                continue
            if use_header:
                chunks.append(f"[第{i + 1}页]\n{t}")
            else:
                chunks.append(t)

        text = "\n\n".join(chunks)
        if not text.strip():
            raise ValueError(
                f"未能从文件提取到文字: {src.name}（可能是扫描件；可改用图像识别 describe_image）"
            )

        md_path = ""
        try:
            out = output_root() / f"{src.stem}.md"
            out.write_text(_clip(text, max_chars * 2), encoding="utf-8")
            md_path = str(out)
        except Exception:
            pass

        log = f"PyMuPDF {note}；页 {start + 1}-{end + 1}/{page_count}；mode={mode}"
        if truncated_note:
            log += f"；{truncated_note}"

        return {
            "engine": "pymupdf",
            "ok": True,
            "path": str(src),
            "output_dir": str(output_root()),
            "markdown_path": md_path,
            "page_count": page_count,
            "pages_read": f"{start + 1}-{end + 1}",
            "text": _clip(text, max_chars),
            "log": log,
        }
    finally:
        doc.close()


def parse_with_builtin(path: str | Path) -> dict[str, Any]:
    """回退：pypdf / docx 等（file_extract）。"""
    from agent.file_extract import extract_text

    cfg = load_parser_config()
    src = Path(path).expanduser().resolve()
    text = extract_text(src, max_chars=int(cfg.get("max_chars") or 20000))
    return {
        "engine": "builtin",
        "ok": True,
        "path": str(src),
        "output_dir": "",
        "markdown_path": "",
        "text": text,
        "log": "使用内置轻量提取（非 PyMuPDF）",
    }


def parse_document(
    path: str | Path,
    *,
    engine: str = "auto",
    pages: str = "",
    extra_args: str = "",
) -> dict[str, Any]:
    """
    engine: auto | pymupdf | builtin
    pages: 可选页码范围，如 "1-5"（传给 PyMuPDF）
    extra_args: 兼容旧参数；若形如 pages=1-5 也会解析
    """
    src = Path(path).expanduser().resolve()
    if not src.is_file():
        raise FileNotFoundError(f"文件不存在: {src}")

    cfg = load_parser_config()
    eng = (engine or cfg.get("default_engine") or "auto").strip().lower()
    if eng in {"marker", "mineru"}:
        eng = "pymupdf"  # 旧配置兼容

    # 纯文本直接 builtin
    if src.suffix.lower() in {".txt", ".md", ".markdown", ".csv"}:
        return parse_with_builtin(src)

    # extra_args 兼容: pages=1-5
    page_spec = (pages or "").strip()
    if not page_spec and extra_args:
        ea = extra_args.strip()
        if ea.lower().startswith("pages="):
            page_spec = ea.split("=", 1)[1].strip().strip('"').strip("'")
        elif ea and all(c.isdigit() or c == "-" for c in ea):
            page_spec = ea

    errors: list[str] = []

    def _try_pymupdf():
        return parse_with_pymupdf(src, pages=page_spec)

    if eng == "pymupdf":
        return _try_pymupdf()
    if eng == "builtin":
        return parse_with_builtin(src)

    # auto
    ok, _ = _pymupdf_ok()
    if ok and src.suffix.lower() in {".pdf", ".xps", ".epub", ".mobi", ".fb2", ".cbz", ".svg"}:
        try:
            return _try_pymupdf()
        except Exception as e:
            errors.append(f"pymupdf: {e}")
    try:
        result = parse_with_builtin(src)
        if errors:
            result["log"] = "PyMuPDF 失败，已回退 builtin。\n" + "\n".join(errors)
        return result
    except Exception as e:
        raise RuntimeError(
            "文档解析失败。\n" + "\n".join(errors + [f"builtin: {e}"])
        ) from e


def format_parse_report(result: dict[str, Any]) -> str:
    lines = [
        f"引擎: {result.get('engine')}",
        f"源文件: {result.get('path')}",
    ]
    if result.get("page_count") is not None:
        lines.append(f"总页数: {result['page_count']}；本次: {result.get('pages_read')}")
    if result.get("markdown_path"):
        lines.append(f"导出: {result['markdown_path']}")
    lines.append("--- 解析正文 ---")
    lines.append(result.get("text") or "（空）")
    if result.get("log"):
        lines.append("--- 日志 ---")
        lines.append(str(result["log"])[:500])
    return "\n".join(lines)


def engines_status_text() -> str:
    st = detect_engines()
    lines = [
        f"默认引擎: {st.get('default_engine')}",
        f"PyMuPDF: {'可用' if st.get('pymupdf') else '未安装 — pip install pymupdf'}",
        "内置 builtin: 始终可用（pypdf/docx 等回退）",
        "说明: 已移除 Marker/MinerU；PDF 解析随桌宠一并打包。",
        f"配置: {_cfg_path()}",
    ]
    return "\n".join(lines)
