"""
确定性工作流：按 phase 顺序执行，每步独立 invoke Agent，journal 落盘可回看。

对齐 CCB workflow-engine 的精简版：phase + pipeline（串行），无 JS 脚本解释器。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from agent.llm_client import app_dir

AgentInvoke = Callable[[str], str]


def workflows_dir() -> Path:
    p = app_dir() / "data" / "workflows"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clip(text: str, n: int = 4000) -> str:
    text = (text or "").strip()
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def parse_plan(plan: str | dict[str, Any]) -> dict[str, Any]:
    """
    plan 支持：
    - dict: {name, steps:[{phase, instruction}, ...]}
    - JSON 字符串
    - 简易多行：每行  阶段名|指令
    """
    if isinstance(plan, dict):
        data = plan
    else:
        raw = (plan or "").strip()
        if not raw:
            raise ValueError("工作流 plan 为空")
        if raw.startswith("{"):
            data = json.loads(raw)
        else:
            steps = []
            for line in raw.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "|" in line:
                    phase, instruction = line.split("|", 1)
                else:
                    phase, instruction = f"step{len(steps)+1}", line
                steps.append({"phase": phase.strip(), "instruction": instruction.strip()})
            data = {"name": "adhoc", "steps": steps}

    name = str(data.get("name") or "workflow").strip() or "workflow"
    steps_in = data.get("steps") or data.get("phases") or []
    if not isinstance(steps_in, list) or not steps_in:
        raise ValueError("plan 需要非空 steps/phases 列表")
    steps: list[dict[str, str]] = []
    for i, s in enumerate(steps_in):
        if isinstance(s, str):
            steps.append({"phase": f"phase{i+1}", "instruction": s.strip()})
            continue
        if not isinstance(s, dict):
            continue
        instr = (s.get("instruction") or s.get("prompt") or s.get("task") or "").strip()
        if not instr:
            continue
        phase = (s.get("phase") or s.get("name") or f"phase{i+1}").strip()
        steps.append({"phase": phase, "instruction": instr})
    if not steps:
        raise ValueError("没有有效步骤")
    return {"name": name, "steps": steps}


def run_pipeline(
    plan: str | dict[str, Any],
    *,
    invoke: AgentInvoke,
    persist: bool = True,
) -> dict[str, Any]:
    """串行执行各 phase；invoke(prompt)->reply。"""
    parsed = parse_plan(plan)
    run_id = uuid.uuid4().hex[:10]
    journal: list[dict[str, Any]] = []
    prev_summary = ""

    for i, step in enumerate(parsed["steps"]):
        phase = step["phase"]
        instruction = step["instruction"]
        prompt_parts = [
            f"【工作流 · {parsed['name']}】",
            f"【阶段 {i+1}/{len(parsed['steps'])} · {phase}】",
            instruction,
            "请完成本阶段；可用工具。只完成本阶段，不要跳到未列出的后续阶段。",
        ]
        if prev_summary:
            prompt_parts.append("---\n【此前阶段结果摘要】\n" + prev_summary)
        prompt = "\n".join(prompt_parts)
        try:
            result = invoke(prompt)
        except Exception as e:
            entry = {
                "phase": phase,
                "index": i,
                "ok": False,
                "error": str(e),
                "ts": _now(),
            }
            journal.append(entry)
            break
        entry = {
            "phase": phase,
            "index": i,
            "ok": True,
            "result": _clip(result, 6000),
            "ts": _now(),
        }
        journal.append(entry)
        # 累计摘要供下一步（控制长度）
        chunk = f"[{phase}] {_clip(result, 800)}"
        prev_summary = (prev_summary + "\n" + chunk).strip()
        if len(prev_summary) > 3500:
            prev_summary = prev_summary[-3500:]

    record = {
        "id": run_id,
        "name": parsed["name"],
        "started_at": journal[0]["ts"] if journal else _now(),
        "finished_at": _now(),
        "steps_planned": len(parsed["steps"]),
        "steps_done": sum(1 for j in journal if j.get("ok")),
        "journal": journal,
        "ok": bool(journal) and all(j.get("ok") for j in journal),
    }
    if persist:
        path = workflows_dir() / f"{run_id}_{parsed['name']}.json"
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        record["path"] = str(path)
    return record


def format_run_report(record: dict[str, Any]) -> str:
    lines = [
        f"工作流「{record.get('name')}」#{record.get('id')}",
        f"结果: {'成功' if record.get('ok') else '未完成/失败'}",
        f"步骤: {record.get('steps_done')}/{record.get('steps_planned')}",
    ]
    if record.get("path"):
        lines.append(f"journal: {record['path']}")
    for j in record.get("journal") or []:
        tag = "[OK]" if j.get("ok") else "[FAIL]"
        lines.append(f"\n{tag} [{j.get('phase')}]")
        if j.get("ok"):
            lines.append(_clip(str(j.get("result") or ""), 1200))
        else:
            lines.append(f"错误: {j.get('error')}")
    return "\n".join(lines)


def list_recent_runs(limit: int = 8) -> str:
    d = workflows_dir()
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    files = files[: max(1, min(int(limit), 20))]
    if not files:
        return "尚无工作流运行记录"
    lines = []
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            lines.append(
                f"- {data.get('id')} {data.get('name')} "
                f"{'OK' if data.get('ok') else 'FAIL'} "
                f"{data.get('steps_done')}/{data.get('steps_planned')} "
                f"@ {data.get('finished_at')}"
            )
        except Exception:
            lines.append(f"- {f.name} （无法读取）")
    return "\n".join(lines)
