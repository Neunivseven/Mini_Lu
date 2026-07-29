"""Agent 流式执行：思考状态 + token + 工具提示；支持取消与网络失败自动重试。"""
from __future__ import annotations

import re
from typing import Any, Callable

from agent.llm_client import LLMConfig
from agent.pet_agent import (
    _message_text,
    _thread_has_messages,
    build_agent,
    thread_config,
)
from agent.run_control import (
    RunCancelled,
    is_invalid_chat_history,
    is_network_error,
    raise_if_cancelled,
    sleep_backoff,
)

OnEvent = Callable[[dict[str, Any]], None]

_THINK_RE = re.compile(
    r"<think>([\s\S]*?)</think>|<thinking>([\s\S]*?)</thinking>",
    re.IGNORECASE,
)

# 网络类错误自动重试次数（不含首次）
MAX_NETWORK_RETRIES = 2


def _emit(on_event: OnEvent | None, kind: str, **kwargs: Any) -> None:
    if on_event is None:
        return
    try:
        on_event({"kind": kind, **kwargs})
    except Exception:
        pass


def _split_think_answer(text: str) -> tuple[str, str]:
    raw = text or ""
    thinks: list[str] = []

    def _sub(m: re.Match) -> str:
        thinks.append((m.group(1) or m.group(2) or "").strip())
        return ""

    answer = _THINK_RE.sub(_sub, raw).strip()
    return "\n".join(t for t in thinks if t), answer


def _build_payload(
    graph,
    cfg: dict[str, Any],
    *,
    user_text: str | None,
    messages: list[dict[str, str]] | None,
    user_content: Any = None,
) -> dict[str, Any]:
    if messages is not None:
        return {"messages": messages}
    content: Any = user_content if user_content is not None else (user_text or "")
    if not _thread_has_messages(graph, cfg):
        try:
            from agent.chat_history import recent_for_llm

            prior = recent_for_llm(limit=16, exclude_trailing_user=True)
        except Exception:
            prior = []
        return {
            "messages": [
                *prior,
                {"role": "user", "content": content},
            ]
        }
    return {"messages": [{"role": "user", "content": content}]}


def _ensure_sane(graph, session_id: str | None, on_event: OnEvent | None) -> None:
    try:
        from agent.lg_runtime import ensure_thread_sane

        n = ensure_thread_sane(session_id, agent=graph)
        if n and n > 0:
            _emit(on_event, "status", text="已修复中断的工具调用记录…")
        elif n == -1:
            _emit(on_event, "status", text="已重置损坏的对话短时记忆…")
    except Exception:
        pass


def _stream_once(
    graph,
    payload: dict[str, Any],
    cfg: dict[str, Any],
    on_event: OnEvent | None,
) -> str:
    _emit(on_event, "status", text="正在思考…")
    answer_acc = ""
    last_tool_hint = ""
    saw_chunk = False
    last_plan_fingerprint = ""

    try:
        stream_iter = graph.stream(payload, cfg, stream_mode=["messages", "updates"])
    except TypeError:
        # 旧版或不支持 list stream_mode
        stream_iter = (
            ("messages", item)
            for item in graph.stream(payload, cfg, stream_mode="messages")
        )

    for item in stream_iter:
        raise_if_cancelled()
        mode = "messages"
        data = item
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str):
            mode, data = item[0], item[1]

        if mode == "updates" and isinstance(data, dict):
            for node_name, update in data.items():
                if not isinstance(update, dict):
                    continue
                plan = update.get("plan")
                if isinstance(plan, list) and plan:
                    fp = "|".join(str(s) for s in plan)
                    if fp != last_plan_fingerprint:
                        last_plan_fingerprint = fp
                        _emit(on_event, "plan", steps=[str(s) for s in plan])
                        preview = " → ".join(str(s)[:40] for s in plan[:5])
                        _emit(on_event, "status", text=f"计划（{len(plan)} 步）：{preview}")
                past = update.get("past_steps")
                if isinstance(past, list) and past and node_name == "executor":
                    step_name = past[-1][0] if isinstance(past[-1], (tuple, list)) else ""
                    total_hint = ""
                    rem = update.get("plan")
                    if isinstance(rem, list):
                        total_hint = f"，剩余 {len(rem)} 步"
                    _emit(
                        on_event,
                        "status",
                        text=f"已完成步骤：{step_name}{total_hint}",
                    )
                route = update.get("route")
                if route == "plan" and node_name == "router":
                    _emit(on_event, "status", text="复杂任务：进入 Plan-and-Execute…")
                elif route == "react" and node_name == "router":
                    _emit(on_event, "status", text="简单任务：ReAct 直接处理…")
            continue

        # messages 模式
        msg = data[0] if isinstance(data, tuple) else data
        name = getattr(msg, "__class__", type("x", (), {})).__name__
        if isinstance(msg, dict):
            name = ""
        role = getattr(msg, "type", None) or getattr(msg, "role", None)

        tool_calls = getattr(msg, "tool_calls", None) or []
        if tool_calls:
            for tc in tool_calls:
                if isinstance(tc, dict):
                    tname = tc.get("name") or "tool"
                else:
                    tname = getattr(tc, "name", None) or "tool"
                hint = f"调用工具 {tname}…"
                if hint != last_tool_hint:
                    _emit(on_event, "tool", text=hint)
                    last_tool_hint = hint

        if role in ("tool",) or name in ("ToolMessage",):
            tname = getattr(msg, "name", None) or "tool"
            _emit(on_event, "tool", text=f"已完成 {tname}")
            continue

        is_ai = role in ("ai", "assistant") or name in (
            "AIMessage",
            "AIMessageChunk",
        )
        if not is_ai:
            continue

        piece = _message_text(msg)
        if not piece:
            continue

        if name == "AIMessage" and "Chunk" not in name:
            answer_acc = piece
            continue

        saw_chunk = True
        answer_acc += piece
        th, ans = _split_think_answer(piece)
        if th:
            _emit(on_event, "thinking", text=th[:240])
        visible = _THINK_RE.sub("", piece)
        visible = re.sub(r"</?think(?:ing)?>", "", visible, flags=re.I)
        if visible:
            _emit(on_event, "token", text=visible)

    raise_if_cancelled()
    final = answer_acc.strip()
    if final:
        th, ans = _split_think_answer(final)
        if th and not saw_chunk:
            _emit(on_event, "thinking", text=th[:800])
        if ans and not saw_chunk:
            chunk_size = 28
            for i in range(0, len(ans), chunk_size):
                raise_if_cancelled()
                _emit(on_event, "token", text=ans[i : i + chunk_size])
        return ans or final
    raise RuntimeError("stream_empty")


def _invoke_once(
    graph,
    payload: dict[str, Any],
    cfg: dict[str, Any],
    on_event: OnEvent | None,
) -> str:
    raise_if_cancelled()
    _emit(on_event, "status", text="整段生成中…")
    result = graph.invoke(payload, cfg)
    raise_if_cancelled()
    if isinstance(result, dict) and (result.get("response") or "").strip():
        text = str(result.get("response") or "").strip()
        th, ans = _split_think_answer(text)
        if th:
            _emit(on_event, "thinking", text=th[:800])
        out = ans or text
        chunk_size = 28
        for i in range(0, len(out), chunk_size):
            raise_if_cancelled()
            _emit(on_event, "token", text=out[i : i + chunk_size])
        return out
    out_messages = result.get("messages") or []
    if not out_messages:
        return "（Agent 无返回消息）"
    text = ""
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
            break
    if not text:
        text = _message_text(out_messages[-1]) or "（无文本回复）"

    th, ans = _split_think_answer(text)
    if th:
        _emit(on_event, "thinking", text=th[:800])
    out = ans or text
    chunk_size = 28
    for i in range(0, len(out), chunk_size):
        raise_if_cancelled()
        _emit(on_event, "token", text=out[i : i + chunk_size])
    return out


def run_agent_streaming(
    user_text: str | None = None,
    *,
    messages: list[dict[str, str]] | None = None,
    agent=None,
    config: LLMConfig | None = None,
    session_id: str | None = None,
    on_event: OnEvent | None = None,
    user_content: Any = None,
) -> str:
    """优先 messages 流式；失败则回退 invoke。网络错误自动重试；可取消。"""
    graph = agent or build_agent(config)
    cfg = thread_config(session_id)
    _ensure_sane(graph, session_id, on_event)
    # 修补/清空后可能改变「是否已有 checkpoint」
    payload = _build_payload(
        graph,
        cfg,
        user_text=user_text,
        messages=messages,
        user_content=user_content,
    )

    last_err: BaseException | None = None
    history_repaired = False
    for attempt in range(MAX_NETWORK_RETRIES + 1):
        raise_if_cancelled()
        if attempt > 0:
            _emit(
                on_event,
                "status",
                text=f"网络异常，正在重试（{attempt}/{MAX_NETWORK_RETRIES}）…",
            )
            sleep_backoff(attempt - 1)
            raise_if_cancelled()

        try:
            try:
                return _stream_once(graph, payload, cfg, on_event)
            except RunCancelled:
                raise
            except Exception as e:
                # 流式不可用时回退 invoke（空流也回退）
                if str(e) == "stream_empty" or not is_network_error(e):
                    if is_invalid_chat_history(e):
                        raise
                    _emit(on_event, "status", text="流式暂不可用，整段生成中…")
                    return _invoke_once(graph, payload, cfg, on_event)
                raise
        except RunCancelled:
            # 取消时也可能留下半截 tool_calls，立刻补齐以免下次炸
            try:
                from agent.lg_runtime import repair_dangling_tool_calls

                repair_dangling_tool_calls(session_id, agent=graph)
            except Exception:
                pass
            raise
        except Exception as e:
            last_err = e
            if is_invalid_chat_history(e) and not history_repaired:
                history_repaired = True
                _emit(on_event, "status", text="对话短时记忆不完整，正在修复并重试…")
                _ensure_sane(graph, session_id, on_event)
                payload = _build_payload(
                    graph,
                    cfg,
                    user_text=user_text,
                    messages=messages,
                    user_content=user_content,
                )
                continue
            if is_network_error(e) and attempt < MAX_NETWORK_RETRIES:
                continue
            # 非网络或重试用尽：再试一次 invoke（若上次是 stream 网络失败）
            if is_network_error(e):
                try:
                    raise_if_cancelled()
                    _emit(on_event, "status", text="仍在重试整段请求…")
                    return _invoke_once(graph, payload, cfg, on_event)
                except RunCancelled:
                    raise
                except Exception as e2:
                    last_err = e2
            break

    # 失败收尾：尽量修掉悬空 tool_calls，方便用户点「重试」
    try:
        from agent.lg_runtime import repair_dangling_tool_calls

        repair_dangling_tool_calls(session_id, agent=graph)
    except Exception:
        pass

    if isinstance(last_err, RunCancelled):
        raise last_err
    raise last_err or RuntimeError("Agent 执行失败")
