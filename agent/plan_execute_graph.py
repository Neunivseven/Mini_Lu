"""
Plan-and-Execute 根图：自动路由 + Planner / 嵌套 ReAct Executor / Replanner。

简单任务 → 现有 ReAct；复杂多步 → plan → execute → replan 循环。
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Optional, Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field
from typing_extensions import TypedDict

from agent.agent_config import AgentRuntimeConfig, load_agent_config
from agent.llm_client import LLMConfig



class PlanExecuteState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    input: str
    plan: list[str]
    past_steps: list[tuple[str, str]]
    response: str
    route: Literal["react", "plan"]


class PlanSteps(BaseModel):
    """结构化计划。"""

    steps: list[str] = Field(description="有序、可执行的步骤列表；每步一句，不要编号")


class ReplanDecision(BaseModel):
    """重规划：要么给出最终回答，要么给出剩余步骤。"""

    done: bool = Field(description="True 表示任务已完成，应填写 response")
    response: str = Field(default="", description="最终给用户的中文回答；done=True 时必填")
    steps: list[str] = Field(
        default_factory=list,
        description="仍需执行的剩余步骤；done=False 时填写（可为空表表示无法继续）",
    )


# 路由启发式：默认 ReAct；仅明确多步/批量/先…再…才走 Plan。

_PLAN_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    # 显式要计划 / 分步
    re.compile(r"(分步|一步步|制定计划|按步骤|分阶段|做个计划|列个计划)"),
    # 清晰的先后链条（先…再…然后 / 先…再…再）
    re.compile(r"先.{1,24}再.{1,24}(然后|再|接着|之后)"),
    re.compile(r"(然后|接着|之后).{0,16}(再|并).{0,12}(改|修|写|编译|测试|运行|部署|提交)"),
    # 搜/扫 后再改（跨步）
    re.compile(r"(扫描|搜索|查找|遍历).{0,24}(并|然后|接着).{0,12}(修改|编辑|改写|替换|编译|构建)"),
    # 构建系统联动
    re.compile(r"(CMake|cmake|Makefile|package\.json).{0,30}(改|编|build|编译)"),
    # 批量范围
    re.compile(r"(工作区|项目|仓库).{0,16}(全部|所有|批量|整库)"),
    # 实现+多交付物
    re.compile(r"(实现|开发|重构).{0,20}(功能|模块|接口).{0,20}(并|然后|以及).{0,12}(测试|文档|编译|部署)"),
]

_SIMPLE_HINTS = re.compile(
    r"^(你好|嗨|在吗|谢谢|早安|晚安|吃了吗|你是谁|几点了)[\s\W]*$",
    re.I,
)

# 路由时去掉附件正文，避免长文档把所有请求推成 plan
_ATTACH_MARKERS = (
    "【附件文档】",
    "--- 文档:",
    "--- 文档：",
    "请结合下方附件",
)


def routing_text(text: str, *, max_chars: int = 280) -> str:
    """供路由器使用的短文本：剥附件、截断。"""
    t = (text or "").strip()
    if not t:
        return ""
    cut = len(t)
    for marker in _ATTACH_MARKERS:
        i = t.find(marker)
        if i >= 0:
            cut = min(cut, i)
    t = t[:cut].strip()
    # 去掉「用户:」等前缀噪声
    if len(t) > max_chars:
        t = t[:max_chars].rstrip() + "…"
    return t


def count_plan_signals(text: str) -> int:
    t = routing_text(text, max_chars=2000)
    if not t:
        return 0
    n = 0
    for pat in _PLAN_SIGNAL_PATTERNS:
        if pat.search(t):
            n += 1
    # 独立动作词 ≥4 才计为复杂信号（避免「打开并读取」误判）
    verbs = len(
        re.findall(
            r"(打开|关闭|创建|删除|修改|编辑|编译|构建|运行|执行|搜索|查找|写入|读取|安装|配置|重构|部署|提交)",
            t,
        )
    )
    if verbs >= 4:
        n += 1
    # 多条指令（分号 / 多行）
    if t.count("；") >= 2 or t.count("\n") >= 3:
        n += 1
    return n


_STRONG_PLAN_HINTS = re.compile(
    r"(分步|一步步|制定计划|按步骤|分阶段|做个计划|列个计划|plan-and-execute)",
    re.I,
)

# 跟进写入 / 禁止再审查：强制 ReAct
_FOLLOWUP_REACT = re.compile(
    r"(不要再|别再|勿再|禁止再|不要再次).{0,16}(审查|扫描|分析|检查|review)|"
    r"(把|将).{0,24}(之前|上次|刚才|历史|前面|以上).{0,24}"
    r"(结果|内容|记录|输出|审查|结论).{0,40}(写入|写到|存|保存|存成|落到)|"
    r"(写入|写到|存成|保存到|存到).{0,40}\.(md|txt|markdown|json)",
    re.I,
)


def heuristic_route(
    text: str,
    *,
    cfg: AgentRuntimeConfig | None = None,
) -> Literal["react", "plan", "ambiguous"]:
    """规则路由：默认 react；明确复杂才 plan；仅边界情况 ambiguous。"""
    acfg = cfg or load_agent_config()
    t = routing_text(text)
    if not t:
        return "react"
    if _SIMPLE_HINTS.match(t) or len(t) < 8:
        return "react"
    # 保存历史结果 / 禁止再审查 → ReAct（依赖对话上下文写文件）
    if _FOLLOWUP_REACT.search(t):
        return "react"
    # 用户明确要求分步/计划 → 直接 plan
    if _STRONG_PLAN_HINTS.search(t):
        return "plan"
    signals = count_plan_signals(t)
    # 无复杂信号 → 一律 ReAct（长文本/附件也不例外）
    if signals == 0:
        return "react"
    if signals >= acfg.router.min_plan_signals:
        return "plan"
    # 仅 1 个信号：长文更倾向 plan，短文交给 LLM/默认 react
    if len(t) >= acfg.router.long_text_chars:
        return "plan"
    return "ambiguous"


def _last_user_text(messages: Sequence[Any]) -> str:
    for msg in reversed(list(messages or [])):
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if isinstance(msg, dict):
            role = msg.get("role") or msg.get("type")
            content = msg.get("content") or ""
        else:
            content = getattr(msg, "content", "") or ""
        if role in ("human", "user"):
            if isinstance(content, list):
                parts = []
                for b in content:
                    if isinstance(b, str):
                        parts.append(b)
                    elif isinstance(b, dict) and b.get("type") == "text":
                        parts.append(str(b.get("text") or ""))
                return "\n".join(parts).strip()
            return str(content).strip()
        if isinstance(msg, HumanMessage):
            return str(content).strip()
    return ""


def _message_text(msg: Any) -> str:
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "\n".join(p for p in parts if p).strip()
    return str(content or "").strip()


def _final_ai_text(messages: Sequence[Any]) -> str:
    for msg in reversed(list(messages or [])):
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        name = msg.__class__.__name__ if not isinstance(msg, dict) else ""
        is_ai = role in ("ai", "assistant") or name in ("AIMessage", "AIMessageChunk")
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            is_ai = True
        if not is_ai:
            continue
        text = _message_text(msg)
        if text:
            return text
    return ""


def _format_dialog_context(
    messages: Sequence[Any] | None,
    *,
    max_msgs: int = 10,
    max_chars: int = 10000,
    per_msg: int = 3500,
) -> str:
    """给 planner/executor 的近期对话（含助手长文，便于「写入之前结果」）。"""
    rows: list[tuple[str, str]] = []
    for msg in list(messages or []):
        role = getattr(msg, "type", None) or getattr(msg, "role", None)
        if isinstance(msg, dict):
            role = msg.get("role") or msg.get("type")
        if role in ("human", "user"):
            tag = "用户"
        elif role in ("ai", "assistant") or (
            not isinstance(msg, dict)
            and msg.__class__.__name__ in ("AIMessage", "AIMessageChunk")
        ):
            tag = "助手"
        else:
            continue
        text = _message_text(msg)
        if not text:
            continue
        # 跳过纯「计划：」元消息的过短噪音可保留；截断长文
        if len(text) > per_msg:
            text = text[: per_msg - 1] + "…"
        rows.append((tag, text))
    if not rows:
        # 冷启动：从 UI transcript 补历史
        try:
            from agent.chat_history import recent_for_llm

            for m in recent_for_llm(limit=max_msgs, exclude_trailing_user=True, max_chars_per_msg=per_msg):
                tag = "用户" if m.get("role") == "user" else "助手"
                rows.append((tag, str(m.get("content") or "")))
        except Exception:
            pass
    rows = rows[-max_msgs:]
    parts: list[str] = []
    total = 0
    for tag, text in rows:
        chunk = f"【{tag}】\n{text}"
        if total + len(chunk) > max_chars:
            remain = max_chars - total
            if remain > 80:
                parts.append(chunk[: remain - 1] + "…")
            break
        parts.append(chunk)
        total += len(chunk) + 2
    return "\n\n".join(parts).strip()


def _thread_id(config: Optional[RunnableConfig]) -> str:
    if not config:
        return "default"
    if isinstance(config, dict):
        cfg = config.get("configurable") or {}
    else:
        cfg = getattr(config, "configurable", None) or {}
    if isinstance(cfg, dict):
        return str(cfg.get("thread_id") or "default")
    return "default"


def _clip_steps(steps: list[str], max_n: int) -> list[str]:
    out = [s.strip() for s in steps if (s or "").strip()]
    return out[: max(1, max_n)]


def build_plan_execute_graph(
    *,
    react_agent,
    step_agent,
    model,
    agent_cfg: AgentRuntimeConfig | None = None,
    llm_config: LLMConfig | None = None,
):
    """
    编译根图。

    react_agent: 完整 ReAct（主对话路径，无 checkpointer 亦可）
    step_agent: 嵌套执行器用 ReAct（建议去掉 run_workflow）
    model: ChatOpenAI，用于 planner / replanner / 可选 router 分类
    """
    acfg = agent_cfg or load_agent_config()
    _ = llm_config

    planner_llm = model
    try:
        structured_planner = planner_llm.with_structured_output(PlanSteps)
    except Exception:
        structured_planner = None
    try:
        structured_replanner = planner_llm.with_structured_output(ReplanDecision)
    except Exception:
        structured_replanner = None

    def router_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        mode = acfg.mode
        raw = state.get("input") or _last_user_text(state.get("messages") or [])
        text = routing_text(raw)
        # 新用户回合：清上一轮 plan / past_steps
        clear = {"plan": [], "past_steps": [], "response": ""}
        if mode == "react":
            return {"route": "react", "input": text or raw, **clear}
        if mode == "plan_execute":
            return {"route": "plan", "input": text or raw, **clear}

        decision = heuristic_route(text or raw, cfg=acfg)
        if decision == "ambiguous" and acfg.router.use_llm_when_ambiguous:
            try:
                resp = planner_llm.invoke(
                    [
                        SystemMessage(
                            content=(
                                "判断用户请求走 react 还是 plan。只回复一个单词：react 或 plan。\n"
                                "默认选 react（单次工具、查询、记事、闹钟、读文件、小改动、闲聊、"
                                "把之前结果写入文件、不要再审查）。\n"
                                "仅当明确需要 ≥3 个先后步骤、批量改多文件、或「先调研再改再测/编译」时选 plan。\n"
                                "不确定时选 react。"
                            )
                        ),
                        HumanMessage(content=(text or raw)[:800]),
                    ]
                )
                ans = _message_text(resp).lower().strip()
                # 严格匹配；含糊则 react
                token = ans.split()[0] if ans else ""
                token = token.strip(".,;:!\"'`。，；：")
                if token == "plan":
                    decision = "plan"
                else:
                    decision = "react"
            except Exception:
                decision = "react"
        if decision == "ambiguous":
            decision = "react"
        return {"route": decision, "input": text or raw, **clear}

    def route_after_router(state: PlanExecuteState) -> str:
        return "planner" if state.get("route") == "plan" else "react"

    def react_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        msgs = list(state.get("messages") or [])
        invoke_cfg: dict[str, Any] = {"recursion_limit": acfg.executor.recursion_limit}
        if config:
            if isinstance(config, dict):
                invoke_cfg = {**config, **invoke_cfg}
            else:
                invoke_cfg = {"configurable": getattr(config, "configurable", {}) or {}, **invoke_cfg}
        out = react_agent.invoke({"messages": msgs}, invoke_cfg)
        out_msgs = list(out.get("messages") or [])
        # 只回写新增消息，避免 add_messages 重复
        new_msgs = out_msgs[len(msgs) :] if len(out_msgs) > len(msgs) else out_msgs[-1:]
        return {"messages": new_msgs}

    def planner_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        goal = state.get("input") or _last_user_text(state.get("messages") or [])
        max_n = acfg.planner.max_plan_steps
        dialog = _format_dialog_context(state.get("messages"), max_msgs=10, max_chars=9000)
        sys = SystemMessage(
            content=(
                "你是任务规划器。把用户目标拆成有序、可执行的短步骤。"
                f"最多 {max_n} 步；每步只做一件事；用中文；不要写编号。"
                "不要执行工具，只输出计划。\n"
                "重要约束：\n"
                "- 必须遵守用户的禁止项（如「不要再审查/扫描」）。\n"
                "- 若用户要保存/写入「之前/上次」的结果，步骤应是："
                "从下方近期对话提取助手已有输出 → 用 write_file/edit_file 写入指定文档；"
                "禁止重新发起审查、扫描或 list_directory 全库遍历。\n"
                "- 不要把简单「写文件」扩成多步调研计划。"
            )
        )
        body = f"用户目标：\n{goal}"
        if dialog:
            body += f"\n\n【近期对话（可引用，勿重复已完成工作）】\n{dialog}"
        human = HumanMessage(content=body)
        steps: list[str] = []
        if structured_planner is not None:
            try:
                obj = structured_planner.invoke([sys, human])
                if isinstance(obj, PlanSteps):
                    steps = obj.steps
                elif isinstance(obj, dict):
                    steps = list(obj.get("steps") or [])
            except Exception:
                steps = []
        if not steps:
            try:
                raw = planner_llm.invoke(
                    [
                        sys,
                        HumanMessage(
                            content=(
                                f"{body}\n\n"
                                "请输出计划，每行一步，不要编号与其它说明。"
                            )
                        ),
                    ]
                )
                text = _message_text(raw)
                for line in text.splitlines():
                    line = line.strip()
                    line = re.sub(r"^[\d]+[\.\)、]\s*", "", line)
                    line = re.sub(r"^[-*•]\s*", "", line)
                    if line:
                        steps.append(line)
            except Exception:
                steps = [f"直接完成用户目标：{goal[:200]}"]
        steps = _clip_steps(steps, max_n) or [f"完成：{goal[:200]}"]
        return {
            "plan": steps,
            "past_steps": [],
            "response": "",
            "input": goal,
            "messages": [
                AIMessage(
                    content="计划：\n"
                    + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                )
            ],
        }

    def executor_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        plan = list(state.get("plan") or [])
        if not plan:
            return {}
        step = plan[0]
        goal = state.get("input") or ""
        past = list(state.get("past_steps") or [])
        sid = _thread_id(config)
        step_idx = len(past)
        dialog = _format_dialog_context(
            state.get("messages"), max_msgs=8, max_chars=12000, per_msg=5000
        )
        task = (
            f"请只完成下面这一步计划，完成后用中文简要汇报结果与关键发现。"
            f"不要擅自执行后续步骤。\n"
            f"若步骤是写入文档：优先使用近期对话里助手已给出的正文，"
            f"调用 write_file 或 edit_file；不要重新审查/扫描整个项目。\n\n"
            f"【当前步骤】{step}\n\n"
            f"【用户原始目标】{goal}\n"
        )
        if past:
            done = "\n".join(f"- {a}: {b[:300]}" for a, b in past[-4:])
            task += f"\n【已完成步骤摘要】\n{done}\n"
        if dialog:
            task += f"\n【近期对话（含历史审查/分析结果，可直接引用）】\n{dialog}\n"

        invoke_cfg: dict[str, Any] = {
            "configurable": {
                "thread_id": f"{sid}:pex-{step_idx}",
                "user_id": "pet",
            },
            "recursion_limit": acfg.executor.recursion_limit,
        }
        out = step_agent.invoke(
            {"messages": [HumanMessage(content=task)]},
            invoke_cfg,
        )
        result = _final_ai_text(out.get("messages") or []) or "（本步无文本结果）"
        new_past = past + [(step, result)]
        # 保留较完整步骤结果，便于后续「写入文档」跟进时引用
        return {
            "past_steps": new_past,
            "plan": plan[1:],
            "messages": [
                AIMessage(content=f"步骤完成：{step}\n结果：{result[:8000]}")
            ],
        }

    def replanner_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        goal = state.get("input") or ""
        past = list(state.get("past_steps") or [])
        remaining = list(state.get("plan") or [])
        past_txt = "\n".join(
            f"{i+1}. {s}\n   → {r[:500]}" for i, (s, r) in enumerate(past)
        )
        rem_txt = "\n".join(f"- {s}" for s in remaining) or "（无）"
        sys = SystemMessage(
            content=(
                "你是重规划器。根据已完成步骤与用户目标，决定："
                "1) done=true 并给出最终中文回答 response；或"
                "2) done=false 并给出仍需执行的 steps（可改写，勿重复已完成工作）。"
                f"剩余步骤总数不超过 {acfg.planner.max_plan_steps}。"
            )
        )
        human = HumanMessage(
            content=(
                f"用户目标：\n{goal}\n\n已完成：\n{past_txt or '（无）'}\n\n"
                f"原剩余计划：\n{rem_txt}\n"
            )
        )
        decision: ReplanDecision | None = None
        if structured_replanner is not None:
            try:
                obj = structured_replanner.invoke([sys, human])
                if isinstance(obj, ReplanDecision):
                    decision = obj
                elif isinstance(obj, dict):
                    decision = ReplanDecision(
                        done=bool(obj.get("done")),
                        response=str(obj.get("response") or ""),
                        steps=list(obj.get("steps") or []),
                    )
            except Exception:
                decision = None

        if decision is None:
            # 启发式：无剩余步骤则结束
            if not remaining:
                summary = past[-1][1] if past else "已完成。"
                return {
                    "response": summary,
                    "plan": [],
                    "messages": [AIMessage(content=summary)],
                }
            return {"plan": remaining}

        if decision.done or (not decision.steps and decision.response):
            resp = (decision.response or "").strip() or (
                past[-1][1] if past else "任务已完成。"
            )
            return {
                "response": resp,
                "plan": [],
                "messages": [AIMessage(content=resp)],
            }

        steps = _clip_steps(decision.steps, acfg.planner.max_plan_steps)
        if not steps:
            resp = (decision.response or "").strip() or (
                past[-1][1] if past else "任务已完成。"
            )
            return {
                "response": resp,
                "plan": [],
                "messages": [AIMessage(content=resp)],
            }
        return {
            "plan": steps,
            "messages": [
                AIMessage(
                    content="更新计划：\n"
                    + "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
                )
            ],
        }

    def should_continue(state: PlanExecuteState) -> str:
        if (state.get("response") or "").strip():
            return "end"
        if state.get("plan"):
            return "executor"
        # 无计划且无 response：用 past 收尾
        return "end"

    def respond_node(state: PlanExecuteState, config: Optional[RunnableConfig] = None):
        resp = (state.get("response") or "").strip()
        if resp:
            return {}
        past = list(state.get("past_steps") or [])
        if past:
            text = past[-1][1]
            return {
                "response": text,
                "messages": [AIMessage(content=text)],
            }
        return {
            "response": "未能完成计划。",
            "messages": [AIMessage(content="未能完成计划。")],
        }

    g = StateGraph(PlanExecuteState)
    g.add_node("router", router_node)
    g.add_node("react", react_node)
    g.add_node("planner", planner_node)
    g.add_node("executor", executor_node)
    g.add_node("replanner", replanner_node)
    g.add_node("respond", respond_node)

    g.add_edge(START, "router")
    g.add_conditional_edges(
        "router",
        route_after_router,
        {"react": "react", "planner": "planner"},
    )
    g.add_edge("react", END)
    g.add_edge("planner", "executor")
    g.add_edge("executor", "replanner")
    g.add_conditional_edges(
        "replanner",
        should_continue,
        {"executor": "executor", "end": "respond"},
    )
    g.add_edge("respond", END)

    return g
