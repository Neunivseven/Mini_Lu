"""Markdown → HTML（聊天卡片）。

流程：
1. ``normalize_markdown``：修正模型脏输出（挤行表格/标题/目录树）
2. ``markdown_to_html``：官方 ``markdown`` 库转 HTML
3. 目录树（├──、|—、|-- 等）收成等宽代码块；UI 侧禁止软换行

若未安装 markdown，则回退为转义纯文本。
"""
from __future__ import annotations

import html
import re

_CODE_FENCE = re.compile(r"```([^\n`]*)\n([\s\S]*?)```", re.MULTILINE)
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

# - – — ─ （ASCII / en / em / box）
_DASH = r"[\-\u2013\u2014\u2500]"
# 表分隔/HR 残留，禁止当目录树：|---、|---|、|---##
_TABLE_OR_HR_PIPE = re.compile(
    r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$"
    r"|^\|\s*-{2,}\s*(?:#|$)"
    r"|^\|\s*-{2,}\s*\|"
)
# ├── └── 以及中文模型常用 |—name / ||—name（破折号后须像文件名，不能是 # 或再跟 |）
_TREE_BRANCH = re.compile(
    rf"^[\s]*(?:"
    rf"(?:[│|][\s]*)*(?:├──|└──|├─|└─)|"
    rf"(?:\|[\s]*)+{_DASH}+(?![|\-:#])\s*[^\s|:\-#]|"
    rf"(?:`[\s]*)+{_DASH}+(?![|\-:#])\s*[^\s|:\-#]"
    rf")"
)
_TREE_GUTTER = re.compile(r"^[\s]*(?:[│|][\s]*)+$")
# 行内树尖：排除 |---| 表分隔
_TREE_TIP_INLINE = re.compile(
    rf"(?:\|[\s]*)+{_DASH}+(?![|\-:#])[^\s|:\-#]|[├└]──"
)

_MD_EXT = [
    "markdown.extensions.tables",
    "markdown.extensions.fenced_code",
    "markdown.extensions.nl2br",
    "markdown.extensions.sane_lists",
]

_WRAP_STYLE = (
    "font-family:'Microsoft YaHei UI','Noto Sans CJK SC',sans-serif;"
    "color:#2C2420;font-size:11pt;line-height:1.55;"
)

_DOC_STYLE = """
h1,h2,h3,h4 {
  color:#2C2420; font-weight:700;
  margin:12px 0 6px 0;
  line-height:1.35;
}
h1 { font-size:14pt; border-bottom:1px solid #E6DDD0; padding-bottom:3px; }
h2 { font-size:13pt; border-bottom:1px solid #EFE6D8; padding-bottom:2px; }
h3 { font-size:12pt; }
h4 { font-size:11.5pt; }
p { margin:4px 0; font-size:11pt; line-height:1.55; color:#2C2420; }
ul,ol { margin:4px 0 4px 18px; padding:0; font-size:11pt; }
li { margin:2px 0; }
hr { border:none; border-top:1px solid #E0D8CC; margin:10px 0; }
code {
  background:#F0EBE3; padding:1px 4px; border-radius:3px;
  font-family:Consolas,'Cascadia Code',monospace; font-size:10.5pt;
}
pre {
  font-family:Consolas,'Cascadia Code',monospace; font-size:10pt;
  line-height:1.4; white-space:pre; margin:6px 0; padding:8px 10px;
  background:#F3F0EA; border:1px solid #E0D8CC; border-radius:6px;
  color:#2C2420;
}
pre code { background:transparent; padding:0; }
table {
  border-collapse:collapse; width:100%; margin:6px 0;
  font-size:10.5pt; line-height:1.45;
}
th {
  border:1px solid #D0C4B0; padding:5px 8px; background:#F3F0EA;
  text-align:left; font-weight:600;
}
td { border:1px solid #E5DDD0; padding:5px 8px; }
blockquote {
  margin:6px 0; padding:4px 10px; border-left:3px solid #D0C4B0;
  color:#5A524C;
}
a { color:#3D7EA6; }
strong,b { font-weight:700; }
"""


def strip_md(text: str) -> str:
    """折叠预览用：去掉常见标记。"""
    t = text or ""
    t = _CODE_FENCE.sub("[代码]", t)
    t = _BOLD.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = re.sub(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)", r"\1", t)
    t = re.sub(r"`([^`\n]+)`", r"\1", t)
    t = _LINK.sub(r"\1", t)
    t = re.sub(r"^#+\s*", "", t, flags=re.M)
    t = re.sub(r"\|", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _looks_like_table_line(line: str) -> bool:
    """GFM 表行（不含目录树）。"""
    s = line.strip()
    if s.startswith("```"):
        return False
    if any(x in s for x in ("├──", "└──", "├─", "└─")):
        return False
    # 表头分隔 |---|---|
    if re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", s):
        return True
    # |—name / ||—name 是树不是表（破折号后直接跟标识符，且非 #）
    if re.match(rf"^\|{{1,3}}\s*{_DASH}+(?![|\-:#])\s*[^\s|:\-#]", s):
        return False
    if s.count("|") >= 2:
        inner = s.strip().strip("|")
        if "|" in inner:
            return True
        # 单格表行 |描述|（模型常把「影响」拆成这种续行）
        if inner and re.fullmatch(r"\|[^|]+\|", s):
            return True
    return False


def _is_tree_line(line: str) -> bool:
    s = line.rstrip("\n")
    raw = s.strip()
    if not raw:
        return False
    # 孤立 | / │、表分隔、|---## 都不是树
    if raw in ("|", "│", "||"):
        return False
    if _TABLE_OR_HR_PIPE.match(raw):
        return False
    if _looks_like_table_line(s):
        return False
    if re.match(r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", raw):
        return False
    if _TREE_BRANCH.match(s):
        if re.match(rf"^[\s]*\|{_DASH}+\|\s*$", raw):
            return False
        return True
    if _TREE_GUTTER.match(raw) and any(c in s for c in "│|") and raw not in ("|", "│"):
        return True
    if re.match(r"^[\s]*[│|][ \t]+\S", s) and s.count("|") <= 2:
        return True
    return False


def _is_tree_root_line(line: str) -> bool:
    """如 `my_item/` 或带注释的根目录名。"""
    s = line.strip()
    if not s or _is_tree_line(line) or _looks_like_table_line(s):
        return False
    if any(c in s for c in "├└"):
        return False
    if s.startswith("```"):
        return False
    return bool(re.match(r"^(?:📂|📁)?[^\s].*/\s*(?:#.*)?$", s))


def _has_tree_markers(t: str) -> bool:
    if any(c in t for c in "├└"):
        return True
    # |—name 而非 |---| / |---##
    return bool(re.search(rf"\|[\s]*{_DASH}+(?![|\-:#])[^\s|:\-#]", t))


def _canonicalize_tree_line(line: str) -> str:
    """规范化单行树：去掉「当换行用」的粘连 │，统一缩进与 |— 写法。"""
    s = line.rstrip("\n").rstrip("│").strip()
    if not s or _looks_like_table_line(s) or _TABLE_OR_HR_PIPE.match(s):
        return line.rstrip("\n") if _looks_like_table_line(line) else s

    # 行首多余 │（模型用 │ 当换行）：│├── → ├── ；保留 │   ├── 这种真缩进
    s = re.sub(r"^│+(?=[├└])", "", s)
    s = re.sub(r"^│{2,}(?=\S)", "│   ", s)

    # ||—foo / |—foo（排除 |---| / |---##）
    s2 = re.sub(
        rf"^([\s]*)\|{{2,}}[\s]*{_DASH}+(?![|\-:#])\s*",
        r"\1│   ├── ",
        s,
    )
    if s2 != s:
        s = s2
    elif (
        re.match(rf"^[\s]*\|[\s]*{_DASH}+(?![|\-:#])\s*[^\s|:\-#]", s)
        and not re.match(rf"^[\s]*\|{_DASH}+\|\s*$", s)
    ):
        s = re.sub(
            rf"^([\s]*)\|[\s]*{_DASH}+(?![|\-:#])\s*",
            r"\1├── ",
            s,
        )

    # ├──name → ├── name ；│└──name → │   └── name
    s = re.sub(r"^([├└]──)\s*", r"\1 ", s)
    s = re.sub(r"^(│\s*)+([├└]──)\s*", r"│   \2 ", s)
    # ASCII gutter
    if re.match(r"^[\s]*\|[\s]*[├└]", s):
        m = re.match(r"^([\s|│]*)(.*)$", s)
        if m:
            gut, rest = m.group(1), m.group(2)
            s = gut.replace("|", "│") + rest
        s = re.sub(r"│[ \t]*([├└]──)\s*", r"│   \1 ", s)

    # ←注释 → 右侧空格分隔（保持可读）
    s = re.sub(r"(←)", r"  # ", s, count=1)
    return s


def _restore_tree_newlines(t: str) -> str:
    """在分支标记前补换行。

    模型常把整棵树挤成一行，并用 │ 代替换行，例如：
    ``my_item/←说明│├──agent/…│└──providers/…├──assets/…``
    此时分支前往往**没有空白**，必须按标记硬拆。
    """
    if not _has_tree_markers(t):
        return t

    # 1) │ 紧贴 ├──/└──：把 │ 当换行符
    t = re.sub(r"│(?=[├└])", "\n", t)
    # 2) 任意非换行字符后紧跟 ├──/└──（无空白）
    t = re.sub(r"([^\n])(?=[├└]──)", r"\1\n", t)
    # 3) 仍兼容「空白 + 分支」的旧写法
    t = re.sub(r"[ \t]+(?=(?:[│|][ \t]*)+[├└]──)", "\n", t)
    t = re.sub(r"(?<![│|\s])[ \t]+(?=[├└]──)", "\n", t)
    # 4) |—name / ||—name（排除 |---| 表分隔与 |---##）
    tip = rf"(?:\|[\s]*)+{_DASH}+(?![|\-:#])[^\s|:\-#]"
    t = re.sub(rf"([^\n])(?={tip})", r"\1\n", t)
    t = re.sub(rf"[ \t]+(?={tip})", "\n", t)

    fixed: list[str] = []
    for line in t.split("\n"):
        raw = line.strip()
        if not raw:
            continue
        if raw in ("|", "│", "||") or _TABLE_OR_HR_PIPE.match(raw):
            # 丢弃孤立管道；表分隔留给后续表格逻辑
            if _looks_like_table_line(raw):
                fixed.append(raw)
            continue
        if _looks_like_table_line(raw):
            fixed.append(raw)
            continue
        if (
            _is_tree_line(raw)
            or _TREE_TIP_INLINE.search(raw)
            or raw.startswith(("├", "└", "│"))
        ):
            fixed.append(_canonicalize_tree_line(raw))
        else:
            if "←" in raw and "/" in raw:
                raw = re.sub(r"←", "  # ", raw, count=1)
            fixed.append(raw.rstrip("│").strip())
    return "\n".join(fixed)


def _wrap_tree_blocks(t: str) -> str:
    """裸目录树 → ```text 代码块。"""
    parts: list[tuple[str, str]] = []
    pos = 0
    for m in _CODE_FENCE.finditer(t):
        if m.start() > pos:
            parts.append(("text", t[pos : m.start()]))
        parts.append(("code", m.group(0)))
        pos = m.end()
    if pos < len(t):
        parts.append(("text", t[pos:]))
    if not parts:
        parts = [("text", t)]

    out: list[str] = []
    for kind, body in parts:
        if kind == "code":
            out.append(body)
            continue
        lines = body.split("\n")
        buf: list[str] = []
        i = 0
        while i < len(lines):
            take_root = (
                _is_tree_root_line(lines[i])
                and i + 1 < len(lines)
                and _is_tree_line(lines[i + 1])
            )
            multi = len(_TREE_TIP_INLINE.findall(lines[i])) >= 2
            if take_root or _is_tree_line(lines[i]) or multi:
                block: list[str] = []
                if take_root:
                    block.append(lines[i].rstrip())
                    i += 1
                elif multi and not _is_tree_line(lines[i]):
                    block.extend(
                        ln.rstrip()
                        for ln in _restore_tree_newlines(lines[i]).split("\n")
                        if ln.strip()
                    )
                    i += 1
                while i < len(lines) and _is_tree_line(lines[i]):
                    block.append(lines[i].rstrip())
                    i += 1
                if len(block) == 1 and len(_TREE_TIP_INLINE.findall(block[0])) >= 2:
                    block = [
                        ln.rstrip()
                        for ln in _restore_tree_newlines(block[0]).split("\n")
                        if ln.strip()
                    ]
                block = [_canonicalize_tree_line(ln) for ln in block]
                if len(block) >= 2:
                    if buf:
                        out.append("\n".join(buf))
                        buf = []
                    out.append("```text\n" + "\n".join(block) + "\n```")
                else:
                    buf.extend(block)
                continue
            buf.append(lines[i])
            i += 1
        if buf:
            out.append("\n".join(buf))
    return "".join(out) if len(out) == 1 else "\n".join(out)


def _split_crushed_tables(t: str) -> str:
    """把挤成一行的 GFM 表格拆成多行。

    典型脏输出：
    ``##一、文件清单|分类|文件||---|---||a|b||c|d|---##二``
    """
    # 真标题粘表头：仅 ##～######（勿匹配 issue 引用「(# 7)|下一格」）
    t = re.sub(r"(#{2,6} [^\n|#]+?)\|", r"\1\n|", t)
    # 单级 # 标题仅在行首（避免 ( # 7) / 正文 # 标签）
    t = re.sub(r"(?m)^(#{1} [^\n|#]+?)\|", r"\1\n|", t)

    if "||" in t:
        # 表行边界：||---| → |\n|---|（必须优先，勿当树）
        t = re.sub(r"\|\|(?=\s*:?-+:?\s*(?:\||$))", "|\n|", t)
        # 其余 || → 换行，但保留 ||—name / ||--name 伪树
        t = re.sub(
            rf"\|\|(?![\s]*{_DASH}+(?![|\-:#])[^\s|:\-#])",
            "|\n|",
            t,
        )
        t = re.sub(r"\|\n\|\|", "|\n|", t)
        t = re.sub(r"^\|\|", "|", t, flags=re.M)

    # 表尾粘标题：...|---##二 ；|## 二（##+ 才拆，避开 |# 标签）
    t = re.sub(r"\|-{2,}(#{1,6} )", r"\n\n---\n\n\1", t)
    t = re.sub(r"\|(\s*#{2,6} )", r"|\n\1", t)
    return t


def _split_glued_headings(t: str) -> str:
    """拆开粘在一起的 ATX 标题。"""
    t = re.sub(r"(#{1,6})([^\s#])", r"\1 \2", t)
    # 标题正文不得吞掉 |（否则会把整表当成标题，再在下一个 ## 前硬拆）
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"(#{1,6} [^\n|#]+?)(#{1,6} )", r"\1\n\2", t)
    return t


def _peel_heading_prefix(line: str) -> list[str]:
    """表行误吞标题：|## 一、清单|分类| → [## 一、清单, |分类|...]"""
    s = line.strip()
    m = re.match(r"^\|?(#{1,6} [^|]+)\|(.*)$", s)
    if not m:
        return [line]
    heading = m.group(1).strip()
    rest = (m.group(2) or "").strip()
    out = [heading]
    if rest:
        if not rest.startswith("|"):
            rest = "|" + rest
        if not rest.endswith("|"):
            rest = rest + "|"
        out.append(rest)
    return out


def normalize_markdown(text: str) -> str:
    """修复模型常把表格/标题/目录树挤成一行的情况。"""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")

    t = _split_glued_headings(t)

    # 正文后的 HR：。---## → 。\n\n---\n\n##
    t = re.sub(r"([^\n|\-])-{3,}(\s*#{1,6} )", r"\1\n\n---\n\n\2", t)
    t = re.sub(r"([^\n|\-])-{3,}([^\n|\-#])", r"\1\n\n---\n\n\2", t)

    t = _split_crushed_tables(t)

    # 编号列表粘连：优点1.**双 → 优点\n1.**双
    t = re.sub(r"([^\n\d.])(\d+\.\*\*)", r"\1\n\2", t)
    # 列表项：可视化-启动**xxx**
    t = re.sub(
        r"([^\n\-\|`])-(?=[\u4e00-\u9fffA-Za-z0-9_]*\*\*)",
        r"\1\n- ",
        t,
    )

    # 列表/正文处理后可能又粘上标题
    t = _split_glued_headings(t)

    t = _restore_tree_newlines(t)

    fixed: list[str] = []
    for line in t.split("\n"):
        s = line.strip()
        if not s:
            fixed.append("")
            continue
        if s in ("|", "||", "│"):
            continue
        for part in _peel_heading_prefix(s):
            ps = part.strip()
            if not ps or ps in ("|", "||"):
                continue
            if _looks_like_table_line(ps) and not _is_tree_line(ps):
                if not ps.startswith("|"):
                    ps = "|" + ps
                # 数据行尾粘了 |---（HR/下一节残渣），不是真正的分隔行
                if not re.match(
                    r"^\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$", ps
                ):
                    ps = re.sub(r"\|-{2,}\|?\s*$", "|", ps)
                if not ps.endswith("|"):
                    ps = ps + "|"
                fixed.append(ps)
            else:
                fixed.append(ps)
    t = "\n".join(fixed)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = _wrap_tree_blocks(t)
    t = _repair_markdown_tables(t)
    return t


def _table_cells(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _is_table_sep_cells(cells: list[str]) -> bool:
    if not cells:
        return False
    return all(re.match(r"^:?-+:?$", (c or "").replace(" ", "")) for c in cells)


def _escape_cell_angles(cell: str) -> str:
    """避免 <gazebo> 等被当成 HTML 标签吞掉（保留 <br>）。"""
    if not cell or "<" not in cell:
        return cell
    token = "%%BR%%"
    protected = re.sub(r"<br\s*/?>", token, cell, flags=re.I)
    protected = re.sub(
        r"<(/?[A-Za-z][\w:-]*)>",
        lambda m: f"`<{m.group(1)}>`",
        protected,
    )
    return protected.replace(token, "<br>")


def _format_table_row(cells: list[str]) -> str:
    return "|" + "|".join(cells) + "|"


_PRIORITY_CELL_RE = re.compile(
    r"^(?:"
    r"[●•◆■▪]|[🔴🟡🟢🔵🟠⚪⚫]|"
    r"P[0-3]\b|"
    r"(?:优先级\s*)?[高中低]|"
    r"(?:紧急|重要|次要|建议)"
    r")"
)


def _looks_like_priority_cell(cell: str) -> bool:
    s = (cell or "").strip()
    if not s:
        return False
    return bool(_PRIORITY_CELL_RE.match(s))


def _row_filled(cells: list[str]) -> list[str]:
    return [c for c in cells if (c or "").strip()]


def _is_heading_or_prose_row(cells: list[str]) -> bool:
    """应从表中拆出的标题/后果说明行。"""
    filled = _row_filled(cells)
    if not filled:
        return False
    text = filled[0].strip()
    if text.startswith("#"):
        return True
    if re.match(r"^\*\*(?:后果|建议|原因|说明|注意|结论|影响)\*\*", text):
        return True
    if re.match(r"^(?:后果|建议|原因|说明|注意|结论)[:：]", text):
        return True
    if len(filled) == 1 and len(text) >= 60:
        return True
    return False


def _cells_to_prose(cells: list[str]) -> str:
    filled = _row_filled(cells)
    if not filled:
        return ""
    if len(filled) == 1:
        return filled[0].strip()
    return " ".join(filled)


def _merge_table_continuation_rows(
    body: list[list[str]], target: int
) -> tuple[list[list[str]], list[str]]:
    """短「影响」续行合并进上一行末列；标题/后果进 leftovers。"""
    leftovers: list[str] = []
    merged: list[list[str]] = []

    def _pad(cells: list[str]) -> list[str]:
        cells = list(cells)
        if len(cells) < target:
            cells.extend([""] * (target - len(cells)))
        return cells[:target]

    for r in body:
        r = list(r)
        if len(r) > target:
            extra = [c for c in r[target:] if (c or "").strip()]
            if extra:
                leftovers.append(" ".join(extra))
            r = r[:target]
        if _is_heading_or_prose_row(r):
            leftovers.append(_cells_to_prose(r))
            continue
        filled = _row_filled(r)
        is_cont = (
            bool(merged)
            and len(filled) == 1
            and not _looks_like_priority_cell(filled[0])
            and len(filled[0]) < 60
            and not filled[0].startswith(("#", "**"))
        )
        if is_cont:
            text = filled[0]
            prev = merged[-1]
            if not (prev[-1] or "").strip():
                prev[-1] = text
            else:
                prev[-1] = f"{prev[-1]}；{text}"
            continue
        merged.append(_pad(r))
    return merged, leftovers


def _emit_single_table(
    header: list[str], sep: list[str], body: list[list[str]]
) -> list[str]:
    """输出一张对齐后的 GFM 表（及拆出的散文）。"""
    from collections import Counter

    prefix: list[str] = []
    header = list(header)
    sep = list(sep)
    body = [list(r) for r in body]

    if len(header) == len(sep) + 1 and header[0]:
        first = header[0]
        if first.endswith(":") or len(first) <= 16:
            title = first.rstrip(":").strip()
            if title:
                prefix.extend([f"### {title}", ""])
            header = header[1:]

    body_lens = [len(r) for r in body]
    mode = Counter(body_lens).most_common(1)[0][0] if body_lens else len(sep)
    sep_n, hdr_n = len(sep), len(header)

    if hdr_n == sep_n and sep_n >= 2:
        target = sep_n
    elif sep_n >= 2 and body and sum(1 for r in body if len(r) >= sep_n) >= (len(body) + 1) // 2:
        target = sep_n
        if hdr_n > target:
            header = header[:target]
        elif hdr_n < target:
            header = header + [""] * (target - hdr_n)
    elif hdr_n >= 2 and abs(hdr_n - mode) <= abs(sep_n - mode):
        target = hdr_n
    else:
        target = mode if mode >= 2 else max(sep_n, hdr_n, 2)
        header = (header + [""] * target)[:target]

    def _pad(cells: list[str], n: int) -> list[str]:
        cells = list(cells)
        if len(cells) < n:
            cells.extend([""] * (n - len(cells)))
        return cells[:n]

    header = _pad(header, target)
    sep = ["---"] * target
    body, leftovers = _merge_table_continuation_rows(body, target)
    header = [_escape_cell_angles(c) for c in header]
    body = [[_escape_cell_angles(c) for c in r] for r in body]

    out = prefix + [_format_table_row(header), _format_table_row(sep)]
    out.extend(_format_table_row(r) for r in body)
    # python-markdown tables 会把表后「无空行的下一行」吞成数据行，必须空行收尾
    out.append("")
    if leftovers:
        out.extend(leftovers)
        out.append("")
    return out


def _fix_one_table_block(block: list[str]) -> list[str]:
    """修复连续表行：按分隔行切多表，拆出 ### / 后果 等散文。

    避免第二张表的 ``|---|---|`` 被吞成字面 ``---`` 数据行。
    """
    if len(block) < 2:
        return block

    rows = [_table_cells(ln) for ln in block]
    out: list[str] = []
    i = 0
    n = len(rows)

    while i < n:
        if _is_heading_or_prose_row(rows[i]):
            out.append(_cells_to_prose(rows[i]))
            out.append("")
            i += 1
            continue
        if _is_table_sep_cells(rows[i]):
            i += 1
            continue

        # 需要 header + 下一行 sep
        if i + 1 >= n or not _is_table_sep_cells(rows[i + 1]):
            out.append(_cells_to_prose(rows[i]) or _format_table_row(rows[i]))
            i += 1
            continue

        header = rows[i]
        sep = rows[i + 1]
        i += 2
        body: list[list[str]] = []

        while i < n:
            if _is_table_sep_cells(rows[i]):
                # 多表：body 最后一行是下一表头
                if body and not _is_heading_or_prose_row(body[-1]):
                    next_header = body.pop()
                    out.extend(_emit_single_table(header, sep, body))
                    header = next_header
                    sep = rows[i]
                    body = []
                    i += 1
                    continue
                i += 1
                continue
            if _is_heading_or_prose_row(rows[i]):
                # 先结束当前表，再输出散文
                out.extend(_emit_single_table(header, sep, body))
                out.append(_cells_to_prose(rows[i]))
                out.append("")
                header, sep, body = [], [], []
                i += 1
                # 后续若再出现 header+sep，外层 while 继续；若仍是表数据行则吞掉？
                # 散文后的表数据若无新 header，作散文列表
                while i < n and not _is_table_sep_cells(rows[i]):
                    if i + 1 < n and _is_table_sep_cells(rows[i + 1]):
                        break  # 新表开始
                    if _is_heading_or_prose_row(rows[i]):
                        out.append(_cells_to_prose(rows[i]))
                        out.append("")
                        i += 1
                        continue
                    # 残缺行
                    out.append(_cells_to_prose(rows[i]) or _format_table_row(rows[i]))
                    i += 1
                break
            body.append(rows[i])
            i += 1
        else:
            if header and sep:
                out.extend(_emit_single_table(header, sep, body))
            continue

        if header and sep:
            out.extend(_emit_single_table(header, sep, body))

    cleaned: list[str] = []
    for ln in out:
        if ln == "" and cleaned and cleaned[-1] == "":
            continue
        cleaned.append(ln)
    return cleaned


def _repair_markdown_tables(t: str) -> str:
    lines = t.split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if not _looks_like_table_line(lines[i]) or _is_tree_line(lines[i]):
            out.append(lines[i])
            i += 1
            continue
        block: list[str] = []
        while i < n and _looks_like_table_line(lines[i]) and not _is_tree_line(lines[i]):
            block.append(lines[i])
            i += 1
        out.extend(_fix_one_table_block(block))
    return "\n".join(out)


def _get_markdown_converter():
    try:
        import markdown as md_lib
    except ImportError as e:
        raise ImportError("需要安装 markdown 库：pip install markdown") from e
    return md_lib.Markdown(extensions=list(_MD_EXT), output_format="html")


_converter = None


def _convert(raw: str) -> str:
    global _converter
    try:
        if _converter is None:
            _converter = _get_markdown_converter()
        _converter.reset()
        return _converter.convert(raw)
    except ImportError:
        esc = html.escape(raw).replace("\n", "<br>\n")
        return f"<p>{esc}</p>"


def markdown_to_html(text: str) -> str:
    """将 MD 正文转为可放入 QTextBrowser 的 HTML。"""
    raw = normalize_markdown(text or "")
    if not raw.strip():
        return "<p>（空）</p>"
    body = _convert(raw)
    return (
        f'<div style="{_WRAP_STYLE}">'
        f"<style>{_DOC_STYLE}</style>"
        f"{body}</div>"
    )
