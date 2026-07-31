"""
基于 LangGraph 的 Mini_Lu Agent。

- 根图：自动路由 ReAct / Plan-and-Execute（见 plan_execute_graph）
- 短时记忆：SqliteSaver checkpointer（thread_id = 聊天会话 id）
- 长时记忆：SqliteStore（跨会话；经工具 / prompt 注入）
"""
from __future__ import annotations

from typing import Any, Optional

from langchain_core.messages import SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from agent.agent_config import load_agent_config
from agent.llm_client import LLMConfig, load_llm_config
from agent.lg_runtime import (
    format_store_block,
    get_checkpointer,
    get_store,
    thread_config,
)
from agent.tool_registry import get_tool_registry

DEFAULT_AGENT_SYSTEM = (
    "你是 Mini_Lu（用户可改名；实际名字见下方【身份】），桌面 Agent 助手。"
    "回答简洁、用中文；不要自称「桌宠」。"
    "涉及代码时用 Markdown 代码围栏包裹，例如：\n```python\n# code\n```\n"
    "便于工作台把正文与代码分框显示。\n"
    "需要执行终端命令时用 run_command；系统会请用户确认，已信任的命令可自动跑。\n"
    "对话短时上下文由 LangGraph Checkpointer 按会话自动保留；"
    "跨会话事实用 remember / recall_memories（LangGraph Store）。\n"
    "可用 ＋ / new_chat_session 开新对话；list_chat_sessions / switch_chat_session 切换。\n"
    "记事与闹钟是两套能力，不要混用：\n"
    "1) 纯记事 append_note：备忘/想法/资料，不设时间、不响铃。用户只说「记一下」且未要求提醒时用这个。\n"
    "2) 闹钟 add_alarm（或 add_reminder）：只有用户明确要「提醒我/到点叫我/闹钟/每天…」时才用。\n"
    "   - 一次性 once：会议、截止日期、N分钟后 → alarm_mode=once。\n"
    "   - 长期重复 repeat：每天吃药、工作日站会、每周例会 → alarm_mode=repeat，"
    "repeat=daily|weekly|weekdays|monthly。\n"
    "根据用户措辞判断 once vs repeat；不确定时问一句，或偏 once。\n"
    "文档：PDF 用 parse_document / read_document（PyMuPDF 抽字，可 pages=\"1-5\"）；"
    "扫描件无文字层时可 describe_image。简单 txt/docx/xlsx 用 read_document。"
    "拖入附件或给路径后：先 inspect_document / parse_document / read_document；"
    "改内容用 edit_word / edit_excel / edit_pdf；"
    "复杂排版用 run_document_code（输出到 OUT_DIR=data/docs_out）。"
    "默认写副本不覆盖原文件；inplace 需谨慎。不要编造未读到的文件内容。\n"
    "代码/文本（省 token · 结构优先 TSA）：\n"
    "0) 全局：接手新任务或不熟悉项目时先 repo_map 看一页项目地图，再精确定位；"
    "禁止用 list_directory + 整文件 read_file 当「读结构」的默认路径。\n"
    "1) 定位：glob_files / grep_files / read_outline / list_symbols。"
    "禁止一上来整文件 Read。\n"
    "2) 跨文件关系：Python 查引用首选 find_references（jedi 精确）；"
    "其它语言用 TSA：先 codegraph_status；索引空/过期则 index_codebase(mode=incremental，可限 max_files)；"
    "再 find_callers / find_callees（近似）。不要默认 full 全库重索。\n"
    "   MetaCoding 旁路（可选，需本机 Bun+MetaCoding）：metacoding_doctor 探测；"
    "TS/Python 大仓可用 metacoding_index → metacoding_search / metacoding_callers / "
    "metacoding_implementers / metacoding_neighbors。C/C++ 不要默认走 MetaCoding。\n"
    "3) 阅读：优先 read_symbol(name=函数或类名)；"
    "或 read_file(focus=符号名) / offset/limit；默认每次约 80 行，上限 200 行。\n"
    "4) 修改：改整个函数/类用 replace_symbol（直接写新定义，勿复述旧代码）；"
    "新增函数/方法用 insert_code(anchor=相邻符号)；零散小改用 edit_file 精确替换（删除则 new_string 为空）；"
    "Python 改名用 rename_symbol（跨文件一次完成）。禁止为小改动 write_file。\n"
    "   写入后工具会附带语法检查结果；若提示「发现问题」必须立即修复再继续。\n"
    "   审核开启时会立即写入新内容，并在目标旁缓存旧版；请在大窗口对每一连续改动段「保留/放弃」。\n"
    "5) write_file 仅新建，或覆盖≤约40行的短文件；更大已有文件会被拒绝。\n"
    "6) 工作区：用户点「📁」或右键「工作区…」打开文件夹；也可用 list_workspaces / "
    "open_workspace_picker / set_workspace。相对路径相对「当前项目」。\n"
    "不要编造未读内容；改完只复核相关片段。\n"
    "打开软件：用户说「打开QQ / 启动微信」等时直接调用 open_app(name)，不要先 refresh_app_index / list_apps。\n"
    "系统会扫描本机软件索引并按名称/文件夹相关性匹配，不要假设固定盘符路径。\n"
    "仅当 open_app 明确找不到时，再 refresh_app_index 后重试，或 list_apps 查看候选。\n"
    "打开终端：用户只要窗口时用 open_terminal；需要执行命令并看结果时用 run_command"
    "（编译/测试/git/脚本）。默认 cwd=当前工作区；失败时读 stderr 再改再跑。"
    "不要用 run_command 做整盘删除或关机等破坏操作。\n"
    "记忆：用户要「记住」用 remember；查看 recall_memories；删除 forget_memory(key)；"
    "清空 clear_memories。\n"
    "Goal：用户要跨多轮持续完成某目标时用 set_goal；查看 get_goal；"
    "完成 mark_goal_done；受阻 report_goal_blocked；暂停/恢复 pause_goal / resume_goal；清除 clear_goal。\n"
    "工作流：多阶段确定性任务用 run_workflow（JSON steps 或 阶段|指令 多行）；"
    "查看 list_workflows。适合「先检视再编辑再总结」等固定流水线。\n"
    "查看：list_notes / open_notes_viewer；详情 get_note；删除 delete_note；只关闹钟不删正文 cancel_reminder。\n"
    "多模型：list_model_providers 查看；set_chat_provider 切换；"
    "register_openai_endpoint 接入任意 OpenAI 兼容 API；"
    "外部 MCP：list_mcp / reload_mcp（config/mcp.yaml）；"
    "Skills：list_skills / load_skill（skills/*/SKILL.md）；"
    "语音转写 transcribe_audio；识图 describe_image；"
    "图像处理 process_image（多数环境未启用，按提示配置 models.yaml）。\n"
    "跟进：用户说「把之前结果写入文档 / 不要再审查」时，从对话历史取助手已有正文，"
    "用 write_file 或 edit_file 落盘；禁止重新扫描项目或再开审查计划。\n"
    "相对时间→delay_seconds；绝对时间→remind_at。不要编造工具结果。"
)


def build_chat_model(config: LLMConfig | None = None) -> ChatOpenAI:
    """LangGraph / LangChain 适配器：把 active.chat 配置转成 ChatOpenAI。

    无图、非 LangChain 场景请用 ``agent.ports.llm.get_chat_model_adapter()``
    或 ``agent.providers.get_hub().chat``，勿把本函数当成唯一 LLM 入口。
    """
    cfg = config or load_llm_config()
    # 优先用 Hub 当前 chat 的连接参数（支持豆包等非 llm 扁平配置）
    try:
        from agent.providers.hub import get_hub

        hub = get_hub()
        chat = hub.chat
        if hasattr(chat, "langchain_kwargs"):
            kw = dict(chat.langchain_kwargs())
            # 温度/采样由 provider 决定；Kimi K3 等为 None（省略，勿 setdefault 成 0.3）
            return ChatOpenAI(**kw)
    except Exception:
        pass
    if not cfg.api_key:
        raise RuntimeError(
            "未配置语言模型 API Key。请填写 config/models.local.yaml / "
            "llm.local.yaml，或设置对应环境变量（如 DEEPSEEK_API_KEY）。"
        )
    from agent.providers.sampling import resolve_chat_temperature, resolve_reasoning_effort

    temp = resolve_chat_temperature(cfg.model)
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "api_key": cfg.api_key,
        "base_url": cfg.base_url,
        "timeout": cfg.timeout_seconds,
        "temperature": temp,
    }
    effort = resolve_reasoning_effort(cfg.model)
    if effort:
        kwargs["reasoning_effort"] = effort
    return ChatOpenAI(**kwargs)


def _thread_id_from_config(config: Optional[RunnableConfig]) -> str | None:
    if config is None:
        return None
    if isinstance(config, dict):
        cfg = config.get("configurable") or {}
        if isinstance(cfg, dict):
            return cfg.get("thread_id")
        return None
    cfg = getattr(config, "configurable", None)
    if isinstance(cfg, dict):
        return cfg.get("thread_id")
    return None


def _make_prompt():
    """每轮从 prompt_store 解析 system（含 A/B），再注入 Goal + Store。"""

    def prompt(state: dict[str, Any], config: Optional[RunnableConfig] = None):
        sid = _thread_id_from_config(config)
        try:
            from agent.prompt_store import resolve_system_prompt

            base = resolve_system_prompt(session_id=sid)
        except Exception:
            base = DEFAULT_AGENT_SYSTEM
        parts = [base.strip()]
        try:
            from agent.identity import format_identity_block

            parts.insert(0, format_identity_block())
        except Exception:
            pass
        try:
            from agent.plugin import get_plugin_manager

            skills_block = get_plugin_manager().collect_system_prompt()
            if skills_block:
                parts.append(skills_block)
        except Exception:
            pass
        try:
            from agent.goal_store import format_goal_block

            goal = format_goal_block()
            if goal:
                parts.append(goal)
        except Exception:
            pass
        try:
            mem = format_store_block(limit=20)
            if mem:
                parts.append(mem)
        except Exception:
            pass
        # 便于排查当前分流版本
        try:
            from agent.prompt_store import resolve_version_id

            vid = resolve_version_id(sid)
            parts.append(f"[prompt_version={vid}]")
        except Exception:
            pass
        sys_msg = SystemMessage(content="\n\n".join(parts))
        messages = list(state.get("messages") or [])
        return [sys_msg, *messages]

    return prompt


def _collect_tools(*, tools: list | None = None, exclude: set[str] | None = None) -> list:
    """统一经 ToolRegistry 收集；传入 tools 时视为完整覆盖列表。"""
    if tools is not None:
        tool_list = list(tools)
        if exclude:
            tool_list = [t for t in tool_list if getattr(t, "name", "") not in exclude]
        return tool_list
    return get_tool_registry().all_tools(exclude=exclude)


def _resolve_prompt_fn(system_prompt: str | None = None):
    if system_prompt:
        fixed = system_prompt.strip()

        def _fixed_prompt(state: dict[str, Any], config: Optional[RunnableConfig] = None):
            parts = [fixed]
            try:
                from agent.goal_store import format_goal_block

                goal = format_goal_block()
                if goal:
                    parts.append(goal)
            except Exception:
                pass
            try:
                mem = format_store_block(limit=20)
                if mem:
                    parts.append(mem)
            except Exception:
                pass
            return [SystemMessage(content="\n\n".join(parts)), *list(state.get("messages") or [])]

        return _fixed_prompt

    try:
        from agent import prompt_store

        prompt_store.resolve_system_prompt()
    except Exception:
        pass
    return _make_prompt()


def build_react_agent(
    config: LLMConfig | None = None,
    *,
    tools: list | None = None,
    system_prompt: str | None = None,
    checkpointer=None,
    store=None,
    name: str = "Mini_Lu",
    exclude_tools: set[str] | None = None,
):
    """创建官方 create_react_agent（可关 checkpointer，供嵌套执行器用）。"""
    cfg = config or load_llm_config()
    model = build_chat_model(cfg)
    tool_list = _collect_tools(tools=tools, exclude=exclude_tools)
    prompt_fn = _resolve_prompt_fn(system_prompt)
    kwargs: dict[str, Any] = {
        "model": model,
        "tools": tool_list,
        "prompt": prompt_fn,
        "name": name,
    }
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    if store is not None:
        kwargs["store"] = store
    return create_react_agent(**kwargs)


def build_agent(
    config: LLMConfig | None = None,
    *,
    tools: list | None = None,
    system_prompt: str | None = None,
):
    """创建根图：Router → ReAct 或 Plan-and-Execute（含 checkpointer + store）。"""
    cfg = config or load_llm_config()
    agent_cfg = load_agent_config()

    get_checkpointer()
    get_store()

    # 主对话 ReAct：无独立 checkpointer，由根图 checkpoint 管 messages
    react = build_react_agent(
        cfg,
        tools=tools,
        system_prompt=system_prompt,
        store=get_store(),
        name="Mini_Lu_react",
    )
    # 单步执行：去掉 run_workflow，避免嵌套爆炸
    step = build_react_agent(
        cfg,
        tools=tools,
        system_prompt=system_prompt,
        store=get_store(),
        name="Mini_Lu_step",
        exclude_tools={"run_workflow"},
    )
    model = build_chat_model(cfg)

    from agent.plan_execute_graph import build_plan_execute_graph

    builder = build_plan_execute_graph(
        react_agent=react,
        step_agent=step,
        model=model,
        agent_cfg=agent_cfg,
        llm_config=cfg,
    )
    return builder.compile(
        checkpointer=get_checkpointer(),
        store=get_store(),
    )


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
            else:
                text = getattr(block, "text", None)
                if text:
                    parts.append(str(text))
        return "\n".join(p for p in parts if p).strip()
    return str(content or "").strip()


def _thread_has_messages(agent, config: dict[str, Any]) -> bool:
    try:
        snap = agent.get_state(config)
        values = getattr(snap, "values", None) or {}
        msgs = values.get("messages") if isinstance(values, dict) else None
        return bool(msgs)
    except Exception:
        return False


def run_agent(
    user_text: str | None = None,
    *,
    messages: list[dict[str, str]] | None = None,
    agent=None,
    config: LLMConfig | None = None,
    session_id: str | None = None,
) -> str:
    """同步执行一轮 Agent；短时上下文靠 checkpointer，无需手传历史。"""
    graph = agent or build_agent(config)
    cfg = thread_config(session_id)

    if messages is not None:
        payload = {"messages": messages}
    else:
        # 若该 thread 尚无 checkpoint，用 UI transcript 冷启动一次
        if not _thread_has_messages(graph, cfg):
            try:
                from agent.chat_history import recent_for_llm

                prior = recent_for_llm(limit=16, exclude_trailing_user=True)
            except Exception:
                prior = []
            payload = {
                "messages": [
                    *prior,
                    {"role": "user", "content": user_text or ""},
                ]
            }
        else:
            payload = {"messages": [{"role": "user", "content": user_text or ""}]}

    result = graph.invoke(payload, cfg)
    if isinstance(result, dict) and (result.get("response") or "").strip():
        return str(result.get("response") or "").strip()
    out_messages = result.get("messages") or []
    if not out_messages:
        return "（Agent 无返回消息）"
    for msg in reversed(out_messages):
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
    return _message_text(out_messages[-1]) or "（无文本回复）"


class PetAgent:
    """LangGraph 根图 Agent（自动路由 ReAct / Plan-Execute）；记忆由 Checkpointer + Store 承担。"""

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or load_llm_config()
        self._agent = build_agent(self.config)

    def rebuild(self) -> None:
        """配置或 prompt 版本变更后重建图。"""
        self.config = load_llm_config()
        self._agent = build_agent(self.config)

    def ask(self, user_text: str, *, memory_user_text: str | None = None, on_event=None, user_content=None) -> str:
        """
        user_text: 发给模型的完整文本（可含附件抽取）
        user_content: 可选多模态 content（str 或 parts 列表）；缺省用 user_text
        memory_user_text: 用户原始短句；优先用作 Plan/ReAct 路由依据
        on_event: 可选回调 dict(kind=thinking|token|tool|status|...)
        """
        from agent.agent_stream import run_agent_streaming

        reply = run_agent_streaming(
            user_text=user_text,
            user_content=user_content,
            agent=self._agent,
            config=self.config,
            on_event=on_event,
            route_input=memory_user_text or None,
        )

        try:
            from agent.goal_store import is_active, record_turn

            if is_active():
                record_turn()
        except Exception:
            pass

        return reply
