"""在后台线程跑 PetAgent.ask，避免卡住桌宠 UI；支持流式、取消、重试。"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from agent.run_control import (
    PendingRun,
    RunCancelled,
    clear_cancel,
    clear_pending,
    get_pending,
    is_invalid_chat_history,
    is_network_error,
    request_cancel,
    set_pending,
)


class AgentWorker(QObject):
    finished = Signal(str)
    failed = Signal(str)
    cancelled = Signal(str)
    stream_event = Signal(object)

    def __init__(
        self,
        agent,
        user_text: str,
        memory_user_text: str | None = None,
        *,
        session_id: str = "",
        user_msg_id: str = "",
        user_content=None,
    ):
        super().__init__()
        self._agent = agent
        self._user_text = user_text
        self._memory_user_text = memory_user_text
        self._session_id = session_id
        self._user_msg_id = user_msg_id
        self._user_content = user_content

    def run(self):
        clear_cancel()
        try:
            def _on_event(ev):
                try:
                    self.stream_event.emit(ev)
                except Exception:
                    pass

            reply = self._agent.ask(
                self._user_text,
                memory_user_text=self._memory_user_text,
                on_event=_on_event,
                user_content=self._user_content,
            )
            clear_pending()
            self.finished.emit(reply or "（没有回复）")
        except RunCancelled as e:
            set_pending(
                PendingRun(
                    prompt=self._user_text,
                    user_text=self._memory_user_text or "",
                    user_msg_id=self._user_msg_id,
                    session_id=self._session_id,
                    reason="cancelled",
                    error=str(e),
                )
            )
            self.cancelled.emit(str(e) or "已停止本轮任务")
        except Exception as e:
            if is_invalid_chat_history(e):
                reason = "history"
            elif is_network_error(e):
                reason = "network"
            else:
                reason = "error"
            set_pending(
                PendingRun(
                    prompt=self._user_text,
                    user_text=self._memory_user_text or "",
                    user_msg_id=self._user_msg_id,
                    session_id=self._session_id,
                    reason=reason,
                    error=str(e),
                )
            )
            self.failed.emit(str(e))


class AgentRunner(QObject):
    """管理单次问答线程；同一时间只跑一个请求。"""

    reply_ready = Signal(str)
    error = Signal(str)
    cancelled = Signal(str)
    busy_changed = Signal(bool)
    stream_event = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._agent = None
        self._thread: QThread | None = None
        self._worker: AgentWorker | None = None
        self._busy = False
        self._last_prompt: str = ""
        self._last_memory: str | None = None
        self._last_user_msg_id: str = ""
        self._last_session_id: str = ""

    @property
    def busy(self) -> bool:
        return self._busy

    def _ensure_agent(self):
        if self._agent is None:
            from agent.pet_agent import PetAgent

            self._agent = PetAgent()

    def reset_agent(self) -> bool:
        """丢弃缓存的 Agent，下次 ask 重建。忙碌中拒绝。"""
        if self._busy:
            return False
        self._agent = None
        try:
            from agent.memory_manager import reset as _reset_memory_manager

            _reset_memory_manager()
        except Exception:
            pass
        return True

    def cancel(self) -> bool:
        """请求停止当前轮（协作式，尽快在流间隙生效）。"""
        if not self._busy:
            return False
        request_cancel()
        try:
            self.stream_event.emit({"kind": "status", "text": "正在停止…"})
        except Exception:
            pass
        return True

    def ask(
        self,
        user_text: str,
        *,
        memory_user_text: str | None = None,
        user_msg_id: str = "",
        session_id: str = "",
        reuse_pending: bool = False,
        user_content=None,
    ) -> bool:
        """启动后台问答。若已在忙则返回 False。

        user_content: 可选多模态 content（str 或 OpenAI parts 列表）；
        缺省时使用 user_text。
        """
        if self._busy:
            return False
        text = (user_text or "").strip()
        if not text and user_content is None:
            return False
        if not text and user_content is not None:
            text = "（多模态消息）"
        try:
            self._ensure_agent()
        except Exception as e:
            self.error.emit(str(e))
            return False

        if not session_id:
            try:
                from agent.chat_history import get_active_id

                session_id = get_active_id() or ""
            except Exception:
                session_id = ""

        self._last_prompt = text
        self._last_memory = memory_user_text
        self._last_user_msg_id = user_msg_id or ""
        self._last_session_id = session_id
        if not reuse_pending:
            clear_pending()

        self._busy = True
        self.busy_changed.emit(True)

        thread = QThread()
        worker = AgentWorker(
            self._agent,
            text,
            memory_user_text=memory_user_text,
            session_id=session_id,
            user_msg_id=user_msg_id,
            user_content=user_content,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stream_event.connect(self.stream_event.emit)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._clear_thread)
        self._thread = thread
        self._worker = worker
        thread.start()
        return True

    def retry_last(self) -> bool:
        """重试上一轮失败/中断的请求（不新增用户气泡，由调用方处理历史）。"""
        pending = get_pending()
        prompt = (pending.prompt if pending else "") or self._last_prompt
        mem = (pending.user_text if pending else None) or self._last_memory
        uid = (pending.user_msg_id if pending else "") or self._last_user_msg_id
        sid = (pending.session_id if pending else "") or self._last_session_id
        if not (prompt or "").strip():
            return False
        return self.ask(
            prompt,
            memory_user_text=mem,
            user_msg_id=uid,
            session_id=sid,
            reuse_pending=True,
        )

    def has_pending(self) -> bool:
        return get_pending() is not None or bool(self._last_prompt)

    def _clear_thread(self):
        self._thread = None
        self._worker = None

    def _on_finished(self, reply: str):
        self._busy = False
        self.busy_changed.emit(False)
        self.reply_ready.emit(reply)
        try:
            from agent.memory_manager import on_turn_completed

            on_turn_completed(self._last_memory or self._last_prompt, reply)
        except Exception:
            pass

    def _on_failed(self, err: str):
        self._busy = False
        self.busy_changed.emit(False)
        self.error.emit(err)

    def _on_cancelled(self, msg: str):
        self._busy = False
        self.busy_changed.emit(False)
        self.cancelled.emit(msg)
