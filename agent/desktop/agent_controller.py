"""Agent 事件监听与分发。"""
from __future__ import annotations

from PySide6.QtCore import QObject

from agent import chat_history
from agent.chat_bubble import display_ms_for_text
from agent.desktop.constants import WINDOW_HEIGHT, WINDOW_WIDTH
from agent.event_bus import (
    AGENT_BUSY,
    AGENT_CANCELLED,
    AGENT_ERROR,
    AGENT_REPLY,
    AGENT_STREAM,
    get_event_bus,
)
from agent.plugin import get_plugin_manager


class AgentController(QObject):
    """on_chat_send → AgentRunner；stream/reply/error + EventBus 广播。"""

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self._cmd_approval_dlg = None

    @staticmethod
    def _is_panel_open_command(text: str, keywords: tuple[str, ...], *, max_len: int = 28) -> bool:
        """仅短句、且以关键词开头（可带礼貌前缀）才弹面板，避免聊天正文误触发。"""
        t = (text or "").strip()
        if not t or len(t) > max_len:
            return False
        # 去掉常见口头前缀后再匹配开头
        for pref in ("请帮我", "请你", "麻烦", "帮我", "请", "我想", "我要"):
            if t.startswith(pref):
                rest = t[len(pref) :].lstrip(" ，,：:")
                if rest:
                    t = rest
                break
        for k in keywords:
            if t == k or t.startswith(k):
                return True
        return False


    def on_chat_send(self, text: str, attachments: list | None = None):
        """用户发送：文档走正文提取；按 Chat 能力原生传图或 vision→文本降级。"""
        from pathlib import Path

        from agent.providers.media_gateway import resolve_turn

        # 「从此重开」：只有发送时才截断重跑；未发送则原对话不变
        rewind_id = self.host._rewind_anchor_id
        if rewind_id:
            self._commit_rewind_send(text, attachments, rewind_id)
            return

        attachments = list(attachments or [])
        # 兼容：纯路径字符串列表
        if attachments and isinstance(attachments[0], str):
            attachments = [
                {"kind": "doc", "path": p, "name": Path(p).name, "analysis": ""}
                for p in attachments
            ]

        docs = [a["path"] for a in attachments if a.get("kind") == "doc" and a.get("path")]
        media = [
            a
            for a in attachments
            if a.get("kind") in ("audio", "image")
        ]

        self.host.walk_target = None
        self.host.user_goto = False
        self.host.bubble_lane.set_pet_geo(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        bubble_bits = []
        if text.strip():
            bubble_bits.append(text.strip())
        labels = []
        for a in attachments:
            name = a.get("name") or Path(str(a.get("path") or "")).name
            kind = a.get("kind")
            prefix = {"image": "🖼", "audio": "🎤", "doc": "📄"}.get(kind, "📎")
            labels.append(f"{prefix}{name}")
        if labels:
            bubble_bits.append(" ".join(labels))
        user_bubble = "\n".join(bubble_bits) if bubble_bits else "（附件）"
        # 纯切换指令：不写入当前会话、不走 Agent
        tstrip = text.strip()
        if tstrip in ("新对话", "新建对话", "New Agent", "换个话题") or tstrip.lower() == "new agent":
            self.host.create_new_agent()
            return
        if any(k in tstrip for k in ("新对话", "新建对话", "New Agent", "换个话题")) and not attachments and len(tstrip) < 12:
            self.host.create_new_agent()
            return

        if not (self.host._studio_open and self.host.agent_studio.isVisible()):
            self.host.bubble_lane.push(user_bubble, role="user", ms=5000)
        user_msg_id = ""
        try:
            item = chat_history.add_message(
                "user", user_bubble, meta={"prompt": ""}  # prompt 稍后补
            )
            user_msg_id = str((item or {}).get("id") or "")
        except Exception:
            pass
        if self.host._studio_open and self.host.agent_studio.isVisible():
            try:
                self.host.agent_studio.reload()
            except Exception:
                pass
        # 短指令才弹面板；长句聊天（如「…不用打开记事本」）不触发
        if self._is_panel_open_command(
            tstrip, ("查看记事", "记事内容", "打开记事", "记事本")
        ):
            self.host.open_notes_panel()
        if self._is_panel_open_command(
            tstrip, ("查看记忆", "打开记忆", "记忆面板")
        ):
            self.host.open_memory_panel()
        if self._is_panel_open_command(
            tstrip, ("聊天记录", "查看对话", "历史对话", "对话记录", "切换对话", "对话列表")
        ):
            self.host.open_history_panel()
        if self._is_panel_open_command(
            tstrip, ("工作区", "打开文件夹", "切换项目", "代码目录")
        ):
            self.host.open_workspace_panel()

        try:
            turn = resolve_turn(text, doc_paths=docs, media_items=media)
            prompt = turn.text_prompt
            user_content = turn.user_content
        except Exception as e:
            self.host.bubble_lane.push(f"附件处理失败：{e}", role="assistant", ms=9000)
            return

        if user_msg_id:
            try:
                chat_history.update_message_meta(
                    user_msg_id,
                    {"prompt": prompt, "media_mode": turn.mode},
                )
            except Exception:
                pass

        mem_text = text.strip() if text.strip() else (
            "（附件：" + "、".join(labels) + "）" if labels else ""
        )

        self.host.chat_panel.set_busy(True)
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()
        if studio_vis:
            try:
                self.host.agent_studio.begin_stream()
            except Exception:
                pass
        else:
            self.host.bubble_lane.show_thinking()
        if not self.host.agent_runner.ask(
            prompt,
            memory_user_text=mem_text or None,
            user_msg_id=user_msg_id,
            user_content=user_content,
        ):
            self.host.chat_panel.set_busy(False)
            self.host.bubble_lane.clear_thinking()
            if not (self.host._studio_open and self.host.agent_studio.isVisible()):
                self.host.bubble_lane.push(
                    "暂时发不出去，可能上一条还在答，或 API Key 未配置。",
                    role="assistant",
                    ms=8000,
                )


    def on_agent_ui_event(self, ev: object):
        """工具线程发出的终端审批等（也可走 stream_event）。"""
        if isinstance(ev, dict):
            self.on_agent_stream_event(ev)


    def on_agent_stream_event(self, ev: object):
        data = ev if isinstance(ev, dict) else {}
        try:
            get_event_bus().emit(AGENT_STREAM, data)
            get_plugin_manager().dispatch_agent_event(AGENT_STREAM, data)
        except Exception:
            pass
        kind = str(data.get("kind") or "")
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()

        # 工作台未打开时：终端审批用独立对话框
        if kind == "command_approval" and not studio_vis:
            self._prompt_command_approval_dialog(data)
            return

        if studio_vis:
            try:
                # 终端审批若工作室打开工作台更直观
                if kind in ("command_approval", "command_auto", "command_result"):
                    if not studio_vis:
                        pass
                self.host.agent_studio.handle_stream_event(data)
            except Exception:
                pass
            return
        # 气泡模式：思考/状态
        if kind in ("thinking", "status", "tool", "plan"):
            tip = str(data.get("text") or "").strip()
            if kind == "plan" and not tip:
                steps = data.get("steps") or []
                tip = f"计划 {len(steps)} 步" if steps else "已生成计划"
            try:
                self.host.bubble_lane.show_thinking(tip[:80] if tip else None)
            except Exception:
                pass


    def _prompt_command_approval_dialog(self, data: dict):
        """非工作台时弹出统一确认框（非阻塞，避免卡住主线程导致其它确认 UI 消失）。"""
        from agent.command_approval import resolve_command_approval
        from agent.ui_dialogs import show_choice

        # 若此刻工作台已打开，改走聊天内嵌审批
        if self.host._studio_open and self.host.agent_studio.isVisible():
            try:
                self.host.agent_studio.handle_stream_event(
                    {**(data if isinstance(data, dict) else {}), "kind": "command_approval"}
                )
            except Exception:
                pass
            return

        rid = str(data.get("request_id") or "")
        cmd = str(data.get("command") or "")
        cwd = str(data.get("cwd") or "")
        detail_lines = []
        if cwd:
            detail_lines.append(f"# cwd: {cwd}")
        detail_lines.append(f"$ {cmd}")

        old = getattr(self, "_cmd_approval_dlg", None)
        if old is not None:
            try:
                # 替换对话框时不要触发「拒绝」回调，避免误否认仍在等待的请求
                old.rejected.disconnect()
            except Exception:
                pass
            try:
                old.close()
            except Exception:
                pass
            self._cmd_approval_dlg = None

        def _on_pick(action: str):
            self._cmd_approval_dlg = None
            try:
                resolve_command_approval(rid, action)
            except Exception:
                pass

        self._cmd_approval_dlg = show_choice(
            self,
            "确认执行终端命令",
            "Agent 想运行下面的命令。可运行、总是允许（加入信任）或取消。",
            detail="\n".join(detail_lines),
            choices=[
                ("deny", "取消"),
                ("always", "总是允许"),
                ("allow", "运行"),
            ],
            on_pick=_on_pick,
        )


    def on_agent_busy(self, busy: bool):
        try:
            get_event_bus().emit(AGENT_BUSY, busy)
        except Exception:
            pass
        self.host.chat_panel.set_busy(busy)
        if self.host._studio_open:
            self.host.agent_studio.set_busy(busy)
        if busy and not (self.host._studio_open and self.host.agent_studio.isVisible()):
            self.host.bubble_lane.show_thinking()
        # 空闲时由 reply/error 清 thinking


    def on_agent_reply(self, reply: str):
        try:
            get_event_bus().emit(AGENT_REPLY, reply)
            get_plugin_manager().dispatch_agent_event(AGENT_REPLY, reply)
        except Exception:
            pass
        self.host.bubble_lane.set_pet_geo(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        text = reply or "（没有回复）"
        meta = None
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.host.agent_studio.finalize_stream()
                if isinstance(snap, dict):
                    meta = {
                        "process": snap.get("process") or [],
                        "terminals": snap.get("terminals") or [],
                    }
                    # 流式累积正文若更完整可用它（否则用 runner 最终回复）
                    streamed = str(snap.get("text") or "").strip()
                    if streamed and len(streamed) >= max(20, len(text) // 2):
                        text = streamed
            except Exception:
                pass
        try:
            chat_history.add_message("assistant", text, meta=meta)
        except Exception:
            pass
        if studio_vis:
            self.host.agent_studio.append_assistant(text)
            self.host.agent_studio.set_busy(False)
        else:
            self.host.bubble_lane.push(
                text,
                role="assistant",
                ms=display_ms_for_text(text, base=14000),
            )
        self.host.change_state("happy")
        self.host.happy_timer.start(max(self.host.happy_duration, 1600))


    def on_agent_stop(self):
        """UI 请求停止本轮。"""
        if not self.host.agent_runner.cancel():
            return
        try:
            self.host.bubble_lane.show_thinking("正在停止…")
        except Exception:
            pass


    def on_agent_cancelled(self, msg: str):
        try:
            get_event_bus().emit(AGENT_CANCELLED, msg)
            get_plugin_manager().dispatch_agent_event(AGENT_CANCELLED, msg)
        except Exception:
            pass
        self.host.bubble_lane.clear_thinking()
        text = f"已停止：{msg or '本轮任务已取消'}"
        meta = {
            "status": "cancelled",
            "retryable": True,
            "error": msg or "",
        }
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.host.agent_studio.finalize_stream()
                if isinstance(snap, dict):
                    meta["process"] = snap.get("process") or []
                    meta["terminals"] = snap.get("terminals") or []
            except Exception:
                pass
        try:
            chat_history.add_message("assistant", text, meta=meta)
        except Exception:
            pass
        if studio_vis:
            try:
                self.host.agent_studio.reload()
                self.host.agent_studio.set_busy(False)
            except Exception:
                pass
        else:
            self.host.bubble_lane.push(text, role="assistant", ms=8000)


    def on_agent_error(self, err: str):
        try:
            get_event_bus().emit(AGENT_ERROR, err)
            get_plugin_manager().dispatch_agent_event(AGENT_ERROR, err)
        except Exception:
            pass
        self.host.bubble_lane.clear_thinking()
        from agent.run_control import is_invalid_chat_history, is_network_error

        if is_invalid_chat_history(err):
            status = "interrupted"
            prefix = "对话中断"
            hint = "\n（短时记忆已自动修复，可点「重试」继续）"
        elif is_network_error(err):
            status = "interrupted"
            prefix = "网络中断"
            hint = "\n（可点「重试」继续同一请求）"
        else:
            status = "failed"
            prefix = "出错了"
            hint = ""
        # 展示时去掉超长 LangGraph 排障链接，保留首句
        brief = (err or "").strip()
        if "For troubleshooting" in brief:
            brief = brief.split("For troubleshooting")[0].strip()
        if len(brief) > 480:
            brief = brief[:479] + "…"
        msg = f"{prefix}：{brief}{hint}"
        meta = {
            "status": status,
            "retryable": True,
            "error": err or "",
        }
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.host.agent_studio.finalize_stream()
                if isinstance(snap, dict):
                    meta["process"] = snap.get("process") or []
                    meta["terminals"] = snap.get("terminals") or []
            except Exception:
                pass
        try:
            chat_history.add_message("assistant", msg, meta=meta)
        except Exception:
            pass
        if studio_vis:
            try:
                self.host.agent_studio.reload()
                self.host.agent_studio.set_busy(False)
            except Exception:
                pass
        else:
            self.host.bubble_lane.push(msg, role="assistant", ms=10000)


    def on_retry_from_message(self, message_id: str):
        """从失败/中断的助手消息重试上一请求。"""
        if self.host.agent_runner.busy:
            return
        try:
            chat_history.drop_trailing_failed_assistant()
        except Exception:
            pass
        # 优先用 pending；否则找对应用户消息的 prompt
        from agent.run_control import get_pending

        pending = get_pending()
        prompt = ""
        mem = None
        uid = ""
        if pending and (pending.prompt or "").strip():
            prompt = pending.prompt
            mem = pending.user_text or None
            uid = pending.user_msg_id or ""
        else:
            try:
                # 截断后最后一条应为用户消息
                msgs = chat_history.list_messages(40)
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        meta = m.get("meta") if isinstance(m.get("meta"), dict) else {}
                        prompt = str(meta.get("prompt") or m.get("text") or "")
                        mem = str(m.get("text") or "") or None
                        uid = str(m.get("id") or "")
                        break
            except Exception:
                pass
        if not (prompt or "").strip():
            self.host.bubble_lane.push("没有可重试的请求。", role="assistant", ms=6000)
            return
        self._start_agent_prompt(prompt, memory_user_text=mem, user_msg_id=uid, add_user=False)


    def cancel_rewind_edit(self):
        """放弃从此重开编辑：不清空历史，只退出编辑态。"""
        was = bool(self.host._rewind_anchor_id)
        self.host._rewind_anchor_id = None
        try:
            self.host.chat_panel.set_rewind_mode(False)
        except Exception:
            pass
        try:
            self.host.agent_studio.set_rewind_mode(False)
        except Exception:
            pass
        if was:
            try:
                self.host.bubble_lane.push(
                    "已取消从此重开，对话保持原样。", role="assistant", ms=4500
                )
            except Exception:
                pass


    def on_rewind_from_message(self, message_id: str):
        """进入从此重开编辑：把原文载入输入框，发送才截断；取消则不变。"""
        if self.host.agent_runner.busy:
            self.host.bubble_lane.push("请先停止当前任务，再从此处重开。", role="assistant", ms=6000)
            return
        msg = chat_history.get_message(message_id)
        if not msg or msg.get("role") != "user":
            return
        old_text = str(msg.get("text") or "")
        self.host._rewind_anchor_id = message_id

        # 载入输入框供编辑（工作台优先）
        try:
            self.host.chat_panel.set_draft_text(old_text)
            self.host.chat_panel.set_rewind_mode(True, old_text)
        except Exception:
            pass
        try:
            self.host.agent_studio.set_draft_text(old_text)
            self.host.agent_studio.set_rewind_mode(True, old_text)
        except Exception:
            pass

        if self.host._studio_open and self.host.agent_studio.isVisible():
            try:
                self.host.agent_studio.raise_()
                self.host.agent_studio.activateWindow()
                self.host.agent_studio.input.setFocus()
            except Exception:
                pass
        else:
            # 小条可见时聚焦；否则打开工作台更方便编辑
            try:
                if self.host.chat_panel.isVisible():
                    self.host.chat_panel.input.setFocus()
                else:
                    self.host.open_agent_studio()
                    self.host.agent_studio.set_draft_text(old_text)
                    self.host.agent_studio.set_rewind_mode(True, old_text)
            except Exception:
                pass


    def _commit_rewind_send(
        self,
        text: str,
        attachments: list | None,
        message_id: str,
    ) -> None:
        """发送时才真正截断历史并用（可编辑后的）内容重跑。"""
        from pathlib import Path

        from agent.providers.media_gateway import resolve_turn

        new_text = (text or "").strip()
        attachments = list(attachments or [])
        if attachments and isinstance(attachments[0], str):
            attachments = [
                {"kind": "doc", "path": p, "name": Path(p).name, "analysis": ""}
                for p in attachments
            ]
        if not new_text and not attachments:
            self.host.bubble_lane.push("内容不能为空。", role="assistant", ms=5000)
            return

        # 先退出编辑态（避免递归）
        self.host._rewind_anchor_id = None
        try:
            self.host.chat_panel.set_rewind_mode(False)
            self.host.agent_studio.set_rewind_mode(False)
        except Exception:
            pass

        result = chat_history.truncate_after_message(message_id, keep_anchor=True)
        if not result.get("ok"):
            self.host.bubble_lane.push(
                f"回退失败：{result.get('error') or '未知错误'}",
                role="assistant",
                ms=7000,
            )
            return

        docs = [a["path"] for a in attachments if a.get("kind") == "doc" and a.get("path")]
        media = [a for a in attachments if a.get("kind") in ("audio", "image")]
        labels = []
        for a in attachments:
            name = a.get("name") or Path(str(a.get("path") or "")).name
            kind = a.get("kind")
            prefix = {"image": "🖼", "audio": "🎤", "doc": "📄"}.get(kind, "📎")
            labels.append(f"{prefix}{name}")
        bubble_bits = []
        if new_text:
            bubble_bits.append(new_text)
        if labels:
            bubble_bits.append(" ".join(labels))
        user_bubble = "\n".join(bubble_bits) if bubble_bits else "（附件）"

        try:
            turn = resolve_turn(new_text, doc_paths=docs, media_items=media)
            prompt = turn.text_prompt
            user_content = turn.user_content
        except Exception as e:
            self.host.bubble_lane.push(f"附件处理失败：{e}", role="assistant", ms=9000)
            return

        try:
            chat_history.replace_message_text(
                message_id, user_bubble, prompt=prompt
            )
        except Exception:
            pass

        if self.host._studio_open and self.host.agent_studio.isVisible():
            try:
                self.host.agent_studio.reload()
            except Exception:
                pass
        else:
            try:
                self.host.bubble_lane.push(user_bubble, role="user", ms=5000)
            except Exception:
                pass

        mem_text = new_text if new_text else (
            "（附件：" + "、".join(labels) + "）" if labels else user_bubble
        )
        self._start_agent_prompt(
            prompt,
            memory_user_text=mem_text,
            user_msg_id=message_id,
            add_user=False,
            user_content=user_content,
        )


    def _start_agent_prompt(
        self,
        prompt: str,
        *,
        memory_user_text: str | None = None,
        user_msg_id: str = "",
        add_user: bool = False,
        user_content=None,
    ) -> None:
        """内部启动 Agent（重试/回退用，默认不重复写入用户气泡）。"""
        if add_user and memory_user_text:
            try:
                chat_history.add_message("user", memory_user_text)
            except Exception:
                pass
        self.host.chat_panel.set_busy(True)
        studio_vis = self.host._studio_open and self.host.agent_studio.isVisible()
        if studio_vis:
            try:
                self.host.agent_studio.begin_stream()
            except Exception:
                pass
        else:
            self.host.bubble_lane.show_thinking()
        if not self.host.agent_runner.ask(
            prompt,
            memory_user_text=memory_user_text,
            user_msg_id=user_msg_id,
            user_content=user_content,
        ):
            self.host.chat_panel.set_busy(False)
            self.host.bubble_lane.clear_thinking()


    def on_bubble_open_full(self, text: str, role: str):
        """点击气泡 → 打开聊天记录并展示全文。"""
        tag = {"user": "我", "assistant": "Mini_Lu", "alarm": "闹钟", "quote": "语录"}.get(role, role)
        if role == "assistant":
            try:
                from agent.identity import assistant_label

                tag = assistant_label()
            except Exception:
                pass
        self.host.open_history_panel()
        self.host.history_panel.show_plain_text(tag, text)


