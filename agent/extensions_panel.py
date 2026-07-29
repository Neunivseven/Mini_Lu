"""扩展面板：Skills 管理 + MCP 状态；接入教程按需打开。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabBar,
    QTabWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.mcp_client import format_mcp_report, load_mcp_config, reload_mcp_tools
from agent.skills_store import (
    create_skill,
    default_skills_dir,
    discover_skills,
    effective_invocation_mode,
    enabled_skills,
    load_skills_config,
    set_skill_enabled,
    set_skill_mode,
    skills_guide_html,
)
from agent.ui_dialogs import ask_text, inform, warn
from agent.ui_fonts import mono_font_family, ui_font, ui_font_family
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at


def _open_path(path: Path) -> None:
    path = path.resolve()
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(path)], close_fds=True)
    except Exception as e:
        raise RuntimeError(str(e)) from e


def _section_card(title: str, hint: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    outer = QVBoxLayout(card)
    outer.setContentsMargins(12, 10, 12, 12)
    outer.setSpacing(8)

    head = QHBoxLayout()
    head.setSpacing(8)
    lab = QLabel(title)
    lab.setObjectName("sec")
    lab.setFont(ui_font(11, QFont.Bold))
    head.addWidget(lab)
    if hint:
        h = QLabel(hint)
        h.setObjectName("hint")
        h.setWordWrap(True)
        head.addWidget(h, 1)
    else:
        head.addStretch(1)
    outer.addLayout(head)
    return card, outer


def _guide_css() -> str:
    """须在 QApplication 创建之后调用（会查字体库）。"""
    return f"""
<style>
body {{
  margin: 0;
  padding: 0;
  color: #1E293B;
  font-family: "{ui_font_family()}", "Microsoft YaHei UI", sans-serif;
  font-size: 13px;
  line-height: 1.55;
  background: transparent;
}}
.wrap {{
  padding: 8px 14px 20px 10px;
}}
h1 {{
  margin: 4px 0 8px 0;
  font-size: 18px;
  font-weight: 700;
  color: #0F172A;
  letter-spacing: 0.2px;
}}
h2 {{
  margin: 18px 0 10px 0;
  font-size: 13px;
  font-weight: 700;
  color: #334155;
  text-transform: none;
  letter-spacing: 0.3px;
  border-bottom: 1px solid #E2E8F0;
  padding-bottom: 6px;
}}
.lead {{
  margin: 0 0 4px 0;
  color: #64748B;
  font-size: 12.5px;
}}
code {{
  font-family: "{mono_font_family()}", Consolas, monospace;
  font-size: 12px;
  background: #EEF2F7;
  color: #0F3A52;
  padding: 1px 6px;
  border-radius: 4px;
}}
ol.steps {{
  list-style: none;
  margin: 0;
  padding: 0;
}}
ol.steps li {{
  display: flex;
  gap: 12px;
  align-items: flex-start;
  margin: 0 0 10px 0;
  padding: 10px 12px;
  background: #FFFFFF;
  border: 1px solid #E2E8F0;
  border-radius: 10px;
}}
ol.steps .n {{
  flex: 0 0 26px;
  width: 26px;
  height: 26px;
  line-height: 26px;
  text-align: center;
  border-radius: 50%;
  background: #3D7EA6;
  color: #fff;
  font-weight: 700;
  font-size: 12px;
}}
ol.steps strong {{
  display: block;
  color: #0F172A;
  font-size: 13px;
  margin-bottom: 2px;
}}
ol.steps p {{
  margin: 0;
  color: #475569;
  font-size: 12.5px;
}}
.modes {{
  display: block;
}}
.mode {{
  margin: 0 0 8px 0;
  padding: 10px 12px;
  background: #F8FAFC;
  border: 1px solid #E2E8F0;
  border-left: 3px solid #3D7EA6;
  border-radius: 8px;
}}
.mode-title {{
  font-weight: 700;
  color: #0F172A;
  margin-bottom: 2px;
}}
.mode p {{
  margin: 0;
  color: #475569;
  font-size: 12.5px;
}}
.foot {{
  margin-top: 16px;
  color: #94A3B8;
  font-size: 12px;
}}
</style>
"""


class ExtensionsPanel(QWidget):
    closed = Signal()
    extensions_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self._mode_btns: dict[str, QPushButton] = {}
        self.setStyleSheet(
            f"""
            QWidget#root {{
                background: #F3F6FA;
                border: 2px solid #94A3B8;
                border-radius: 12px;
            }}
            QLabel#title {{
                color: #1E293B; font-weight: 700;
                font-family: "{ui_font_family()}";
            }}
            QLabel#sec {{
                color: #1E293B; font-weight: 700;
                font-family: "{ui_font_family()}";
            }}
            QLabel#hint {{
                color: #64748B; font-size: 11px;
                font-family: "{ui_font_family()}";
            }}
            QFrame#card {{
                background: #FFFFFF;
                border: 1px solid #C5D0DC;
                border-radius: 10px;
            }}
            QTextEdit, QListWidget, QTextBrowser {{
                background: #FFFFFF;
                border: 1px solid #C5D0DC;
                border-radius: 8px;
                padding: 8px;
                color: #1E293B;
                font-size: 12px;
                selection-background-color: #D7E8F4;
            }}
            QTextEdit {{
                font-family: "{mono_font_family()}", Consolas, monospace;
            }}
            QTextBrowser {{
                font-family: "{ui_font_family()}";
                border: none;
                background: transparent;
                padding: 0;
            }}
            QFrame#guidePane {{
                background: #FFFFFF;
                border: 1px solid #C5D0DC;
                border-radius: 10px;
            }}
            QListWidget::item {{
                padding: 8px 6px;
                border-radius: 6px;
                margin: 1px 0;
            }}
            QListWidget::item:hover {{ background: #EEF4F9; }}
            QListWidget::item:selected {{
                background: #D7E8F4;
                color: #0F172A;
            }}
            QPushButton {{
                background: #3D7EA6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 7px 12px;
                font-weight: 700;
                min-height: 28px;
                font-family: "{ui_font_family()}";
            }}
            QPushButton:hover {{ background: #2F6A8C; }}
            QPushButton#ghost {{
                background: #E8EEF5;
                color: #334155;
            }}
            QPushButton#ghost:hover {{ background: #D9E2EC; }}
            QPushButton#modeBtn {{
                background: #E8EEF5;
                color: #334155;
                font-weight: 600;
                padding: 6px 14px;
                min-width: 64px;
            }}
            QPushButton#modeBtn:hover {{ background: #D9E2EC; }}
            QPushButton#modeBtn[active="true"] {{
                background: #3D7EA6;
                color: white;
            }}
            QToolButton#navBtn {{
                background: transparent;
                color: #334155;
                border: none;
                border-radius: 6px;
                padding: 4px 10px;
                font-weight: 600;
                font-family: "{ui_font_family()}";
            }}
            QToolButton#navBtn:hover {{
                background: #DCE4EE;
                color: #0F172A;
            }}
            QPushButton#closeBtn {{
                background: transparent;
                color: #64748B;
                font-size: 16px;
                padding: 2px 8px;
            }}
            QPushButton#closeBtn:hover {{
                background: #C45C5C;
                color: #fff;
            }}
            QTabWidget#extTabs::pane {{
                border: none;
                background: transparent;
                top: 0;
            }}
            QTabWidget#extTabs > QTabBar::tab {{
                background: #E8EEF5;
                color: #64748B;
                border: 1px solid #C5D0DC;
                border-bottom: none;
                padding: 7px 14px;
                margin-right: 2px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                font-family: "{ui_font_family()}";
                font-size: 12px;
            }}
            QTabWidget#extTabs > QTabBar::tab:selected {{
                background: #FFFFFF;
                color: #1E293B;
                font-weight: 700;
                border-bottom: 2px solid #3D7EA6;
            }}
            QTabWidget#extTabs > QTabBar::tab:hover:!selected {{
                background: #EEF4F9;
                color: #1E293B;
            }}
            QTabWidget#extTabs QTabBar::close-button {{
                subcontrol-position: right;
                padding: 3px;
                margin-right: 2px;
                border-radius: 3px;
            }}
            QTabWidget#extTabs QTabBar::close-button:hover {{
                background: rgba(128, 128, 128, 0.25);
            }}
            QSplitter::handle {{
                background: transparent;
            }}
            QSplitter::handle:horizontal {{
                width: 8px;
                margin: 4px 2px;
                border-radius: 3px;
                background: #D5DEE8;
            }}
            QSplitter::handle:horizontal:hover {{
                background: #3D7EA6;
            }}
            """
        )

        root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(14, 12, 14, 14)
        lay.setSpacing(10)

        title = QLabel("扩展 · Skills / MCP")
        title.setObjectName("title")
        title.setFont(ui_font(13, QFont.Bold))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setToolTip("关闭")
        close_btn.clicked.connect(self.hide_panel)

        # 顶栏右侧：按需打开教程
        btn_guide = QToolButton()
        btn_guide.setObjectName("navBtn")
        btn_guide.setText("接入教程")
        btn_guide.setToolTip("打开 Skills 接入说明（按需显示）")
        btn_guide.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_guide.clicked.connect(self._open_guide_tab)

        header = build_panel_header(title, close_btn)
        header.setStyleSheet(
            """
            QWidget#panelHeader {
                background: #E8EEF5;
                border-radius: 8px;
            }
            """
        )
        # build_panel_header 通常是 title + close；在 close 前插入教程按钮
        try:
            h_lay = header.layout()
            if h_lay is not None:
                h_lay.insertWidget(max(0, h_lay.count() - 1), btn_guide)
        except Exception:
            tip_row = QHBoxLayout()
            tip_row.addStretch(1)
            tip_row.addWidget(btn_guide)
            lay.addLayout(tip_row)
        lay.addWidget(header)
        self._title = title
        self._btn_guide = btn_guide

        self.tabs = QTabWidget()
        self.tabs.setObjectName("extTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self._on_tab_close)

        # —— 管理页：Skills | MCP ——
        manage = QWidget()
        manage_lay = QVBoxLayout(manage)
        manage_lay.setContentsMargins(0, 6, 0, 0)
        manage_lay.setSpacing(0)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        split.setOpaqueResize(True)
        self._main_split = split

        skills_card, sl = _section_card(
            "已发现的 Skills",
            "选中后启用 / 禁用 · 切换 Auto / Manual / Always",
        )
        self.skills_list = QListWidget()
        self.skills_list.setMinimumWidth(260)
        self.skills_list.setAlternatingRowColors(False)
        self.skills_list.itemDoubleClicked.connect(self._open_selected_skill)
        self.skills_list.currentItemChanged.connect(self._on_skill_selection_changed)
        sl.addWidget(self.skills_list, 1)

        sgrid = QGridLayout()
        sgrid.setSpacing(6)
        actions = (
            ("打开目录", self._open_skills_dir, True),
            ("新建 Skill…", self._new_skill, False),
            ("启用", lambda: self._toggle_selected(True), True),
            ("禁用", lambda: self._toggle_selected(False), True),
            ("打开文件", self._open_selected_skill, True),
            ("刷新", self.reload, True),
        )
        for i, (text, slot, ghost) in enumerate(actions):
            b = QPushButton(text)
            if ghost:
                b.setObjectName("ghost")
            b.clicked.connect(slot)
            sgrid.addWidget(b, i // 3, i % 3)
        sl.addLayout(sgrid)

        mrow = QHBoxLayout()
        mrow.setSpacing(6)
        mode_tip = QLabel("调用模式")
        mode_tip.setObjectName("hint")
        mrow.addWidget(mode_tip)
        for text, mode in (("Auto", "auto"), ("Manual", "manual"), ("Always", "always")):
            b = QPushButton(text)
            b.setObjectName("modeBtn")
            b.setProperty("active", False)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(
                {
                    "auto": "目录可见，模型可自行 load_skill；写入 skills.local.yaml",
                    "manual": "仅目录可见，需显式加载；不改 SKILL.md",
                    "always": "正文尝试注入 system（占 token）",
                }[mode]
            )
            b.clicked.connect(lambda _=False, m=mode: self._set_mode_selected(m))
            self._mode_btns[mode] = b
            mrow.addWidget(b)
        mrow.addStretch(1)
        sl.addLayout(mrow)
        split.addWidget(skills_card)

        mcp_card, ml = _section_card(
            "MCP 状态",
            "修改 mcp.yaml 后点「重新加载 MCP」",
        )
        self.mcp_view = QTextEdit()
        self.mcp_view.setReadOnly(True)
        self.mcp_view.setMinimumWidth(200)
        ml.addWidget(self.mcp_view, 1)
        mrow_mcp = QHBoxLayout()
        mrow_mcp.setSpacing(6)
        btn_reload = QPushButton("重新加载 MCP")
        btn_reload.clicked.connect(self._reload_mcp)
        mrow_mcp.addWidget(btn_reload)
        btn_refresh = QPushButton("刷新显示")
        btn_refresh.setObjectName("ghost")
        btn_refresh.clicked.connect(self.reload)
        mrow_mcp.addWidget(btn_refresh)
        ml.addLayout(mrow_mcp)
        split.addWidget(mcp_card)

        split.setSizes([520, 360])
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        manage_lay.addWidget(split, 1)

        self.tabs.addTab(manage, "Skills / MCP")
        try:
            self.tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
            self.tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        except Exception:
            pass

        # —— 教程页（默认不挂载；点「接入教程」再打开）——
        self._guide_host = QWidget(self)
        self._guide_host.setVisible(False)
        gh = QVBoxLayout(self._guide_host)
        gh.setContentsMargins(0, 0, 0, 0)

        guide_pane = QFrame()
        guide_pane.setObjectName("guidePane")
        gpl = QVBoxLayout(guide_pane)
        gpl.setContentsMargins(8, 8, 8, 8)
        gpl.setSpacing(6)

        guide_head = QHBoxLayout()
        guide_title = QLabel("接入说明")
        guide_title.setObjectName("sec")
        guide_head.addWidget(guide_title)
        guide_head.addStretch(1)
        btn_close_guide = QPushButton("关闭教程")
        btn_close_guide.setObjectName("ghost")
        btn_close_guide.setToolTip("关闭接入教程选项卡")
        btn_close_guide.clicked.connect(self._close_guide_tab)
        guide_head.addWidget(btn_close_guide)
        gpl.addLayout(guide_head)

        self.guide = QTextBrowser()
        self.guide.setOpenExternalLinks(True)
        self.guide.setFrameShape(QFrame.NoFrame)
        self.guide.setHtml(_guide_css() + skills_guide_html())
        gpl.addWidget(self.guide, 1)
        gh.addWidget(guide_pane)
        self.guide_page = guide_pane

        lay.addWidget(self.tabs, 1)

        attach_move_resize(
            self,
            header,
            width=920,
            height=560,
            min_width=680,
            min_height=420,
        )
        seal_hidden_toplevel(self)

    def _open_guide_tab(self) -> None:
        idx = self.tabs.indexOf(self.guide_page)
        if idx < 0:
            idx = self.tabs.addTab(self.guide_page, "接入教程")
        self.tabs.setCurrentIndex(idx)
        try:
            self.guide.setHtml(_guide_css() + skills_guide_html())
            self.guide.verticalScrollBar().setValue(0)
        except Exception:
            pass

    def _close_guide_tab(self) -> None:
        idx = self.tabs.indexOf(self.guide_page)
        if idx < 0:
            return
        self.tabs.removeTab(idx)
        self.guide_page.setParent(self._guide_host)
        if self._guide_host.layout() is not None:
            self._guide_host.layout().addWidget(self.guide_page)
        self.tabs.setCurrentIndex(0)

    def _on_tab_close(self, index: int) -> None:
        if index < 0 or index >= self.tabs.count():
            return
        if self.tabs.widget(index) is self.guide_page:
            self._close_guide_tab()

    def _sync_mode_buttons(self, mode: str | None):
        for key, btn in self._mode_btns.items():
            active = bool(mode) and key == mode
            btn.setProperty("active", active)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _on_skill_selection_changed(self, current, _previous=None):
        if not current:
            self._sync_mode_buttons(None)
            return
        mode = current.data(Qt.UserRole + 3)
        self._sync_mode_buttons(str(mode) if mode else None)

    def reload(self):
        try:
            cfg = load_mcp_config()
            header = (
                f"config enabled={cfg.get('enabled')} "
                f"servers={list((cfg.get('servers') or {}).keys())}\n\n"
            )
            self.mcp_view.setPlainText(header + format_mcp_report())
        except Exception as e:
            self.mcp_view.setPlainText(str(e))

        prev_name = None
        cur = self.skills_list.currentItem()
        if cur:
            prev_name = cur.data(Qt.UserRole + 1)

        self.skills_list.clear()
        try:
            cfg = load_skills_config()
            on_set = {s.name for s in enabled_skills(cfg)}
            restore_row = -1
            for i, sk in enumerate(discover_skills(cfg)):
                mode = effective_invocation_mode(sk, cfg)
                mark = "✓" if sk.name in on_set else "·"
                item = QListWidgetItem(
                    f"{mark}  {sk.name}  [{mode}]\n    {sk.description[:120]}"
                )
                item.setData(Qt.UserRole, str(sk.path))
                item.setData(Qt.UserRole + 1, sk.name)
                item.setData(Qt.UserRole + 2, sk.name in on_set)
                item.setData(Qt.UserRole + 3, mode)
                self.skills_list.addItem(item)
                if prev_name and sk.name == prev_name:
                    restore_row = i
            if self.skills_list.count() == 0:
                self.skills_list.addItem("（尚未发现 Skill — 点「新建 Skill…」开始）")
                self._sync_mode_buttons(None)
            elif restore_row >= 0:
                self.skills_list.setCurrentRow(restore_row)
            else:
                self.skills_list.setCurrentRow(0)
        except Exception as e:
            self.skills_list.addItem(str(e))
            self._sync_mode_buttons(None)

    def _selected_skill_meta(self) -> tuple[str, Path] | None:
        item = self.skills_list.currentItem()
        if not item:
            return None
        name = item.data(Qt.UserRole + 1)
        path = item.data(Qt.UserRole)
        if not name or not path:
            return None
        return str(name), Path(str(path))

    def _open_skills_dir(self):
        try:
            _open_path(default_skills_dir())
        except Exception as e:
            warn(self, "Skills", str(e))

    def _new_skill(self):
        name, ok = ask_text(
            self,
            "新建 Skill",
            "目录名请用英文/数字/连字符（如 my-review）：",
            placeholder="my-review",
            ok_text="创建",
        )
        if not ok or not (name or "").strip():
            return
        try:
            path = create_skill(name.strip())
            self.reload()
            _open_path(path)
            inform(
                self,
                "Skills",
                f"已创建：\n{path}\n\n"
                "请编辑 description 与正文，保存后点「刷新」。\n"
                "完整说明见 docs/SKILLS.md",
            )
        except Exception as e:
            warn(self, "Skills", str(e))

    def _open_selected_skill(self):
        meta = self._selected_skill_meta()
        if not meta:
            inform(self, "Skills", "请先选中列表中的一项。")
            return
        _, path = meta
        try:
            _open_path(path)
        except Exception as e:
            warn(self, "Skills", str(e))

    def _toggle_selected(self, enable: bool):
        meta = self._selected_skill_meta()
        if not meta:
            inform(self, "Skills", "请先选中列表中的一项。")
            return
        name, _ = meta
        try:
            set_skill_enabled(name, enable)
            self.reload()
            self.extensions_changed.emit()
            inform(
                self,
                "Skills",
                f"已{'启用' if enable else '禁用'}「{name}」。\n"
                "写入 config/skills.local.yaml；下一轮对话生效。",
            )
        except Exception as e:
            warn(self, "Skills", str(e))

    def _set_mode_selected(self, mode: str):
        meta = self._selected_skill_meta()
        if not meta:
            inform(self, "Skills", "请先选中列表中的一项。")
            return
        name, _ = meta
        try:
            set_skill_mode(name, mode)
            self.reload()
            self.extensions_changed.emit()
            tip = {
                "auto": "模型可自行 load_skill",
                "manual": "仅目录可见，需显式加载",
                "always": "正文将尝试注入 system（占 token）",
            }.get(mode, mode)
            inform(
                self,
                "Skills",
                f"「{name}」已设为 [{mode}]\n{tip}\n\n"
                "已写入 config/skills.local.yaml（未改 SKILL.md）；下一轮对话生效。",
            )
        except Exception as e:
            warn(self, "Skills", str(e))

    def _reload_mcp(self):
        try:
            reload_mcp_tools(force=True)
            self.reload()
            self.extensions_changed.emit()
            inform(
                self,
                "MCP",
                "已重新加载 MCP 工具，并请求重建 Agent。\n"
                "若当前正在回答，结束后下一轮生效。",
            )
        except Exception as e:
            warn(self, "MCP", str(e))

    def show_panel(self):
        prepare_toplevel_show(self, activate=True)
        self.reload()
        # 每次打开回到管理页；教程保持按需
        self.tabs.setCurrentIndex(0)
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_panel(self):
        self.hide()
        self.closed.emit()

    def place_near(self, global_x: int, global_y: int, pet_w: int = 200, pet_h: int = 260):
        from PySide6.QtCore import QPoint

        x = global_x - self.width() - 8
        y = global_y + 10
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        if x < screen.left() + 8:
            x = global_x + pet_w + 8
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
