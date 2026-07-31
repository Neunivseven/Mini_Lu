"""Skills：Cursor 风格 SKILL.md 扫描、启用、注入 system。"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from agent.llm_client import app_dir, config_read_path, config_write_path, user_dir

_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    disable_model_invocation: bool = True
    always_inject: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return self.body.strip()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_skills_config() -> dict[str, Any]:
    cfg = _read_yaml(config_read_path("skills.yaml"))
    local = _read_yaml(config_read_path("skills.local.yaml"))
    if local:
        # 浅合并列表类字段以 local 为准
        for k, v in local.items():
            cfg[k] = v
    cfg.setdefault("enabled", True)
    cfg.setdefault("auto_inject_max_chars", 6000)
    cfg.setdefault("enabled_skills", [])
    cfg.setdefault("disabled_skills", [])
    cfg.setdefault("skill_modes", {})  # name -> auto|manual|always（UI 覆盖，不改 SKILL.md）
    cfg.setdefault("roots", ["skills", "data/skills"])
    return cfg


def effective_invocation_mode(sk: Skill, cfg: dict[str, Any] | None = None) -> str:
    """实际调用模式：优先 skills.local.yaml 的 skill_modes，否则读 SKILL.md。

    返回 ``auto`` | ``manual`` | ``always``。
    """
    cfg = cfg or load_skills_config()
    modes = cfg.get("skill_modes") or {}
    if isinstance(modes, dict):
        raw = modes.get(sk.name)
        if raw is not None:
            m = str(raw).strip().lower()
            if m in ("auto", "manual", "always"):
                return m
    if sk.always_inject:
        return "always"
    if not sk.disable_model_invocation:
        return "auto"
    return "manual"


def set_skill_mode(name: str, mode: str) -> None:
    """设置 skill 的 auto/manual/always，写入 skills.local.yaml（不改 SKILL.md）。"""
    key = (name or "").strip()
    if not key:
        raise ValueError("skill 名为空")
    m = (mode or "").strip().lower()
    if m not in ("auto", "manual", "always"):
        raise ValueError("模式须为 auto / manual / always")
    cfg = load_skills_config()
    modes = dict(cfg.get("skill_modes") or {})
    modes[key] = m
    save_skills_local_patch({"skill_modes": modes})


def _parse_skill_md(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    meta: dict[str, Any] = {}
    body = text
    m = _FRONT_RE.match(text)
    if m:
        try:
            meta = yaml.safe_load(m.group(1)) or {}
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        body = m.group(2)
    name = str(meta.get("name") or path.parent.name).strip()
    if not name:
        return None
    desc = str(meta.get("description") or "").strip()
    disable = meta.get("disable-model-invocation")
    if disable is None:
        disable = meta.get("disable_model_invocation", True)
    always = bool(meta.get("always_inject") or meta.get("always-inject") or False)
    return Skill(
        name=name,
        description=desc or f"Skill {name}",
        body=body,
        path=path,
        disable_model_invocation=bool(disable),
        always_inject=always,
        meta=meta,
    )


def skill_roots(cfg: dict[str, Any] | None = None) -> list[Path]:
    cfg = cfg or load_skills_config()
    roots: list[Path] = []
    for r in cfg.get("roots") or []:
        p = Path(str(r))
        if not p.is_absolute():
            p = app_dir() / p
        roots.append(p)
    # 打包版：用户自建 skill 存放在用户目录，也纳入发现范围
    user_skills = user_dir() / "skills"
    if user_skills not in roots:
        roots.append(user_skills)
    return roots


def discover_skills(cfg: dict[str, Any] | None = None) -> list[Skill]:
    cfg = cfg or load_skills_config()
    found: dict[str, Skill] = {}
    for root in skill_roots(cfg):
        if not root.is_dir():
            continue
        for skill_md in root.glob("*/SKILL.md"):
            sk = _parse_skill_md(skill_md)
            if sk:
                found[sk.name] = sk
    return sorted(found.values(), key=lambda s: s.name)


def enabled_skills(cfg: dict[str, Any] | None = None) -> list[Skill]:
    cfg = cfg or load_skills_config()
    if not cfg.get("enabled", True):
        return []
    allow = [str(x) for x in (cfg.get("enabled_skills") or []) if str(x).strip()]
    deny = {str(x) for x in (cfg.get("disabled_skills") or []) if str(x).strip()}
    out: list[Skill] = []
    for sk in discover_skills(cfg):
        if sk.name in deny:
            continue
        if allow and sk.name not in allow:
            continue
        out.append(sk)
    return out


def get_skill(name: str) -> Skill | None:
    key = (name or "").strip()
    if not key:
        return None
    for sk in enabled_skills():
        if sk.name == key:
            return sk
    # 也允许从全量发现中取（即使被 disabled 列表挡住，工具可明示）
    for sk in discover_skills():
        if sk.name == key:
            return sk
    return None


def format_skills_catalog(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg or load_skills_config()
    if not cfg.get("enabled", True):
        return ""
    skills = enabled_skills(cfg)
    if not skills:
        return ""
    lines = [
        "【Skills 目录】需要细则时调用工具 load_skill(name)。",
        "可用 skills：",
    ]
    for sk in skills:
        mode = effective_invocation_mode(sk, cfg)
        flag = "auto" if mode in ("auto", "always") else "on-demand"
        lines.append(f"- {sk.name} [{flag}]: {sk.description}")
    return "\n".join(lines)


def format_skills_inject_block(cfg: dict[str, Any] | None = None) -> str:
    """注入 system：目录 + 自动正文（受字符上限）。"""
    cfg = cfg or load_skills_config()
    catalog = format_skills_catalog(cfg)
    if not catalog:
        return ""
    parts = [catalog]
    budget = max(500, int(cfg.get("auto_inject_max_chars") or 6000))
    used = len(catalog)
    for sk in enabled_skills(cfg):
        mode = effective_invocation_mode(sk, cfg)
        if mode == "manual":
            continue
        chunk = f"\n\n### Skill: {sk.name}\n{sk.full_text}"
        if used + len(chunk) > budget:
            parts.append(
                f"\n（其余 auto/always skill 因长度未注入，请 load_skill('{sk.name}')）"
            )
            break
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts).strip()


def format_skills_report() -> str:
    cfg = load_skills_config()
    lines = [
        f"skills enabled={cfg.get('enabled')}",
        f"roots: {', '.join(str(p) for p in skill_roots(cfg))}",
    ]
    for sk in discover_skills(cfg):
        on = sk in enabled_skills(cfg)
        mode = effective_invocation_mode(sk, cfg)
        mark = "ON" if on else "--"
        lines.append(f"  {mark} {sk.name} [{mode}] — {sk.description[:80]}")
    return "\n".join(lines)


SKILLS_GUIDE = """接入步骤（约 1 分钟）

1. 点「打开 Skills 目录」→ 进入 skills/
2. 点「新建 Skill…」或手动建文件夹 skills/<英文名>/SKILL.md
3. 填写 YAML 头：name / description
4. 回到本面板点「刷新」→ 列表出现后可「启用 / 禁用」
5. 用「设为 Auto / Manual」切换调用模式（写入 skills.local.yaml，不改 SKILL.md）
6. 对话里说相关需求，或让 Agent 调用 load_skill("名字") 加载全文

模式说明
· auto：目录可见，模型可自行 load_skill
· manual：仅目录可见，需显式 load_skill
· always：正文自动注入 system（慎用，占 token）

细则见 docs/SKILLS.md
"""


def skills_guide_html() -> str:
    """接入教程富文本（供扩展面板展示）。"""
    return """
<div class="wrap">
  <h1>Skills 接入教程</h1>
  <p class="lead">大约 1 分钟。写好 <code>SKILL.md</code> 后，模型就能按需加载专项流程。</p>

  <h2>快速接入</h2>
  <ol class="steps">
    <li>
      <span class="n">1</span>
      <div>
        <strong>打开目录</strong>
        <p>点「打开目录」进入 <code>skills/</code>。</p>
      </div>
    </li>
    <li>
      <span class="n">2</span>
      <div>
        <strong>新建 Skill</strong>
        <p>点「新建 Skill…」，或手动创建 <code>skills/&lt;英文名&gt;/SKILL.md</code>。</p>
      </div>
    </li>
    <li>
      <span class="n">3</span>
      <div>
        <strong>填写 YAML 头</strong>
        <p>至少写清 <code>name</code> 与 <code>description</code>（description 供模型检索）。</p>
      </div>
    </li>
    <li>
      <span class="n">4</span>
      <div>
        <strong>刷新并启用</strong>
        <p>回到本面板点「刷新」，在列表中「启用 / 禁用」。</p>
      </div>
    </li>
    <li>
      <span class="n">5</span>
      <div>
        <strong>选择调用模式</strong>
        <p>Auto / Manual / Always 写入 <code>skills.local.yaml</code>，不会改 <code>SKILL.md</code>。</p>
      </div>
    </li>
    <li>
      <span class="n">6</span>
      <div>
        <strong>在对话中使用</strong>
        <p>直接提相关需求，或让 Agent 调用 <code>load_skill("名字")</code> 加载全文。</p>
      </div>
    </li>
  </ol>

  <h2>调用模式</h2>
  <div class="modes">
    <div class="mode">
      <div class="mode-title">Auto</div>
      <p>目录可见，模型可自行 <code>load_skill</code>。</p>
    </div>
    <div class="mode">
      <div class="mode-title">Manual</div>
      <p>仅目录可见，需显式加载后才展开正文。</p>
    </div>
    <div class="mode">
      <div class="mode-title">Always</div>
      <p>正文尝试注入 system；占 token，请慎用。</p>
    </div>
  </div>

  <p class="foot">更细的约定见仓库文档 <code>docs/SKILLS.md</code>。</p>
</div>
"""

_SKILL_TEMPLATE = """---
name: {name}
description: >-
  在什么场景下应使用本 Skill（一句话，供模型检索）。
disable-model-invocation: false
always_inject: false
---

# {title}

## 何时使用

（写清楚触发场景）

## 工作步骤

1. …
2. …

## 注意

-
"""


def _local_skills_path() -> Path:
    return config_write_path("skills.local.yaml")


def save_skills_local_patch(patch: dict[str, Any]) -> Path:
    """合并写入 config/skills.local.yaml（不覆盖未提及字段）。"""
    path = _local_skills_path()
    cur = _read_yaml(path)
    for k, v in patch.items():
        cur[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(cur, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return path


def set_skill_enabled(name: str, enabled: bool) -> None:
    """启用/禁用单个 skill（写入 skills.local.yaml 的 disabled_skills）。"""
    key = (name or "").strip()
    if not key:
        raise ValueError("skill 名为空")
    cfg = load_skills_config()
    deny = [str(x) for x in (cfg.get("disabled_skills") or []) if str(x).strip()]
    if enabled:
        deny = [x for x in deny if x != key]
    elif key not in deny:
        deny.append(key)
    save_skills_local_patch({"disabled_skills": deny})


def create_skill(
    name: str,
    *,
    root: Path | None = None,
    open_description: str = "",
) -> Path:
    """在 skills/<name>/SKILL.md 创建模板（已存在则报错）。"""
    raw = (name or "").strip()
    safe = re.sub(r"[^\w\-]+", "-", raw, flags=re.UNICODE).strip("-").lower()
    if not safe:
        raise ValueError("请使用英文/数字/连字符作为 skill 目录名")
    base = root or default_skills_dir()
    base.mkdir(parents=True, exist_ok=True)
    folder = base / safe
    skill_md = folder / "SKILL.md"
    if skill_md.exists():
        raise FileExistsError(f"已存在: {skill_md}")
    folder.mkdir(parents=True, exist_ok=False)
    title = raw if raw != safe else safe
    desc = (open_description or "").strip() or f"在需要「{title}」相关流程时使用。"
    text = _SKILL_TEMPLATE.format(name=safe, title=title).replace(
        "在什么场景下应使用本 Skill（一句话，供模型检索）。",
        desc,
        1,
    )
    skill_md.write_text(text, encoding="utf-8")
    return skill_md


def default_skills_dir() -> Path:
    """用户自建 skill 的默认目录（打包版在用户目录，不随更新丢失）。"""
    p = user_dir() / "skills"
    p.mkdir(parents=True, exist_ok=True)
    return p
