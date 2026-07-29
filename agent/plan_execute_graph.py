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


# ── State ──


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


# ── Router heuristics ──

_PLAN_SIGNAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(分步|一步步|制定计划|先.*再.*然后|多步|按步骤)"),
    re.compile(r"(并|然后|接着|之后).{0,12}(改|修|写|编译|测试|运行|部署|提交)"),
    re.compile(r"(扫描|搜索|查找).{0,20}(修改|编辑|改写|编译|构建)"),
    re.compile(r"(CMake|cmake|Makefile|package\.json).{0,30}(改|编|build|编译)"),
    re.compile(r"(工作区|项目).{0,20}(全部|所有|批量)"),
    re.compile(r"(实现|开发|重构).{0,16}(功能|模块|接口)"),
    re.compile(r"(并且|同时).{0,8}(打开|运行|执行|创建|删除)"),
]

_SIMPLE_HINTS = re.compile(
    r"^(你好|嗨|在吗|谢谢|早安|晚安|吃了吗|你是谁|几点了)[\s\W]*$",
    re.I,
)


def count_plan_signals(text: str) -> int:
    t = (text or "").strip()
    if not t:
        return 0
    n = 0
    for pat in _PLAN_SIGNAL_PATTERNS:
        if pat.search(t):
            n += 1
    # 多个祈使/动作词
    verbs = len(
        re.findall(
            r"(打开|关闭|创建|删除|修改|编辑|编译|构建|运行|执行|搜索|查找|写入|读取|安装|配置)",
            t,
        )
    )
    if verbs >= 3:
        n += 1
    if "；" in t or t.count("\n") >= 2:
        n += 1
    return n


def heuristic_route(
    text: str,
    *,
    cfg: AgentRuntimeConfig | None = None,
) -> Literal["react", "plan", "ambiguous"]:
    """规则路由：react / plan / ambiguous。"""
    acfg = cfg or load_agent_config()
    t = (text or "").strip()
    if not t:
        return "react"
    if _SIMPLE_HINTS.match(t) or len(t) < 8:
        return "react"
    signals = count_plan_signals(t)
    if signals >= acfg.router.min_plan_signals:
        return "plan"
    if len(t) >= acfg.router.long_text_chars and signals >= 1:
        return "plan"
    if signals == 0 and len(t) < acfg.router.long_text_chars:
        return "react"
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


# ── Graph factory ──


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
        text = state.get("input") or _last_user_text(state.get("messages") or [])
        if mode == "react":
            return {"route": "react", "input": text}
        if mode == "plan_execute":
            return {"route": "plan", "input": text}

        decision = heuristic_route(text, cfg=acfg)
        if decision == "ambiguous" and acfg.router.use_llm_when_ambiguous:
            try:
                resp = planner_llm.invoke(
                    [
                        SystemMessage(
                            content=(
                                "判断用户请求应走简单对话(react)还是多步计划执行(plan)。"
                                "仅回复单词 react 或 plan。"
                                "闲聊、单点查询、单次工具 → react；"
                                "需多步骤、改代码+编译、调研后修改 → plan。"
                            )
                        ),
                        HumanMessage(content=text[:2000]),
                    ]
                )
                ans = _message_text(resp).lower().strip()
                if ans in ("plan", "react"):
                    decision = ans  # type: ignore[assignment]
                elif "plan" in ans and "react" not in ans:
                    decision = "plan"
                elif ans.startswith("plan") or " plan" in f" {ans}":
                    decision = "plan"
                else:
                    decision = "react"
            except Exception:
                decision = "react"
        if decision == "ambiguous":
            decision = "react"
        return {"route": decision, "input": text}

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
        sys = SystemMessage(
            content=(
                "你是任务规划器。把用户目标拆成有序、可执行的短步骤。"
                f"最多 {max_n} 步；每步只做一件事；用中文；不要写编号。"
                "不要执行工具，只输出计划。"
            )
        )
        human = HumanMessage(content=f"用户目标：\n{goal}")
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
                                f"用户目标：\n{goal}\n\n"
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
        task = (
            f"请只完成下面这一步计划，完成后用中文简要汇报结果与关键发现。"
            f"不要擅自执行后续步骤。\n\n"
            f"【当前步骤】{step}\n\n"
            f"【用户原始目标】{goal}\n"
        )
        if past:
            done = "\n".join(f"- {a}: {b[:300]}" for a, b in past[-4:])
            task += f"\n【已完成步骤摘要】\n{done}\n"

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
        return {
            "past_steps": new_past,
            "plan": plan[1:],
            "messages": [
                AIMessage(content=f"步骤完成：{step}\n结果：{result[:1200]}")
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
