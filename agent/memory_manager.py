"""langmem 后台记忆：回合结束后自动提取/整合长期记忆。

设计约束：
- langmem 只在本模块内 import；未安装或未启用时全部静默跳过
- 单飞 + 最小间隔节流：同一时间最多一个提取任务，避免每回合都多烧一次 LLM
- 写入与 remember/recall_memories 相同的 Store 命名空间，记忆面板直接可见
- 提取模型复用当前 active.chat（切换模型后调用 reset() 重建）
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_busy = False
_last_ts = 0.0
_manager = None

_INSTRUCTIONS = (
    "从对话中提取值得跨会话长期记住的信息，每条用简体中文一句话表述：\n"
    "- 用户的稳定偏好（称呼、语言、口味、工作流习惯）\n"
    "- 用户的长期事实（职业、项目、环境、常用路径）\n"
    "- 用户明确要求记住的内容\n"
    "不要记录：一次性任务细节、代码片段、临时状态、寒暄闲聊。\n"
    "若新信息与已有记忆矛盾或使其过时，更新/删除旧记忆而不是重复新增。"
)


def is_available() -> bool:
    try:
        import langmem  # noqa: F401

        return True
    except Exception:
        return False


def reset() -> None:
    """模型/配置变更后丢弃缓存的 manager，下次触发重建。"""
    global _manager
    with _lock:
        _manager = None


def _get_manager():
    global _manager
    with _lock:
        if _manager is not None:
            return _manager
    from langmem import create_memory_store_manager

    from agent.lg_runtime import MEMORY_NAMESPACE, get_store
    from agent.pet_agent import build_chat_model

    mgr = create_memory_store_manager(
        build_chat_model(),
        namespace=MEMORY_NAMESPACE,
        instructions=_INSTRUCTIONS,
        enable_inserts=True,
        enable_deletes=True,
        query_limit=8,
        store=get_store(),
    )
    with _lock:
        _manager = mgr
    return mgr


def on_turn_completed(user_text: str, reply: str) -> None:
    """回合成功结束时调用（非阻塞）。按配置与节流决定是否后台提取。"""
    global _busy
    user_text = (user_text or "").strip()
    reply = (reply or "").strip()
    if not user_text or not reply:
        return
    try:
        from agent.agent_config import load_agent_config

        mc = load_agent_config().memory
    except Exception:
        return
    if not mc.auto_extract or not is_available():
        return
    with _lock:
        if _busy or (time.time() - _last_ts) < mc.min_interval_seconds:
            return
        _busy = True
    threading.Thread(
        target=_extract,
        args=(user_text[: mc.max_chars], reply[: mc.max_chars]),
        daemon=True,
        name="langmem-extract",
    ).start()


def _extract(user_text: str, reply: str) -> None:
    global _busy, _last_ts
    try:
        mgr = _get_manager()
        mgr.invoke(
            {
                "messages": [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                ]
            }
        )
        logger.info("langmem 记忆提取完成")
    except Exception as e:
        logger.warning("langmem 记忆提取失败: %s", e)
        reset()  # 失败可能因模型配置变化，下次重建
    finally:
        with _lock:
            _busy = False
            _last_ts = time.time()


def extract_now(user_text: str, reply: str) -> str:
    """同步提取（供调试/手动触发），返回结果描述。"""
    if not is_available():
        return "langmem 未安装"
    try:
        _extract(user_text, reply)
        return "提取完成（结果见记忆面板 / recall_memories）"
    except Exception as e:
        return f"提取失败: {e}"
