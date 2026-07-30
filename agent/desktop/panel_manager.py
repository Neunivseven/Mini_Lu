"""面板注册、布局与生命周期。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from agent import chat_history
from agent.agent_studio import AgentStudio
from agent.chat_history_panel import ChatHistoryPanel
from agent.chat_panel import ChatPanel
from agent.desktop.constants import WINDOW_HEIGHT, WINDOW_WIDTH
from agent.edit_staging import set_review_enabled
from agent.extensions_panel import ExtensionsPanel
from agent.file_workspace import get_active_root
from agent.memory_panel import MemoryPanel
from agent.models_panel import ModelsPanel
from agent.notes_panel import NotesPanel
from agent.prompt_panel import PromptPanel
from agent.quotes_panel import QuotesPanel
from agent.workspace_panel import WorkspacePanel

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class PanelManager:
    """创建并管理所有工具/聊天面板。"""

    def __init__(self, host: "QWidget"):
        self.host = host
        self.chat_panel = ChatPanel()
        self.notes_panel = NotesPanel()
        self.memory_panel = MemoryPanel()
        self.workspace_panel = WorkspacePanel()
        self.quotes_panel = QuotesPanel()
        self.prompt_panel = PromptPanel()
        self.models_panel = ModelsPanel()
        self.extensions_panel = ExtensionsPanel()
        self.history_panel = ChatHistoryPanel()
        self.agent_studio = AgentStudio()

    def open_panel(self, name: str) -> None:
        mapping = {
            "notes": self.open_notes_panel,
            "memory": self.open_memory_panel,
            "history": self.open_history_panel,
            "workspace": self.open_workspace_panel,
            "quotes": self.open_quotes_panel,
            "prompt": self.open_prompt_panel,
            "models": self.open_models_panel,
            "extensions": self.open_extensions_panel,
            "studio": self.open_agent_studio,
            "chat": self.open_chat,
        }
        key = (name or "").strip().lower()
        fn = mapping.get(key)
        if fn:
            try:
                from agent.event_bus import UI_OPEN_PANEL, get_event_bus

                get_event_bus().emit(UI_OPEN_PANEL, key)
            except Exception:
                pass
            fn()

    def open_chat(self):
        """打开输入条；若大窗口已开则聚焦工作台（不叠小条）。"""
        self.host.cancel_goto_pick()
        if self.host._studio_open and self.agent_studio.isVisible():
            self.agent_studio.raise_()
            self.agent_studio.activateWindow()
            try:
                self.agent_studio.input.setFocus()
            except Exception:
                pass
            return
        self.host._chat_open = True
        self.chat_panel.refresh_session_hint()
        self.host.bubble_lane.set_pet_geo(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self.chat_panel.show_near(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self._sync_bubble_avoid()


    def _reposition_chat(self):
        if self.chat_panel.is_pinned():
            return
        self.chat_panel.place_near(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self._sync_bubble_avoid()


    def _sync_bubble_avoid(self):
        """气泡排布时避开聊天输入框。"""
        from PySide6.QtCore import QRect

        if self.host._chat_open and self.chat_panel.isVisible():
            g = self.chat_panel.frameGeometry()
            self.host.bubble_lane.set_avoid_rect(
                QRect(g.x(), g.y(), g.width(), g.height())
            )
        else:
            self.host.bubble_lane.set_avoid_rect(None)


    def on_chat_closed(self):
        self.host._chat_open = False
        self.host.bubble_lane.set_avoid_rect(None)


    def _place_tool_panel_once(self, panel) -> None:
        """工具窗只在首次弹出时靠近桌宠一次；已打开则保持用户拖过的位置。"""
        if panel.isVisible():
            return
        panel.place_near(self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT)


    def open_notes_panel(self):
        """打开记事本面板（列表简略 → 点进全文）。"""
        self.host.cancel_goto_pick()
        self.host._notes_open = True
        self._place_tool_panel_once(self.notes_panel)
        self.notes_panel.show_panel()


    def _reposition_notes(self):
        # 工具窗不跟随桌宠
        return


    def on_notes_closed(self):
        self.host._notes_open = False


    def open_memory_panel(self):
        """打开记忆面板（运行/对话记忆，可删可重置）。"""
        self.host.cancel_goto_pick()
        self.host._memory_open = True
        self._place_tool_panel_once(self.memory_panel)
        self.memory_panel.show_panel()


    def open_history_panel(self):
        """打开对话列表 / 聊天记录（多 Agent 切换）。"""
        self.host.cancel_goto_pick()
        self.host._history_open = True
        self._place_tool_panel_once(self.history_panel)
        self.history_panel.show_panel()


    def create_new_agent(self):
        """新建独立对话，可主动命名。"""
        from agent.ui_dialogs import ask_text

        self.host.cancel_goto_pick()
        self.host.cancel_rewind_edit()
        title, ok = ask_text(
            self,
            "新对话",
            "给这次对话起个名字（可随时在对话列表里改）：",
            text="新对话",
            placeholder="例如：修登录页 / 整理笔记",
            ok_text="创建",
        )
        if not ok:
            return
        title = (title or "").strip() or "新对话"
        s = chat_history.create_session(title, activate=True)
        self.on_session_changed(s["id"])
        if self.host._studio_open and self.agent_studio.isVisible():
            self.agent_studio.reload()
        else:
            self.open_chat()
        try:
            self.host.bubble_lane.push(
                f"已新建对话：{s.get('title') or title}",
                role="assistant",
                ms=3500,
            )
        except Exception:
            pass


    def on_session_changed(self, session_id: str = ""):
        """切换对话后刷新输入栏提示。"""
        if self.host._rewind_anchor_id:
            self.host._rewind_anchor_id = None
            try:
                self.chat_panel.set_rewind_mode(False)
                self.agent_studio.set_rewind_mode(False)
            except Exception:
                pass
        if hasattr(self, "chat_panel"):
            self.chat_panel.refresh_session_hint()
        # 工作台内部已自行刷新聊天；仅同步历史面板
        if self.host._history_open and self.history_panel.isVisible():
            self.history_panel.reload()
        _ = session_id


    def open_agent_studio(self):
        """打开编码大窗口：聊天 + 改动对比；隐藏小输入条。"""
        self.host.cancel_goto_pick()
        already_open = bool(self.host._studio_open and self.agent_studio.isVisible())
        self.host._studio_open = True
        set_review_enabled(True)
        # 已打开时只刷新待确认列表，不要 place_near / show_panel（会把窗口拽回固定位置）
        if already_open:
            try:
                self.agent_studio.reload_edits()
            except Exception:
                pass
            return
        draft = ""
        try:
            draft = self.chat_panel.get_draft_text()
        except Exception:
            pass
        rewind_on = bool(self.host._rewind_anchor_id)
        if self.chat_panel.isVisible():
            self.chat_panel.hide()
        self.agent_studio.place_near(
            self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self.agent_studio.show_panel()
        # 小条草稿 → 大窗（在 show/reload 之后写入，避免被刷新冲掉）
        try:
            self.agent_studio.set_draft_text(draft)
        except Exception:
            pass
        if rewind_on:
            try:
                self.agent_studio.set_rewind_mode(True, draft)
            except Exception:
                pass


    def on_studio_collapse(self):
        """大窗「收起」→ 恢复小输入条，并带回草稿。"""
        self.host._studio_open = False
        self.host._chat_open = True
        try:
            self.chat_panel.set_draft_text(self.agent_studio.get_draft_text())
        except Exception:
            pass
        self.chat_panel.refresh_session_hint()
        if self.chat_panel.is_pinned():
            self.chat_panel.show()
            self.chat_panel.raise_()
        else:
            self.chat_panel.show_near(
                self.host.x(), self.host.y(), WINDOW_WIDTH, WINDOW_HEIGHT
            )


    def on_studio_closed(self):
        """大窗 ×：直接关闭，不自动打开小输入条；草稿写回小条供下次打开。"""
        self.host._studio_open = False
        self.host._chat_open = False
        try:
            self.chat_panel.set_draft_text(self.agent_studio.get_draft_text())
        except Exception:
            pass


    def apply_ui_font_zoom(self):
        """Ctrl+滚轮后刷新各聊天相关面板字号。"""
        try:
            from agent.message_view import refresh_font_sizes

            refresh_font_sizes()
        except Exception:
            pass
        try:
            self.chat_panel.apply_font_zoom()
        except Exception:
            pass
        try:
            self.agent_studio.apply_font_zoom()
        except Exception:
            pass


    def _reposition_studio(self):
        # 保留接口；大窗口不再强制跟随宠物
        return


    def on_edits_changed(self):
        if self.host._studio_open and self.agent_studio.isVisible():
            self.agent_studio.reload_edits()


    def on_studio_send(self, text: str, attachments: list | None = None):
        """大窗口发送：与小输入条同一套 Agent 管线（含附件）。"""
        self.host.agent_ctrl.on_chat_send(text, attachments)


    def on_history_closed(self):
        self.host._history_open = False


    def _reposition_history(self):
        return


    def open_workspace_panel(self):
        """打开工作区管理（选择/切换代码项目文件夹）。"""
        self.host.cancel_goto_pick()
        self.host._workspace_open = True
        self._place_tool_panel_once(self.workspace_panel)
        self.workspace_panel.show_panel()


    def on_workspace_closed(self):
        self.host._workspace_open = False


    def _reposition_workspace(self):
        return


    def _on_workspace_changed(self):
        self._refresh_workspace_tooltip()
        if hasattr(self, "agent_studio"):
            self.agent_studio.refresh_workspace()
        active = get_active_root()
        tip = f"当前项目：{active}" if active else "已更新工作区"
        try:
            self.host.bubble_lane.push(tip, role="assistant", ms=3500)
        except Exception:
            pass


    def _refresh_workspace_tooltip(self):
        active = get_active_root()
        if hasattr(self, "chat_panel") and hasattr(self.chat_panel, "ws_btn"):
            if active:
                self.chat_panel.ws_btn.setToolTip(f"工作区（当前：{active}）")
            else:
                self.chat_panel.ws_btn.setToolTip("工作区：打开/切换代码项目文件夹")


    def _reposition_memory(self):
        return


    def on_memory_closed(self):
        self.host._memory_open = False


    def open_quotes_panel(self):
        self.host.cancel_goto_pick()
        self.host._quotes_open = True
        self._place_tool_panel_once(self.quotes_panel)
        self.quotes_panel.show_panel()


    def _reposition_quotes(self):
        return


    def on_quotes_closed(self):
        self.host._quotes_open = False


    def open_prompt_panel(self):
        """打开 Prompt 版本 / A/B / 反馈改写面板。"""
        self.host.cancel_goto_pick()
        self.host._prompt_open = True
        self._place_tool_panel_once(self.prompt_panel)
        self.prompt_panel.show_panel()


    def _reposition_prompt(self):
        return


    def on_prompt_closed(self):
        self.host._prompt_open = False


    def open_models_panel(self):
        """打开多模型 / API 接入设置。"""
        self.host.cancel_goto_pick()
        self.host._models_open = True
        self._place_tool_panel_once(self.models_panel)
        self.models_panel.show_panel()


    def _reposition_models(self):
        return


    def on_models_closed(self):
        self.host._models_open = False


    def on_models_changed(self):
        """切换 Chat 模型后重建 Agent。"""
        ok = self.host.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self.host,
                "模型设置",
                "Agent 忙碌中，当前回复结束后的下一轮将使用新模型。",
            )


    def open_extensions_panel(self):
        self.host.cancel_goto_pick()
        self.host._extensions_open = True
        panel = self.extensions_panel
        if not panel.isVisible():
            if self.host._studio_open and self.agent_studio.isVisible():
                g = self.agent_studio.frameGeometry()
                # 先确保有默认尺寸再算位置
                if panel.width() < 100:
                    panel.resize(980, 580)
                x = g.right() + 8
                y = g.top() + 40
                from agent.hover_tip import screen_geometry_at
                from PySide6.QtCore import QPoint

                screen = screen_geometry_at(QPoint(g.center().x(), g.center().y()))
                if x + panel.width() > screen.right() - 8:
                    x = max(screen.left() + 8, g.left() - panel.width() - 8)
                x = max(screen.left() + 8, min(x, screen.right() - panel.width() - 8))
                y = max(screen.top() + 8, min(y, screen.bottom() - panel.height() - 8))
                panel.move(x, y)
            else:
                self._place_tool_panel_once(panel)
        panel.show_panel()


    def _reposition_extensions(self):
        return


    def on_extensions_closed(self):
        self.host._extensions_open = False


    def on_extensions_changed(self):
        try:
            from agent.plugin import reload_plugins_and_tools

            reload_plugins_and_tools()
        except Exception:
            pass
        ok = self.host.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self.host,
                "扩展",
                "Agent 忙碌中，当前回复结束后的下一轮将带上新 MCP 工具。",
            )


    def on_prompt_changed(self):
        """激活版本或 A/B 变更后重建 Agent。"""
        ok = self.host.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self.host,
                "Prompt",
                "Agent 忙碌中，稍后空闲会使用新 Prompt（或等当前回复结束后再试）。",
            )


