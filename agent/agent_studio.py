"""
Agent 大窗口：聊天流 + 会话切换 + 代码改动内联对比 / 保留放弃。
支持自由缩放、按当前屏 availableGeometry 最大化（兼顾任务栏与分屏）、工作区切换。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup, QFont, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from agent import chat_history
from agent.chat_panel import ChatPanel
from agent.edit_staging import (
    apply_all,
    apply_edit,
    decide_hunk,
    first_pending_hunk_id,
    get_edit,
    list_pending,
    reject_all,
    reject_edit,
    stage_edit,
)
from agent.hover_tip import screen_geometry_at
from agent.inline_diff_view import InlineDiffView
from agent.message_view import MessageList
from agent.models_panel import ModelsPanel
from agent.monaco_editor import MonacoEditor, lang_from_path
from agent.studio_prefs import (
    append_usage_event,
    load_layout,
    reset_layout,
    save_layout,
)
from agent.studio_theme import THEMES, get_theme, load_theme_id, save_theme_id
from agent.terminal_panel import TerminalPanel
from agent.ui_dialogs import ask_multiline, ask_text, confirm, inform, warn
from agent.ui_fonts import mono_font_family, ui_font, ui_font_family
from agent.workspace_tree import WorkspaceFileTree

# 主题加载前的默认色
CLOTH = "#3D7EA6"
BG = "#F3F6FA"
SURFACE = "#E8EEF5"
PANEL = "#FFFFFF"
INK = "#1E293B"
MUTED = "#64748B"
BORDER = "#C5D0DC"
PANEL_W = 1280
PANEL_H = 780
EDGE = 8  # 边缘拖拽缩放热区
# PySide6 的 Qt.Edge 不是 int 枚举，用自定义位标志
_L, _R, _T, _B = 1, 2, 4, 8


class _DragBar(QWidget):
    """顶栏：拖动；双击最大化/还原。"""

    double_clicked = Signal()

    def __init__(self, host: "AgentStudio", parent=None):
        super().__init__(parent)
        self._host = host
        self._offset: QPoint | None = None
        self.setObjectName("dragBar")
        self.setCursor(Qt.SizeAllCursor)
        self.setMinimumHeight(32)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            if self._host.is_zoomed():
                # 最大化时按下准备拖出还原
                self._offset = event.globalPosition().toPoint()
            else:
                self._offset = (
                    event.globalPosition().toPoint() - self._host.frameGeometry().topLeft()
                )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._offset is not None and event.buttons() & Qt.LeftButton:
            gpos = event.globalPosition().toPoint()
            if self._host.is_zoomed():
                # 拖出最大化 → 还原到鼠标相对宽度比例位置
                self._host.restore_from_zoom(anchor_global=gpos)
                geo = self._host.frameGeometry()
                self._offset = QPoint(int(geo.width() * 0.4), 20)
            self._host.move(gpos - self._offset)
            self._host._user_placed = True
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class AgentStudio(QWidget):
    closed = Signal()
    collapse_requested = Signal()  # 收起 → 小输入条
    send_requested = Signal(str, list)  # text, attachments
    new_agent_requested = Signal()
    session_changed = Signal(str)
    workspace_requested = Signal()  # 打开完整工作区管理面板
    workspace_changed = Signal()
    extensions_requested = Signal()  # 打开 Skills / MCP 扩展面板
    models_changed = Signal()  # 工作台内切换模型
    rewind_requested = Signal(str)  # message_id
    retry_requested = Signal(str)  # message_id
    stop_requested = Signal()
    rewind_cancel_requested = Signal()

    def __init__(self, parent=None):
        # Window：任务栏可见；Frameless：自绘顶栏（不置顶，避免霸占其它应用）
        super().__init__(parent, Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DontShowOnScreen, True)
        self.resize(PANEL_W, PANEL_H)
        self.setMinimumSize(720, 480)
        self._current_edit_id: str | None = None
        self._focus_hunk_id: int | None = None
        self._view_sid: str | None = None
        self._switching = False
        self._editing_path: Path | None = None
        self._editing_loaded_text = ""
        self._editor_dirty = False
        self._editor_lock = False
        self._code_panel_mode = "edit"
        self._zoomed = False
        self._restore_geom: QRect | None = None
        self._resize_edges = 0  # Qt.LeftEdge | …
        self._resize_origin: QPoint | None = None
        self._resize_geom: QRect | None = None
        self._user_placed = False  # 用户拖过/缩放过则不再被宠物移动拽走
        self._theme = get_theme(load_theme_id())
        self._layout_loading = False
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(450)
        self._layout_save_timer.timeout.connect(self._save_layout_now)

        self.setStyleSheet(self._chrome_qss())

        root = QWidget(self)
        root.setObjectName("studioRoot")
        self._root = root
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # 顶栏导航
        self.drag_bar = _DragBar(self)
        self.drag_bar.double_clicked.connect(self.toggle_zoom)
        bar_lay = QHBoxLayout(self.drag_bar)
        bar_lay.setContentsMargins(8, 2, 4, 2)
        bar_lay.setSpacing(2)

        self.title = QLabel("Agent 工作台")
        self.title.setObjectName("title")
        self.title.setFont(ui_font(11, QFont.DemiBold))
        self.title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.title.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        bar_lay.addWidget(self.title)
        try:
            self.refresh_identity()
        except Exception:
            pass

        sep0 = QLabel("│")
        sep0.setObjectName("navSep")
        sep0.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        bar_lay.addWidget(sep0)

        def _nav_btn(text: str, tip: str = "") -> QToolButton:
            b = QToolButton(self.drag_bar)
            b.setObjectName("navBtn")
            b.setText(text)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
            b.setAutoRaise(True)
            if tip:
                b.setToolTip(tip)
            return b

        # 工作区菜单
        self.btn_ws_menu = _nav_btn("工作区", "切换 / 管理工作区")
        self.btn_ws_menu.setPopupMode(QToolButton.InstantPopup)
        ws_menu = QMenu(self.btn_ws_menu)
        act_switch = QAction("切换文件夹…", ws_menu)
        act_switch.triggered.connect(self._pick_workspace)
        ws_menu.addAction(act_switch)
        act_mgr = QAction("管理多根目录…", ws_menu)
        act_mgr.triggered.connect(self.workspace_requested.emit)
        ws_menu.addAction(act_mgr)
        self.btn_ws_menu.setMenu(ws_menu)
        bar_lay.addWidget(self.btn_ws_menu)

        btn_models_nav = _nav_btn("模型", "打开聊天区「模型」配置选项卡")
        btn_models_nav.clicked.connect(self._show_models_tab)
        bar_lay.addWidget(btn_models_nav)

        # 主题 / 布局菜单
        self.btn_theme = _nav_btn("主题", "配色与布局")
        self.btn_theme.setPopupMode(QToolButton.InstantPopup)
        self._theme_menu = QMenu(self.btn_theme)
        self._theme_group = QActionGroup(self._theme_menu)
        self._theme_group.setExclusive(True)
        self._rebuild_theme_menu()
        self.btn_theme.setMenu(self._theme_menu)
        bar_lay.addWidget(self.btn_theme)

        btn_skills = _nav_btn("Skills", "打开 Skills / MCP 扩展设置")
        btn_skills.clicked.connect(self.extensions_requested.emit)
        bar_lay.addWidget(btn_skills)

        bar_lay.addStretch(1)

        self.badge = QLabel("")
        self.badge.setObjectName("meta")
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.badge.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.badge.setMinimumWidth(0)
        self.badge.setWordWrap(False)
        self.badge.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bar_lay.addWidget(self.badge)

        btn_collapse = _nav_btn("收起", "收起为大窗口旁的小输入条（保留草稿）")
        btn_collapse.clicked.connect(self.collapse_to_chat)
        bar_lay.addWidget(btn_collapse)

        self.btn_zoom = QPushButton("□")
        self.btn_zoom.setObjectName("winBtn")
        self.btn_zoom.setToolTip("最大化到当前显示器工作区（避开任务栏；分屏时用当前屏）")
        self.btn_zoom.clicked.connect(self.toggle_zoom)
        bar_lay.addWidget(self.btn_zoom)

        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.setToolTip("关闭工作台（不打开小输入条）")
        close_btn.clicked.connect(self.hide_panel)
        bar_lay.addWidget(close_btn)
        lay.addWidget(self.drag_bar)

        # 工作区路径条
        self.ws_bar = QWidget(root)
        self.ws_bar.setObjectName("wsBar")
        ws_lay = QHBoxLayout(self.ws_bar)
        ws_lay.setContentsMargins(10, 4, 8, 4)
        ws_lay.setSpacing(6)
        ws_tag = QLabel("工作区", self.ws_bar)
        ws_tag.setObjectName("meta")
        ws_tag.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        ws_lay.addWidget(ws_tag)
        slash = QLabel("/", self.ws_bar)
        slash.setObjectName("navSep")
        ws_lay.addWidget(slash)
        self.ws_path = QLabel("（未设置）", self.ws_bar)
        self.ws_path.setObjectName("wsPath")
        self.ws_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.ws_path.setWordWrap(False)
        self.ws_path.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.ws_path.setMinimumWidth(80)
        self.ws_path.setToolTip("Agent 读写代码的当前项目根目录")
        ws_lay.addWidget(self.ws_path, stretch=1)
        btn_switch = QToolButton(self.ws_bar)
        btn_switch.setObjectName("navBtn")
        btn_switch.setText("切换文件夹")
        btn_switch.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_switch.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        btn_switch.setToolTip("选择并设为当前工作区")
        btn_switch.clicked.connect(self._pick_workspace)
        ws_lay.addWidget(btn_switch)
        btn_mgr = QToolButton(self.ws_bar)
        btn_mgr.setObjectName("navBtn")
        btn_mgr.setText("管理")
        btn_mgr.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_mgr.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        btn_mgr.setToolTip("打开工作区列表面板（多根目录）")
        btn_mgr.clicked.connect(self.workspace_requested.emit)
        ws_lay.addWidget(btn_mgr)
        lay.addWidget(self.ws_bar)

        self._left_collapsed = False
        self._files_collapsed = False
        self._left_width = 200
        self._files_width = 220

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        self.main_split = split

        # 左：会话（可折叠）
        left = QWidget(split)
        self.left_panel = left
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        left_head = QHBoxLayout()
        left_title = QLabel("对话", left)
        left_title.setObjectName("sectionHead")
        left_head.addWidget(left_title)
        left_head.addStretch()
        self.btn_collapse_left = QPushButton("«")
        self.btn_collapse_left.setObjectName("railBtn")
        self.btn_collapse_left.setToolTip("收起对话列表")
        self.btn_collapse_left.clicked.connect(self._toggle_left_rail)
        left_head.addWidget(self.btn_collapse_left)
        ll.addLayout(left_head)
        self.session_list = QListWidget()
        self.session_list.setToolTip("单击打开该对话；右键可删除")
        self.session_list.itemClicked.connect(self._on_session_clicked)
        self.session_list.itemDoubleClicked.connect(self._on_session_clicked)
        self.session_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.session_list.customContextMenuRequested.connect(self._session_menu)
        ll.addWidget(self.session_list, 1)
        nrow = QHBoxLayout()
        btn_new = QPushButton("＋ 新对话")
        btn_new.clicked.connect(self._on_new_agent)
        btn_open = QPushButton("打开")
        btn_open.setObjectName("ghost")
        btn_open.setToolTip("打开选中的对话")
        btn_open.clicked.connect(self._open_selected_session)
        btn_del = QPushButton("删除")
        btn_del.setObjectName("danger")
        btn_del.setToolTip("删除选中的对话")
        btn_del.clicked.connect(self._delete_selected_session)
        nrow.addWidget(btn_new)
        nrow.addWidget(btn_open)
        nrow.addWidget(btn_del)
        ll.addLayout(nrow)
        split.addWidget(left)

        # 收起后的窄条
        self.left_rail = QWidget(split)
        self.left_rail.setFixedWidth(28)
        self.left_rail.setVisible(False)
        lr = QVBoxLayout(self.left_rail)
        lr.setContentsMargins(2, 4, 2, 4)
        self.btn_expand_left = QPushButton("»", self.left_rail)
        self.btn_expand_left.setObjectName("railBtn")
        self.btn_expand_left.setToolTip("展开对话列表")
        self.btn_expand_left.clicked.connect(self._toggle_left_rail)
        lr.addWidget(self.btn_expand_left)
        lr.addStretch()
        split.addWidget(self.left_rail)

        # 中：聊天 / 模型 选项卡
        mid = QWidget(split)
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        self.mid_tabs = QTabWidget(mid)
        self.mid_tabs.setObjectName("midTabs")
        self.mid_tabs.setDocumentMode(True)
        self.mid_tabs.setMovable(False)
        self.mid_tabs.setTabsClosable(True)
        self.mid_tabs.tabCloseRequested.connect(self._on_mid_tab_close)
        self.chat_head = self.mid_tabs  # 兼容缩放刷新

        chat_page = QWidget()
        cpl = QVBoxLayout(chat_page)
        cpl.setContentsMargins(0, 4, 0, 0)
        cpl.setSpacing(6)

        chat_split = QSplitter(Qt.Vertical)
        chat_split.setChildrenCollapsible(False)
        chat_split.setHandleWidth(6)
        chat_split.setToolTip("拖动调整消息区与输入区高度")
        self.chat_split = chat_split

        self.msg_list = MessageList(chat_page)
        self.msg_list.feedback.connect(self._on_msg_feedback)
        self.msg_list.rewind_requested.connect(self.rewind_requested.emit)
        self.msg_list.retry_requested.connect(self.retry_requested.emit)
        self.msg_list.code_send_requested.connect(self._on_inline_code_send)
        self.msg_list.code_apply_requested.connect(self._on_inline_code_apply)
        chat_split.addWidget(self.msg_list)

        self.composer = ChatPanel(self, embedded=True)
        self.composer.send_requested.connect(self._on_composer_send)
        self.composer.stop_requested.connect(self.stop_requested.emit)
        self.composer.rewind_cancel_requested.connect(self.rewind_cancel_requested.emit)
        chat_split.addWidget(self.composer)
        chat_split.setStretchFactor(0, 4)
        chat_split.setStretchFactor(1, 1)
        chat_split.setSizes([420, 160])
        cpl.addWidget(chat_split, 1)
        self.input = self.composer.input

        # 模型页：按需挂到选项卡；关闭后仍保活，可从顶栏「模型」再打开
        self._models_host = QWidget(mid)
        self._models_host.setVisible(False)
        self.models_embed = ModelsPanel(self._models_host, embedded=True)
        self.models_embed.models_changed.connect(self.models_changed.emit)
        self.models_embed.close_requested.connect(self._close_models_tab)
        self.models_embed.apply_theme(self._theme)
        mh = QVBoxLayout(self._models_host)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.models_embed)

        self.mid_tabs.addTab(chat_page, "聊天")
        # 「聊天」不可关闭
        try:
            self.mid_tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.RightSide, None)
            self.mid_tabs.tabBar().setTabButton(0, QTabBar.ButtonPosition.LeftSide, None)
        except Exception:
            pass
        ml.addWidget(self.mid_tabs, 1)
        split.addWidget(mid)

        # 代码面板（统一标签栏 + Monaco 编辑 / diff 预览）
        edits = QWidget(split)
        edits.setObjectName("editsPanel")
        self.edits_panel = edits
        rl = QVBoxLayout(edits)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(0)

        # 统一标签栏
        self.file_tabs = QTabBar(edits)
        self.file_tabs.setTabsClosable(True)
        self.file_tabs.setMovable(True)
        self.file_tabs.setExpanding(False)
        self.file_tabs.setDocumentMode(True)
        self.file_tabs.setDrawBase(False)
        self.file_tabs.tabCloseRequested.connect(self._on_tab_close)
        self.file_tabs.currentChanged.connect(self._on_tab_changed)
        self.file_tabs.setStyleSheet(self._file_tabs_qss())
        self._tab_data: list[dict] = []
        rl.addWidget(self.file_tabs)

        # 底部状态/操作行
        action_row = QHBoxLayout()
        action_row.setContentsMargins(4, 4, 4, 2)
        action_row.setSpacing(6)
        self.editor_title = QLabel("双击文件目录打开编辑", edits)
        self.editor_title.setObjectName("meta")
        action_row.addWidget(self.editor_title, 1)
        self.chk_autosave = QCheckBox("自动保存", edits)
        self.chk_autosave.setChecked(True)
        self.chk_autosave.setToolTip("停止输入约 1 秒后自动写盘；Ctrl+S 立即保存")
        self.chk_autosave.toggled.connect(self._on_autosave_toggled)
        action_row.addWidget(self.chk_autosave)
        self.btn_editor_reload = QPushButton("重载", edits)
        self.btn_editor_reload.setObjectName("ghost")
        self.btn_editor_reload.clicked.connect(self._reload_editor_file)
        action_row.addWidget(self.btn_editor_reload)
        self.btn_editor_save = QPushButton("保存并暂存", edits)
        self.btn_editor_save.setObjectName("ok")
        self.btn_editor_save.clicked.connect(self._save_editor_stage)
        action_row.addWidget(self.btn_editor_save)
        rl.addLayout(action_row)

        # 代码面板
        self.code_stack = QStackedWidget(edits)

        self.editor = MonacoEditor(self.code_stack)
        self.editor.content_changed.connect(self._on_editor_text_changed)
        self.editor.save_requested.connect(self._save_editor_now)
        self.code_stack.addWidget(self.editor)  # index 0

        # 自动保存 + 磁盘监视（与 AI 写盘协同）
        self._autosave_enabled = True
        self._disk_conflict = False
        self._suppress_fs = False
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(1200)
        self._autosave_timer.timeout.connect(self._autosave_now)
        self._fs_watcher = QFileSystemWatcher(self)
        self._fs_watcher.fileChanged.connect(self._on_disk_file_changed)

        diff_page = QWidget(self.code_stack)
        dpl = QVBoxLayout(diff_page)
        dpl.setContentsMargins(0, 0, 0, 0)
        dpl.setSpacing(6)
        self.diff_panel = InlineDiffView(diff_page)
        self.diff_panel.hunk_keep.connect(self._on_inline_keep)
        self.diff_panel.hunk_discard.connect(self._on_inline_discard)
        self.diff_panel.hunk_keep_all.connect(self._save_all_file)
        self.diff_panel.hunk_discard_all.connect(self._reject_all_file)
        dpl.addWidget(self.diff_panel, 1)

        self.diff_meta = QLabel("", diff_page)
        self.diff_meta.setObjectName("meta")
        self.diff_meta.setWordWrap(True)
        dpl.addWidget(self.diff_meta)
        self.code_stack.addWidget(diff_page)  # index 1

        rl.addWidget(self.code_stack, 1)
        split.addWidget(edits)
        self._code_panel_mode = "edit"
        self._sync_code_panel_mode_ui()

        # 最右：文件目录 + 终端（可折叠）
        files_wrap = QWidget(split)
        files_wrap.setObjectName("filesPanel")
        self.files_panel = files_wrap
        fl = QVBoxLayout(files_wrap)
        fl.setContentsMargins(8, 8, 8, 8)
        fl.setSpacing(4)
        fhead = QHBoxLayout()
        self.files_head_icon = QLabel(files_wrap)
        self.files_head_icon.setFixedSize(16, 16)
        fhead.addWidget(self.files_head_icon, 0, Qt.AlignVCenter)
        self.files_title = QLabel("文件目录", files_wrap)
        self.files_title.setObjectName("sectionHead")
        fhead.addWidget(self.files_title)
        fhead.addStretch()
        self.btn_collapse_files = QPushButton("»", files_wrap)
        self.btn_collapse_files.setObjectName("railBtn")
        self.btn_collapse_files.setToolTip("收起文件目录")
        self.btn_collapse_files.clicked.connect(self._toggle_files_rail)
        fhead.addWidget(self.btn_collapse_files)
        fl.addLayout(fhead)

        files_split = QSplitter(Qt.Vertical, files_wrap)
        files_split.setChildrenCollapsible(False)
        files_split.setHandleWidth(6)
        self.files_split = files_split

        self.file_tree = WorkspaceFileTree(files_wrap)
        self.file_tree.file_open_requested.connect(self._on_file_open)
        self.file_tree.tree_changed.connect(self.refresh_workspace)
        tree_host = QWidget(files_wrap)
        thl = QVBoxLayout(tree_host)
        thl.setContentsMargins(0, 0, 0, 0)
        thl.setSpacing(4)
        thl.addWidget(self.file_tree, 1)
        files_split.addWidget(tree_host)

        term_host = QWidget(files_wrap)
        th2 = QVBoxLayout(term_host)
        th2.setContentsMargins(0, 0, 0, 0)
        th2.setSpacing(4)
        self.terminal_panel = TerminalPanel(term_host)
        th2.addWidget(self.terminal_panel, 1)
        files_split.addWidget(term_host)
        files_split.setStretchFactor(0, 2)
        files_split.setStretchFactor(1, 0)
        files_split.setSizes([520, 0])
        files_split.setCollapsible(1, True)
        fl.addWidget(files_split, 1)
        split.addWidget(files_wrap)

        self.files_rail = QWidget(split)
        self.files_rail.setObjectName("filesPanel")
        self.files_rail.setFixedWidth(28)
        self.files_rail.setVisible(False)
        fr = QVBoxLayout(self.files_rail)
        fr.setContentsMargins(2, 6, 2, 4)
        self.btn_expand_files = QPushButton("«", self.files_rail)
        self.btn_expand_files.setObjectName("railBtn")
        self.btn_expand_files.setToolTip("展开文件目录")
        self.btn_expand_files.clicked.connect(self._toggle_files_rail)
        fr.addWidget(self.btn_expand_files)
        fr.addStretch()
        split.addWidget(self.files_rail)

        # 主分割：left | left_rail | chat | edits | files | files_rail
        #          0      1         2      3      4       5
        split.setSizes([180, 0, 380, 400, 200, 0])
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 0)
        split.setStretchFactor(2, 3)
        split.setStretchFactor(3, 3)
        split.setStretchFactor(4, 1)
        split.setStretchFactor(5, 0)
        # 允许文件目录面板缩得更窄
        files_wrap.setMinimumWidth(120)
        body = QWidget(root)
        body.setObjectName("studioBody")
        bl = QVBoxLayout(body)
        bl.setContentsMargins(8, 6, 8, 4)
        bl.setSpacing(0)
        bl.addWidget(split, 1)
        lay.addWidget(body, 1)

        # 右下角缩放柄
        grip_row = QHBoxLayout()
        grip_row.setContentsMargins(0, 0, 4, 2)
        grip_row.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(18, 18)
        grip_row.addWidget(grip, 0, Qt.AlignRight | Qt.AlignBottom)
        lay.addLayout(grip_row)

        self.setMouseTracking(True)
        root.setMouseTracking(True)
        self._refresh_section_icons()
        try:
            from agent.ui_icons import app_icon

            self.setWindowIcon(app_icon())
        except Exception:
            pass
        self.refresh_workspace()
        from agent.hover_tip import seal_hidden_toplevel

        seal_hidden_toplevel(self)

        # 分割条变化 → 防抖写入用户布局偏好
        self.main_split.splitterMoved.connect(lambda *_: self._schedule_save_layout())
        self.chat_split.splitterMoved.connect(lambda *_: self._schedule_save_layout())
        self.files_split.splitterMoved.connect(lambda *_: self._schedule_save_layout())
        try:
            self._apply_layout(load_layout())
        except Exception:
            pass

    def _file_tabs_qss(self) -> str:
        t = self._theme
        hover = t.list_hover
        return f"""
            QTabBar {{
                background: {t.surface};
            }}
            QTabBar::tab {{
                background: {t.surface};
                color: {t.muted};
                border: none;
                border-right: 1px solid {t.border};
                padding: 5px 22px 5px 10px;
                font-size: 11px;
                min-width: 60px;
                max-width: 220px;
            }}
            QTabBar::tab:selected {{
                background: {t.panel};
                color: {t.ink};
                border-bottom: 2px solid {t.cloth};
                font-weight: 600;
            }}
            QTabBar::tab:hover:!selected {{
                background: {hover};
                color: {t.ink};
            }}
            QTabBar::close-button {{
                subcontrol-position: right;
                padding: 4px;
                margin-right: 2px;
                border-radius: 3px;
                background: transparent;
            }}
            QTabBar::close-button:hover {{
                background: rgba(128, 128, 128, 0.25);
            }}
        """

    def _rebuild_theme_menu(self) -> None:
        menu = self._theme_menu
        menu.clear()
        for act in list(self._theme_group.actions()):
            self._theme_group.removeAction(act)
        for tid, th in THEMES.items():
            act = QAction(th.label, menu)
            act.setCheckable(True)
            act.setChecked(tid == self._theme.id)
            act.setData(tid)
            act.triggered.connect(
                lambda checked=False, i=tid: self._set_theme(i) if checked else None
            )
            self._theme_group.addAction(act)
            menu.addAction(act)
        menu.addSeparator()
        act_reset = QAction("重置布局为默认", menu)
        act_reset.setToolTip("恢复出厂面板比例；配色主题不变")
        act_reset.triggered.connect(self._reset_layout_to_factory)
        menu.addAction(act_reset)

    def _set_theme(self, theme_id: str) -> None:
        save_theme_id(theme_id)
        self._theme = get_theme(theme_id)
        self._apply_chrome()
        self._rebuild_theme_menu()
        append_usage_event("theme_change", {"theme": theme_id})

    def _show_models_tab(self, *, record: bool = True) -> None:
        if not hasattr(self, "mid_tabs") or not hasattr(self, "models_embed"):
            return
        idx = self.mid_tabs.indexOf(self.models_embed)
        if idx < 0:
            idx = self.mid_tabs.addTab(self.models_embed, "模型")
        self.mid_tabs.setCurrentIndex(idx)
        try:
            self.models_embed.reload()
            self.models_embed.apply_theme(self._theme)
        except Exception:
            pass
        self._schedule_save_layout()
        if record:
            append_usage_event("open_models_tab")

    def _close_models_tab(self, *, record: bool = True) -> None:
        if not hasattr(self, "mid_tabs") or not hasattr(self, "models_embed"):
            return
        idx = self.mid_tabs.indexOf(self.models_embed)
        if idx < 0:
            return
        self.mid_tabs.removeTab(idx)
        self.models_embed.setParent(self._models_host)
        if self._models_host.layout() is not None:
            self._models_host.layout().addWidget(self.models_embed)
        self.mid_tabs.setCurrentIndex(0)
        self._schedule_save_layout()
        if record:
            append_usage_event("close_models_tab")

    def _on_mid_tab_close(self, index: int) -> None:
        if index < 0 or index >= self.mid_tabs.count():
            return
        w = self.mid_tabs.widget(index)
        if w is self.models_embed:
            self._close_models_tab()

    def _schedule_save_layout(self) -> None:
        if not hasattr(self, "_layout_save_timer"):
            return
        self._layout_save_timer.start()

    def _collect_layout(self) -> dict:
        layout: dict = {
            "main_split": list(self.main_split.sizes()) if hasattr(self, "main_split") else [],
            "chat_split": list(self.chat_split.sizes()) if hasattr(self, "chat_split") else [],
            "files_split": list(self.files_split.sizes()) if hasattr(self, "files_split") else [],
            "left_collapsed": bool(getattr(self, "_left_collapsed", False)),
            "files_collapsed": bool(getattr(self, "_files_collapsed", False)),
            "models_tab_open": bool(
                hasattr(self, "mid_tabs")
                and hasattr(self, "models_embed")
                and self.mid_tabs.indexOf(self.models_embed) >= 0
            ),
            "editor_autosave": bool(getattr(self, "_autosave_enabled", True)),
        }
        if not self._zoomed:
            layout["window_size"] = [max(720, self.width()), max(480, self.height())]
        return layout

    def _save_layout_now(self) -> None:
        if getattr(self, "_layout_loading", False):
            return
        try:
            save_layout(self._collect_layout())
        except Exception:
            pass

    def _apply_layout(self, layout: dict | None = None, *, factory: bool = False) -> None:
        from agent.studio_prefs import factory_layout

        data = factory_layout() if factory else (layout or load_layout())
        self._layout_loading = True
        try:
            left_collapsed = bool(data.get("left_collapsed"))
            files_collapsed = bool(data.get("files_collapsed"))
            if hasattr(self, "left_panel"):
                self.left_panel.setVisible(not left_collapsed)
                self.left_rail.setVisible(left_collapsed)
                self._left_collapsed = left_collapsed
            if hasattr(self, "files_panel"):
                self.files_panel.setVisible(not files_collapsed)
                self.files_rail.setVisible(files_collapsed)
                self._files_collapsed = files_collapsed

            ms = data.get("main_split") or []
            if hasattr(self, "main_split") and isinstance(ms, list) and len(ms) == 6:
                self.main_split.setSizes([int(x) for x in ms])
            cs = data.get("chat_split") or []
            if hasattr(self, "chat_split") and isinstance(cs, list) and len(cs) == 2:
                self.chat_split.setSizes([int(x) for x in cs])
            fs = data.get("files_split") or []
            if hasattr(self, "files_split") and isinstance(fs, list) and len(fs) == 2:
                self.files_split.setSizes([int(x) for x in fs])

            wsz = data.get("window_size") or []
            if (
                not self._zoomed
                and isinstance(wsz, list)
                and len(wsz) == 2
                and int(wsz[0]) >= 720
                and int(wsz[1]) >= 480
            ):
                self.resize(int(wsz[0]), int(wsz[1]))

            if data.get("models_tab_open"):
                self._show_models_tab(record=False)
            else:
                self._close_models_tab(record=False)

            if hasattr(self, "chk_autosave"):
                self.chk_autosave.setChecked(bool(data.get("editor_autosave", True)))
        finally:
            self._layout_loading = False

    def _reset_layout_to_factory(self) -> None:
        layout = reset_layout()
        self._apply_layout(layout, factory=True)
        append_usage_event("reset_layout")
        inform(self, "布局", "已恢复出厂默认面板比例。")

    def _chrome_qss(self) -> str:
        """工作台样式；字号随 Ctrl+滚轮缩放。"""
        from agent.ui_zoom import px

        t = getattr(self, "_theme", None) or get_theme("white")
        cloth, bg, surface, panel = t.cloth, t.bg, t.surface, t.panel
        ink, muted, border = t.ink, t.muted, t.border
        root_border = "#64748B" if t.is_dark else "#94A3B8"
        ok = "#2F8F6B" if not t.is_dark else "#3D9B72"
        ok_h = "#267A5A" if not t.is_dark else "#348560"
        danger = "#C45C5C"
        danger_h = "#A94A4A"
        return f"""
            QWidget#studioRoot {{
                background: {bg};
                border: 2px solid {root_border};
                border-radius: 10px;
            }}
            QWidget#studioRoot[zoomed="true"] {{
                border-radius: 0px;
            }}
            QWidget#dragBar {{
                background: {t.nav_bg};
                border-bottom: 1px solid {border};
                border-radius: 0px;
            }}
            QWidget#wsBar {{
                background: {t.ws_bg};
                border: none;
                border-bottom: 1px solid {t.ws_border};
                border-radius: 0px;
            }}
            QWidget#studioBody {{
                background: {bg};
            }}
            QWidget#filesPanel {{
                background: {surface};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QWidget#editsPanel {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 6px;
            }}
            QLabel#title {{
                color: {ink}; font-weight: 600; font-size: {px(12)};
                font-family: "{ui_font_family()}";
                padding-right: 6px;
            }}
            QLabel#meta {{
                color: {muted}; font-size: {px(10)}; font-weight: 400;
                font-family: "{ui_font_family()}";
            }}
            QLabel#navSep {{
                color: {muted}; font-size: {px(11)}; padding: 0 4px;
            }}
            QLabel#sectionHead {{
                color: {ink}; font-size: {px(11)}; font-weight: 600;
                font-family: "{ui_font_family()}";
                letter-spacing: 0.2px;
            }}
            QLabel#wsPath {{
                color: {ink}; font-size: {px(11)}; font-weight: 500;
                font-family: "{ui_font_family()}";
            }}
            QToolButton#navBtn {{
                background: transparent;
                color: {t.nav_fg};
                border: none;
                border-radius: 4px;
                padding: 4px 10px;
                font-weight: 500;
                font-size: {px(11)};
                font-family: "{ui_font_family()}";
            }}
            QToolButton#navBtn:hover {{
                background: {t.nav_hover};
                color: {ink};
            }}
            QToolButton#navBtn::menu-indicator {{
                image: none;
                width: 0px;
            }}
            QListWidget {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 6px;
                outline: none;
                font-weight: 400;
                font-size: {px(11)};
                font-family: "{ui_font_family()}";
                color: {ink};
            }}
            QListWidget::item {{ padding: 8px 8px; border-bottom: 1px solid {surface}; }}
            QListWidget::item:selected {{ background: {t.list_sel}; color: {ink}; }}
            QListWidget::item:hover {{ background: {t.list_hover}; }}
            QTextEdit {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 8px;
                color: {ink};
                font-family: "{mono_font_family()}", Consolas, monospace;
                font-size: {px(12.5)};
                font-weight: 400;
            }}
            QLineEdit {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 8px;
                color: {ink};
                font-family: "{mono_font_family()}", Consolas, monospace;
                font-size: {px(11)};
            }}
            QTabWidget#midTabs::pane {{
                border: 1px solid {border};
                border-top: none;
                background: {panel};
                border-radius: 0 0 6px 6px;
            }}
            QTabWidget#midTabs > QTabBar::tab {{
                background: {surface};
                color: {muted};
                border: 1px solid {border};
                border-bottom: none;
                padding: 7px 16px;
                margin-right: 1px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: {px(11)};
                font-family: "{ui_font_family()}";
            }}
            QTabWidget#midTabs > QTabBar::tab:selected {{
                background: {panel};
                color: {ink};
                font-weight: 600;
                border-bottom: 2px solid {cloth};
            }}
            QTabWidget#midTabs > QTabBar::tab:hover:!selected {{
                background: {t.list_hover};
                color: {ink};
            }}
            QTabWidget#midTabs QTabBar::close-button {{
                subcontrol-position: right;
                padding: 3px;
                margin-right: 2px;
                border-radius: 3px;
                background: transparent;
            }}
            QTabWidget#midTabs QTabBar::close-button:hover {{
                background: rgba(128, 128, 128, 0.28);
            }}
            QTabWidget::pane {{
                border: 1px solid {border};
                border-radius: 6px;
                background: {panel};
            }}
            QTabBar::tab {{
                background: {surface};
                border: 1px solid {border};
                padding: 6px 10px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: {muted};
            }}
            QTabBar::tab:selected {{
                background: {panel};
                color: {ink};
            }}
            QPushButton {{
                background: {cloth}; color: white; border: none;
                border-radius: 6px; padding: 7px 12px; font-weight: 600;
                font-family: "{ui_font_family()}";
                font-size: {px(11)};
            }}
            QPushButton:hover {{ background: {cloth}; color: white; opacity: 0.9; }}
            QPushButton#ghost {{
                background: {t.ghost_bg}; color: {ink}; font-weight: 500;
                border: 1px solid {border};
            }}
            QPushButton#ghost:hover {{ background: {t.ghost_hover}; }}
            QPushButton#ok {{ background: {ok}; color: white; }}
            QPushButton#ok:hover {{ background: {ok_h}; color: white; }}
            QPushButton#danger {{ background: {danger}; color: white; }}
            QPushButton#danger:hover {{ background: {danger_h}; color: white; }}
            QPushButton#railBtn {{
                background: {panel}; color: {cloth}; font-weight: 700;
                border: 1px solid {border};
                padding: 4px 6px; min-width: 22px; max-width: 28px;
            }}
            QPushButton#railBtn:hover {{ background: {t.list_sel}; }}
            QPushButton#winBtn {{
                background: transparent; color: {muted}; font-size: {px(13)};
                font-weight: 600; padding: 4px 10px; min-width: 28px;
            }}
            QPushButton#winBtn:hover {{ background: {t.nav_hover}; color: {ink}; }}
            QPushButton#closeBtn {{
                background: transparent; color: {muted}; font-size: {px(16)}; padding: 2px 8px;
            }}
            QPushButton#closeBtn:hover {{ color: #fff; background: {danger}; }}
            QSplitter::handle {{
                background: {t.split_handle};
                width: 5px;
                height: 5px;
                margin: 1px;
                border-radius: 2px;
            }}
            QSplitter::handle:hover {{ background: {cloth}; }}
            """

    def _apply_chrome(self) -> None:
        self.setStyleSheet(self._chrome_qss())
        try:
            if hasattr(self, "file_tabs"):
                self.file_tabs.setStyleSheet(self._file_tabs_qss())
        except Exception:
            pass
        try:
            if hasattr(self, "models_embed"):
                self.models_embed.apply_theme(self._theme)
        except Exception:
            pass
        try:
            from agent.ui_zoom import pt

            if hasattr(self, "title"):
                self.title.setFont(ui_font(pt(11), QFont.DemiBold))
        except Exception:
            pass
        self._refresh_section_icons()

    def _refresh_section_icons(self) -> None:
        try:
            from agent.ui_icons import pixmap
            from agent.ui_zoom import pt

            sz = max(14, pt(16))
            pass
            if hasattr(self, "files_head_icon"):
                self.files_head_icon.setFixedSize(sz, sz)
                self.files_head_icon.setPixmap(pixmap("files", sz))
        except Exception:
            pass

    # 工作区

    def refresh_workspace(self):
        try:
            from agent.file_workspace import get_active_root

            active = get_active_root()
        except Exception:
            active = None
        if active:
            self.ws_path.setText(str(active))
            self.ws_path.setToolTip(str(active))
        else:
            self.ws_path.setText("（未设置 · 相对路径默认相对应用目录）")
            self.ws_path.setToolTip("点击「切换文件夹」选择项目根目录")
        if hasattr(self, "file_tree"):
            self.file_tree.reload()

    def _toggle_left_rail(self):
        sizes = self.main_split.sizes()
        # 0 left, 1 left_rail, 2 chat, 3 edits, 4 files, 5 files_rail
        if not self._left_collapsed:
            self._left_width = max(sizes[0], 160)
            self.left_panel.setVisible(False)
            self.left_rail.setVisible(True)
            self._left_collapsed = True
            chat = sizes[2] + max(0, sizes[0] - 28)
            self.main_split.setSizes(
                [0, 28, chat, sizes[3], sizes[4], sizes[5]]
            )
        else:
            self.left_rail.setVisible(False)
            self.left_panel.setVisible(True)
            self._left_collapsed = False
            chat = max(200, sizes[2] - self._left_width + 28)
            self.main_split.setSizes(
                [self._left_width, 0, chat, sizes[3], sizes[4], sizes[5]]
            )
        self._schedule_save_layout()

    def _toggle_files_rail(self):
        sizes = self.main_split.sizes()
        # 4 files, 5 files_rail
        if not self._files_collapsed:
            self._files_width = max(sizes[4], 160)
            self.files_panel.setVisible(False)
            self.files_rail.setVisible(True)
            self._files_collapsed = True
            edits = sizes[3] + max(0, sizes[4] - 28)
            self.main_split.setSizes(
                [sizes[0], sizes[1], sizes[2], edits, 0, 28]
            )
        else:
            self.files_rail.setVisible(False)
            self.files_panel.setVisible(True)
            self._files_collapsed = False
            edits = max(200, sizes[3] - self._files_width + 28)
            self.main_split.setSizes(
                [sizes[0], sizes[1], sizes[2], edits, self._files_width, 0]
            )
        self._schedule_save_layout()

    def _on_file_open(self, path: str):
        """双击文件：在标签栏打开（或激活已有标签）。"""
        p = Path(path).resolve()
        self._open_file_in_editor(p)

    def _set_code_panel_mode(self, mode: str) -> None:
        mode = "diff" if mode == "diff" else "edit"
        self._code_panel_mode = mode
        self._sync_code_panel_mode_ui()
        if mode == "diff" and self._current_edit_id:
            self._show_edit(self._current_edit_id, prefer_hunk=self._focus_hunk_id)

    def _sync_code_panel_mode_ui(self) -> None:
        is_edit = self._code_panel_mode != "diff"
        self.code_stack.setCurrentIndex(0 if is_edit else 1)
        self.btn_editor_reload.setVisible(is_edit)
        self.chk_autosave.setVisible(is_edit)
        # 自动保存开启时隐藏「保存并暂存」（自动直接写盘，无需手动）
        self.btn_editor_save.setVisible(is_edit and not self._autosave_enabled)

    # 统一标签栏管理

    def _find_tab(self, *, path: Path | None = None, edit_id: str | None = None) -> int:
        for i, td in enumerate(self._tab_data):
            if path and td.get("type") == "file" and td.get("path") == path:
                return i
            if edit_id and td.get("type") == "edit" and td.get("edit_id") == edit_id:
                return i
        return -1

    def _add_or_activate_file_tab(self, p: Path) -> int:
        idx = self._find_tab(path=p)
        if idx >= 0:
            self.file_tabs.setCurrentIndex(idx)
            return idx
        self._tab_data.append({"type": "file", "path": p})
        idx = self.file_tabs.addTab(p.name)
        self.file_tabs.setCurrentIndex(idx)
        return idx

    def _add_or_activate_edit_tab(self, edit_id: str, name: str, hunks_left: int) -> int:
        idx = self._find_tab(edit_id=edit_id)
        if idx >= 0:
            label = f"🔴 {name} ({hunks_left}段)"
            self.file_tabs.setTabText(idx, label)
            self.file_tabs.setCurrentIndex(idx)
            return idx
        label = f"🔴 {name} ({hunks_left}段)"
        self._tab_data.append({"type": "edit", "edit_id": edit_id})
        idx = self.file_tabs.addTab(label)
        self.file_tabs.setCurrentIndex(idx)
        return idx

    def _on_tab_close(self, idx: int):
        if 0 <= idx < len(self._tab_data):
            td = self._tab_data[idx]
            if td.get("type") == "file" and self._editor_dirty and self._editing_path == td.get("path"):
                self._flush_autosave()
            if td.get("type") == "file" and self._editor_dirty and self._editing_path == td.get("path"):
                if not confirm(self, "关闭标签", f"{td['path'].name} 有未保存修改，确定关闭？", yes_text="关闭"):
                    return
            self._tab_data.pop(idx)
            self.file_tabs.removeTab(idx)
            if self.file_tabs.count() == 0:
                self._editing_path = None
                self._editor_dirty = False
                self.editor_title.setText("双击文件目录打开编辑")
                self._set_code_panel_mode("edit")

    def _on_tab_changed(self, idx: int):
        if self._switching:
            return
        if idx < 0 or idx >= len(self._tab_data):
            return
        self._flush_autosave()
        td = self._tab_data[idx]
        if td.get("type") == "file":
            p = td["path"]
            self._load_file_into_editor(p)
            self._set_code_panel_mode("edit")
        elif td.get("type") == "edit":
            eid = td["edit_id"]
            self._current_edit_id = eid
            self._focus_hunk_id = None
            self._show_edit(eid)
            self._set_code_panel_mode("diff")

    def _load_file_into_editor(self, p: Path) -> None:
        text = ""
        if p.exists():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return
        self._editor_lock = True
        lang = lang_from_path(p)
        self.editor.set_content(text, lang)
        self._editor_lock = False
        self._editing_path = p
        self._editing_loaded_text = text
        self._editor_dirty = False
        self._disk_conflict = False
        self.editor.set_file_path(str(p))
        watched = self._fs_watcher.files()
        if watched:
            self._fs_watcher.removePaths(watched)
        if p.exists():
            self._fs_watcher.addPath(str(p))
        tag = "新文件" if not p.exists() else "编辑中"
        self.editor_title.setText(f"{p.name} · {tag}")

    def _on_inline_code_send(self, code: str) -> None:
        cur = self.composer.get_draft_text() or ""
        add = (code or "").rstrip()
        if not add:
            return
        if cur.strip():
            cur = cur.rstrip() + "\n\n```text\n" + add + "\n```"
        else:
            cur = "```text\n" + add + "\n```"
        self.composer.set_draft_text(cur)
        self.input.setFocus()

    def _on_inline_code_apply(self, code: str, lang: str, _mid: str) -> None:
        if not (code or "").strip():
            return
        default_path = ""
        if self._editing_path is not None:
            default_path = str(self._editing_path)
        target, ok = ask_text(
            self,
            "应用代码块到文件",
            "输入目标文件路径（相对当前工作区或绝对路径）：",
            text=default_path,
            placeholder="src/foo.py",
            ok_text="应用",
        )
        if not ok or not (target or "").strip():
            return
        try:
            from agent.file_workspace import resolve_workspace_path

            p = resolve_workspace_path(target.strip(), for_write=True)
        except Exception as e:
            warn(self, "代码块应用", str(e))
            return
        before = ""
        if p.exists():
            try:
                before = p.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                warn(self, "代码块应用", f"读取目标失败: {e}")
                return
        after = code.rstrip("\n") + "\n"
        if before == after:
            inform(self, "代码块应用", "目标文件内容未变化。")
            return
        if p.exists() and before.strip():
            if not confirm(
                self,
                "覆盖目标文件",
                f"将把代码块内容作为文件完整内容写入：\n{p}\n\n建议用于新建/草稿文件。是否继续？",
                yes_text="继续",
                danger=False,
            ):
                return
        summary = f"聊天代码块应用 {p.name} [{lang or 'code'}]"
        msg = stage_edit(str(p), before, after, summary=summary)
        inform(self, "代码块应用", msg)
        self.reload_edits()
        self.file_tree.reload()
        self._open_file_in_editor(p)

    def _open_file_in_editor(self, p: Path, *, switch_mode: bool = True) -> None:
        if p.exists() and not p.is_file():
            warn(self, "文件编辑器", f"不是文件: {p}")
            return
        self._add_or_activate_file_tab(p)

    def _on_editor_text_changed(self) -> None:
        if self._editor_lock:
            return
        if self._editing_path is None:
            return
        self._editor_dirty = True
        if self._disk_conflict:
            return  # 冲突提示保持显示
        self.editor_title.setText(f"{self._editing_path.name} · 有未保存改动")
        if self._autosave_enabled:
            self._autosave_timer.start()

    def _reload_editor_file(self) -> None:
        if self._editing_path is None:
            return
        self._open_file_in_editor(self._editing_path)

    def _save_editor_stage(self) -> None:
        if self._editing_path is None:
            warn(self, "文件编辑器", "请先在文件目录双击一个文件。")
            return
        self.editor.get_content(self._save_editor_stage_cb)

    def _save_editor_stage_cb(self, after: str) -> None:
        if not self._confirm_overwrite_if_conflict():
            return
        before = self._editing_loaded_text
        if before == after:
            inform(self, "文件编辑器", "内容无变化。")
            return
        summary = f"工作台编辑保存 {self._editing_path.name}"
        self._suppress_fs = True
        try:
            msg = stage_edit(str(self._editing_path), before, after, summary=summary)
        finally:
            QTimer.singleShot(400, self._release_fs_suppress)
        self._editing_loaded_text = after
        self._editor_dirty = False
        self._disk_conflict = False
        self.editor_title.setText(f"{self._editing_path.name} · 已暂存")
        self.reload_edits()
        self.file_tree.reload()
        inform(self, "文件编辑器", msg)

    # —— 自动保存 / 磁盘同步 ——

    def _on_autosave_toggled(self, on: bool) -> None:
        self._autosave_enabled = bool(on)
        if not hasattr(self, "code_stack"):
            return  # 构造期间的初始 setChecked
        self._sync_code_panel_mode_ui()
        if on and getattr(self, "_editor_dirty", False):
            self._autosave_timer.start()
        self._schedule_save_layout()

    def _release_fs_suppress(self) -> None:
        self._suppress_fs = False
        p = self._editing_path
        if p is not None and p.exists() and str(p) not in self._fs_watcher.files():
            self._fs_watcher.addPath(str(p))

    def _flush_autosave(self) -> None:
        """切标签/关窗前把未保存改动落盘（仅自动保存开且无冲突时）。"""
        if (
            self._autosave_enabled
            and self._editor_dirty
            and not self._disk_conflict
            and self._editing_path is not None
        ):
            self._autosave_timer.stop()
            self._autosave_now()

    def _autosave_now(self) -> None:
        if not self._autosave_enabled or self._disk_conflict:
            return
        if self._editing_path is None or not self._editor_dirty:
            return
        self.editor.get_content(self._autosave_write)

    def _autosave_write(self, after: str) -> None:
        p = self._editing_path
        if p is None:
            return
        if after == self._editing_loaded_text:
            self._editor_dirty = False
            return
        if p.exists():
            try:
                disk = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                disk = None
            if disk is not None and disk != self._editing_loaded_text:
                self._mark_disk_conflict()
                return
        self._write_editor_file(p, self._editing_loaded_text, after, "已自动保存")

    def _save_editor_now(self) -> None:
        """Ctrl+S / 手动立即保存（直接写盘，不进审阅队列）。"""
        p = self._editing_path
        if p is None:
            return
        self._autosave_timer.stop()
        if not self._confirm_overwrite_if_conflict():
            return

        def _cb(after: str) -> None:
            if after == self._editing_loaded_text:
                self._editor_dirty = False
                self.editor_title.setText(f"{p.name} · 无变化")
                return
            self._write_editor_file(p, self._editing_loaded_text, after, "已保存")

        self.editor.get_content(_cb)

    def _write_editor_file(self, p: Path, before: str, after: str, label: str) -> None:
        self._suppress_fs = True
        try:
            from agent.file_workspace import mark_read, save_backup

            if p.exists() and before:
                try:
                    save_backup(p, before)
                except Exception:
                    pass
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(after, encoding="utf-8", newline="\n")
            mark_read(p, after)
            from agent.read_cache import invalidate

            invalidate()
        except Exception as e:
            self.editor_title.setText(f"{p.name} · 保存失败: {e}")
            return
        finally:
            QTimer.singleShot(400, self._release_fs_suppress)
        self._editing_loaded_text = after
        self._editor_dirty = False
        self._disk_conflict = False
        from time import strftime

        self.editor_title.setText(f"{p.name} · {label} {strftime('%H:%M:%S')}")

    def _mark_disk_conflict(self) -> None:
        self._disk_conflict = True
        name = self._editing_path.name if self._editing_path else "文件"
        self.editor_title.setText(
            f"⚠ {name} 磁盘上已被 AI/外部修改，且你有未保存改动 —— "
            "Ctrl+S 覆盖磁盘版，或点「重载」放弃本地改动"
        )

    def _confirm_overwrite_if_conflict(self) -> bool:
        """磁盘被外部改过且缓冲区脏时，保存前确认覆盖。返回是否继续保存。"""
        p = self._editing_path
        if p is None:
            return False
        disk = None
        if p.exists():
            try:
                disk = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                disk = None
        if disk is None or disk == self._editing_loaded_text:
            return True
        ok = confirm(
            self,
            "保存冲突",
            f"{p.name} 在磁盘上已被 AI/外部修改。\n"
            "用你当前的编辑内容覆盖磁盘版本？\n"
            "（想先看最新内容请选否，再点「重载」——本地改动会丢失）",
            yes_text="覆盖",
        )
        if ok:
            self._disk_conflict = False
        return ok

    def _on_disk_file_changed(self, path: str) -> None:
        p = self._editing_path
        if p is None or str(p) != str(path):
            return
        # 写盘方式可能导致 watch 失联，重新挂上
        if p.exists() and str(p) not in self._fs_watcher.files():
            self._fs_watcher.addPath(str(p))
        if self._suppress_fs:
            return
        try:
            disk = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        except Exception:
            return
        if disk == self._editing_loaded_text:
            return
        if self._editor_dirty:
            self._mark_disk_conflict()
            return
        # 缓冲区干净：自动同步磁盘（AI 修改），保持光标位置
        state = self.editor.get_view_state()
        self._editor_lock = True
        self.editor.set_content(disk, lang_from_path(p))
        self._editor_lock = False
        self.editor.set_view_state(state)
        self._editing_loaded_text = disk
        self._editor_dirty = False
        self.editor_title.setText(f"{p.name} · 已同步磁盘改动（AI/外部）")

    def _pick_workspace(self):
        from agent.file_workspace import get_active_root, set_active_workspace

        start = str(get_active_root() or Path.home())
        path = QFileDialog.getExistingDirectory(self, "选择工作区文件夹", start)
        if not path:
            return
        try:
            set_active_workspace(path)
        except Exception as e:
            warn(self, "工作区", str(e))
            return
        self.refresh_workspace()
        self.workspace_changed.emit()

    # 最大化（当前屏 availableGeometry，适配任务栏/分屏）

    def is_zoomed(self) -> bool:
        return self._zoomed

    def _current_screen_avail(self) -> QRect:
        center = self.frameGeometry().center()
        return screen_geometry_at(center)

    def toggle_zoom(self):
        if self._zoomed:
            self.restore_from_zoom()
        else:
            self.zoom_to_screen()

    def _clear_wm_max_flags(self) -> None:
        """清掉 WM 最大化/全屏标志，避免 Linux 下 setGeometry 还原被忽略。"""
        st = self.windowState()
        cleared = st & ~Qt.WindowMaximized & ~Qt.WindowFullScreen
        if cleared != st:
            self.setWindowState(cleared)

    def zoom_to_screen(self):
        """铺满当前显示器的可用区（不含任务栏；多屏时只占窗口所在那一块）。"""
        if not self._zoomed:
            # 用 frameGeometry 记位置更稳；尺寸用客户区 geometry
            g = self.geometry()
            if g.width() < 200 or g.height() < 160:
                g = QRect(g.x(), g.y(), PANEL_W, PANEL_H)
            self._restore_geom = QRect(g)
        avail = self._current_screen_avail()
        self._zoomed = True
        self.btn_zoom.setText("❐")
        self.btn_zoom.setToolTip("还原窗口大小")
        self._root.setProperty("zoomed", "true")
        self._root.style().unpolish(self._root)
        self._root.style().polish(self._root)
        # 自定义铺满，不用 showMaximized（无边框窗还原时常失效）
        self._clear_wm_max_flags()
        self.setGeometry(avail)
        self._user_placed = True

    def restore_from_zoom(self, anchor_global: QPoint | None = None):
        self._zoomed = False
        self.btn_zoom.setText("□")
        self.btn_zoom.setToolTip("最大化到当前显示器工作区")
        self._root.setProperty("zoomed", "false")
        self._root.style().unpolish(self._root)
        self._root.style().polish(self._root)

        avail = (
            screen_geometry_at(anchor_global)
            if anchor_global is not None
            else self._current_screen_avail()
        )
        geom = QRect(self._restore_geom) if self._restore_geom else QRect()
        if not geom.isValid() or geom.width() < 320 or geom.height() < 240:
            geom = QRect(0, 0, PANEL_W, PANEL_H)
        # 若记忆尺寸已接近全屏，则退回默认工作台大小，避免「还原无感」
        if geom.width() >= avail.width() - 24 and geom.height() >= avail.height() - 24:
            geom.setWidth(min(PANEL_W, avail.width() - 48))
            geom.setHeight(min(PANEL_H, avail.height() - 48))

        if anchor_global is not None:
            w, h = geom.width(), geom.height()
            x = anchor_global.x() - int(w * 0.4)
            y = anchor_global.y() - 20
            x = max(avail.left(), min(x, avail.right() - w))
            y = max(avail.top(), min(y, avail.bottom() - h))
            geom = QRect(x, y, w, h)
        else:
            # 保证仍在当前屏内
            x = max(avail.left(), min(geom.x(), avail.right() - geom.width()))
            y = max(avail.top(), min(geom.y(), avail.bottom() - geom.height()))
            geom.moveTo(x, y)

        self._clear_wm_max_flags()
        # 先 move+resize 再 setGeometry，兼容部分 Wayland/Mutter 忽略单次 setGeometry
        try:
            self.showNormal()
        except Exception:
            pass
        self.resize(geom.size())
        self.move(geom.topLeft())
        self.setGeometry(geom)
        self._user_placed = True
        self._restore_geom = QRect(geom)

    # 边缘缩放

    def _hit_edges(self, pos: QPoint) -> int:
        if self._zoomed:
            return 0
        r = self.rect()
        e = 0
        if pos.x() <= EDGE:
            e |= _L
        if pos.x() >= r.width() - EDGE:
            e |= _R
        if pos.y() <= EDGE:
            e |= _T
        if pos.y() >= r.height() - EDGE:
            e |= _B
        return e

    def _cursor_for_edges(self, edges: int):
        left = edges & _L
        right = edges & _R
        top = edges & _T
        bottom = edges & _B
        if top and left:
            return Qt.SizeFDiagCursor
        if top and right:
            return Qt.SizeBDiagCursor
        if bottom and left:
            return Qt.SizeBDiagCursor
        if bottom and right:
            return Qt.SizeFDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        if top or bottom:
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and not self._zoomed:
            edges = self._hit_edges(event.position().toPoint())
            if edges:
                self._resize_edges = edges
                self._resize_origin = event.globalPosition().toPoint()
                self._resize_geom = QRect(self.geometry())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._resize_edges and self._resize_origin is not None and self._resize_geom:
            delta = event.globalPosition().toPoint() - self._resize_origin
            g = QRect(self._resize_geom)
            min_w, min_h = self.minimumWidth(), self.minimumHeight()
            if self._resize_edges & _L:
                new_left = g.left() + delta.x()
                if g.right() - new_left + 1 >= min_w:
                    g.setLeft(new_left)
            if self._resize_edges & _R:
                new_w = g.width() + delta.x()
                if new_w >= min_w:
                    g.setWidth(new_w)
            if self._resize_edges & _T:
                new_top = g.top() + delta.y()
                if g.bottom() - new_top + 1 >= min_h:
                    g.setTop(new_top)
            if self._resize_edges & _B:
                new_h = g.height() + delta.y()
                if new_h >= min_h:
                    g.setHeight(new_h)
            self.setGeometry(g)
            self._user_placed = True
            event.accept()
            return
        if not self._zoomed:
            edges = self._hit_edges(event.position().toPoint())
            self.setCursor(self._cursor_for_edges(edges))
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._resize_edges = 0
        self._resize_origin = None
        self._resize_geom = None
        super().mouseReleaseEvent(event)

    def _on_composer_send(self, text: str, attachments: list):
        if self._view_sid and self._view_sid != chat_history.get_active_id():
            chat_history.switch_session(self._view_sid)
            self.session_changed.emit(self._view_sid)
        self.send_requested.emit(text or "", list(attachments or []))

    def reload(self):
        self.refresh_workspace()
        self._reload_sessions()
        self._reload_chat(self._view_sid or chat_history.get_active_id())
        self.reload_edits()

    def _reload_sessions(self):
        active = chat_history.get_active_id()
        prefer = self._view_sid or active
        self.session_list.blockSignals(True)
        self.session_list.clear()
        for s in chat_history.list_sessions():
            sid = s.get("id") or ""
            title = s.get("title") or "对话"
            n = len(s.get("messages") or [])
            mark = "★ " if sid == active else ""
            item = QListWidgetItem(f"{mark}{title} ({n})")
            item.setData(Qt.UserRole, sid)
            self.session_list.addItem(item)
            if sid == prefer:
                self.session_list.setCurrentItem(item)
        self.session_list.blockSignals(False)
        s = chat_history.get_active_session()
        self.title.setText(f"Agent 工作台 · {s.get('title') or '对话'}")

    def _reload_chat(self, sid: str | None):
        self._view_sid = sid or chat_history.get_active_id()
        msgs = chat_history.list_messages(200, session_id=self._view_sid)
        self.msg_list.set_messages(msgs)

    def _on_msg_feedback(self, message_id: str, rating: str):
        """工作台消息赞踩 → prompt_store 反馈。"""
        from agent import prompt_store

        msgs = chat_history.list_messages(200, session_id=self._view_sid)
        target = None
        prev_user = ""
        for m in msgs:
            if m.get("role") == "user":
                prev_user = str(m.get("text") or "")
            if str(m.get("id") or "") == str(message_id):
                target = m
                break
        note = ""
        if rating == "down":
            note, ok = ask_multiline(
                self,
                "反馈原因",
                "可选：说明哪里不好（用于后续改写 Prompt）",
                text="",
            )
            if not ok:
                return
            note = (note or "").strip()
        prompt_store.add_feedback(
            rating=rating,
            message_id=message_id,
            session_id=self._view_sid,
            user_note=note,
            assistant_preview=str((target or {}).get("text") or ""),
            user_preview=prev_user,
        )
        tip = "已记录点赞" if rating == "up" else "已记录点踩（可在 Prompt 设置里生成改写）"
        self.badge.setText(tip)

    def append_assistant(self, text: str):
        # 历史已由 main 写入；刷新列表即可（流式卡会被清掉）
        _ = text
        self._reload_chat(self._view_sid)

    def begin_stream(self):
        self.msg_list.begin_assistant_stream()

    def handle_stream_event(self, ev: dict):
        self.msg_list.handle_stream_event(ev if isinstance(ev, dict) else {})

    def finalize_stream(self) -> dict:
        return self.msg_list.finalize_assistant_stream()

    def _on_session_clicked(self, item: QListWidgetItem):
        if self._switching or not item:
            return
        sid = str(item.data(Qt.UserRole) or "")
        if not sid:
            return
        self._open_session(sid)

    def _open_selected_session(self):
        item = self.session_list.currentItem()
        if item:
            self._on_session_clicked(item)

    def _open_session(self, sid: str):
        self._switching = True
        try:
            if sid != chat_history.get_active_id():
                chat_history.switch_session(sid)
                self.session_changed.emit(sid)
            self._view_sid = sid
            self._reload_chat(sid)
            self._reload_sessions()
            s = chat_history.get_active_session()
            self.title.setText(f"Agent 工作台 · {s.get('title') or '对话'}")
        finally:
            self._switching = False

    def _session_menu(self, pos):
        from PySide6.QtWidgets import QMenu

        item = self.session_list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        act_open = menu.addAction("打开")
        act_del = menu.addAction("删除")
        chosen = menu.exec(self.session_list.mapToGlobal(pos))
        if chosen is act_open:
            self._on_session_clicked(item)
        elif chosen is act_del:
            self._delete_session(str(item.data(Qt.UserRole) or ""))

    def _delete_selected_session(self):
        item = self.session_list.currentItem()
        if item:
            self._delete_session(str(item.data(Qt.UserRole) or ""))

    def _delete_session(self, sid: str):
        if not sid:
            return
        if not confirm(
            self,
            "删除对话",
            "确定删除该对话？不可恢复。",
            yes_text="删除",
            danger=True,
        ):
            return
        try:
            from agent.goal_store import clear_goal

            clear_goal(session_id=sid)
        except Exception:
            pass
        chat_history.delete_session(sid)
        self._view_sid = chat_history.get_active_id()
        self.session_changed.emit(self._view_sid or "")
        self.reload()

    def reload_edits(self):
        pending = list_pending()
        keep_id = self._current_edit_id
        keep_hunk = self._focus_hunk_id

        # 移除已不存在的 edit 标签
        self._switching = True
        existing_eids = {e.get("id") for e in pending}
        for i in range(len(self._tab_data) - 1, -1, -1):
            td = self._tab_data[i]
            if td.get("type") == "edit" and td.get("edit_id") not in existing_eids:
                self._tab_data.pop(i)
                self.file_tabs.removeTab(i)

        total_hunks = 0
        first_edit_tab = -1
        for e in pending:
            hunks = e.get("hunks") or []
            left = sum(1 for h in hunks if h.get("status") == "pending")
            total_hunks += left
            name = Path(e.get("path") or "").name
            eid = e.get("id")
            idx = self._find_tab(edit_id=eid)
            if idx >= 0:
                self.file_tabs.setTabText(idx, f"🔴 {name} ({left}段)")
            else:
                self._tab_data.append({"type": "edit", "edit_id": eid})
                idx = self.file_tabs.addTab(f"🔴 {name} ({left}段)")
            if first_edit_tab < 0:
                first_edit_tab = idx

        self._switching = False

        self.badge.setText(
            f"待确认 {total_hunks} 段" if total_hunks else "无待确认改动"
        )
        if pending:
            target_tab = -1
            if keep_id:
                target_tab = self._find_tab(edit_id=keep_id)
            if target_tab < 0:
                target_tab = first_edit_tab
            if target_tab >= 0:
                self.file_tabs.setCurrentIndex(target_tab)
                td = self._tab_data[target_tab]
                eid = td.get("edit_id", "")
                self._current_edit_id = eid
                self._show_edit(eid, prefer_hunk=keep_hunk if keep_id == eid else None)
                self._set_code_panel_mode("diff")
        else:
            self._current_edit_id = None
            self._focus_hunk_id = None
            self.diff_panel.clear()
            self.diff_meta.setText("")
            if self._code_panel_mode == "diff":
                self._set_code_panel_mode("edit")
        try:
            self.msg_list._restore_pending_approvals()
        except Exception:
            pass

    def _show_edit(self, edit_id: str, prefer_hunk: int | None = None):
        self._current_edit_id = edit_id
        e = get_edit(edit_id)
        if not e:
            return
        hunks = e.get("hunks") or []
        pending_ids = [int(h["id"]) for h in hunks if h.get("status") == "pending"]
        if prefer_hunk is not None and prefer_hunk in pending_ids:
            self._focus_hunk_id = prefer_hunk
        elif self._focus_hunk_id in pending_ids:
            pass
        elif pending_ids:
            self._focus_hunk_id = pending_ids[0]
        elif hunks:
            self._focus_hunk_id = int(hunks[0]["id"])
        else:
            self._focus_hunk_id = None

        cache = e.get("cache_path") or ""
        self.diff_meta.setText(
            f"{e.get('path')}\n{e.get('summary')} · {e.get('ts')}"
            + (f"\n缓存: {cache}" if cache else "")
        )
        self.diff_panel.set_diff(
            e.get("before") or "",
            e.get("after") or "",
            hunks,
            self._focus_hunk_id,
        )
        # 同步路径标题（不强制切编辑内容，避免冲掉草稿）
        path = e.get("path") or ""
        if path:
            name = Path(path).name
            if self._code_panel_mode == "diff":
                self.editor_title.setText(f"{name} · 改动预览")


    def _on_edit_select(self, edit_id: str):
        if not edit_id:
            return
        self._focus_hunk_id = None
        self._show_edit(edit_id)
        self._set_code_panel_mode("diff")

    def _on_new_agent(self):
        title, ok = ask_text(
            self,
            "新对话",
            "给这次对话起个名字（可随时在左侧列表重命名）：",
            text="新对话",
            placeholder="例如：修登录页 / 整理笔记",
            ok_text="创建",
        )
        if not ok:
            return
        title = (title or "").strip() or "新对话"
        s = chat_history.create_session(title, activate=True)
        self._view_sid = s["id"]
        self.session_changed.emit(s["id"])
        self.reload()

    def _on_send(self):
        self.composer._on_send()

    def _on_inline_keep(self, hunk_id: int):
        if not self._current_edit_id:
            return
        self._focus_hunk_id = hunk_id
        decide_hunk(self._current_edit_id, hunk_id, keep=True)
        nxt = first_pending_hunk_id(self._current_edit_id)
        self._focus_hunk_id = nxt
        self.reload_edits()

    def _on_inline_discard(self, hunk_id: int):
        if not self._current_edit_id:
            return
        self._focus_hunk_id = hunk_id
        decide_hunk(self._current_edit_id, hunk_id, keep=False)
        nxt = first_pending_hunk_id(self._current_edit_id)
        self._focus_hunk_id = nxt
        self.reload_edits()

    def _save_current(self):
        """保留当前连续改动段。"""
        if not self._current_edit_id or self._focus_hunk_id is None:
            return
        decide_hunk(self._current_edit_id, self._focus_hunk_id, keep=True)
        nxt = first_pending_hunk_id(self._current_edit_id)
        self._focus_hunk_id = nxt
        self.reload_edits()

    def _reject_current(self):
        """放弃当前连续改动段。"""
        if not self._current_edit_id or self._focus_hunk_id is None:
            return
        decide_hunk(self._current_edit_id, self._focus_hunk_id, keep=False)
        nxt = first_pending_hunk_id(self._current_edit_id)
        self._focus_hunk_id = nxt
        self.reload_edits()

    def _save_all_file(self):
        """保留当前文件剩余全部段。"""
        if not self._current_edit_id:
            return
        apply_edit(self._current_edit_id)
        self.reload_edits()

    def _reject_all_file(self):
        """放弃当前文件剩余全部段。"""
        if not self._current_edit_id:
            return
        reject_edit(self._current_edit_id)
        self.reload_edits()

    def _save_all(self):
        apply_all()
        self.reload_edits()

    def _reject_all(self):
        reject_all()
        self.reload_edits()

    def set_busy(self, busy: bool):
        self.composer.set_busy(busy)

    def refresh_identity(self):
        """产品名 / 昵称变更后刷新顶栏。"""
        try:
            from agent.identity import display_name, product_name

            pname = product_name()
            dname = display_name()
            if dname and dname != pname:
                self.title.setText(f"{pname} · {dname}")
            else:
                self.title.setText(f"{pname} 工作台")
        except Exception:
            self.title.setText("Mini_Lu 工作台")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.isVisible() and not self._zoomed and not getattr(self, "_layout_loading", False):
            self._schedule_save_layout()

    def show_panel(self):
        from agent.hover_tip import prepare_toplevel_show

        prepare_toplevel_show(self)
        self.refresh_identity()
        self.reload()
        self.refresh_workspace()
        self.show()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        append_usage_event("open_studio")

    def collapse_to_chat(self):
        """收起：关闭大窗并通知主程序打开小输入条。"""
        self._flush_autosave()
        self._save_layout_now()
        if self._zoomed:
            self.restore_from_zoom()
        self.hide()
        self.collapse_requested.emit()
        append_usage_event("collapse_studio")

    def hide_panel(self):
        """× 关闭：不自动打开小输入条。"""
        self._flush_autosave()
        self._save_layout_now()
        if self._zoomed:
            self.restore_from_zoom()
        self.hide()
        self.closed.emit()
        append_usage_event("close_studio")

    def apply_font_zoom(self) -> None:
        """Ctrl+滚轮后刷新聊天、待确认改动、改动预览、目录树字号。"""
        try:
            from agent.message_view import refresh_font_sizes

            refresh_font_sizes()
        except Exception:
            pass
        # 重建 QSS（sectionHead / meta / QListWidget 字号都在样式表里）
        try:
            self._apply_chrome()
        except Exception:
            pass
        try:
            self.composer.apply_font_zoom()
        except Exception:
            pass
        try:
            self._reload_chat(self._view_sid or chat_history.get_active_id())
        except Exception:
            pass
        try:
            self.diff_panel.apply_font_zoom()
        except Exception:
            pass
        try:
            self.file_tree.apply_font_zoom()
        except Exception:
            pass

    def get_draft_text(self) -> str:
        return self.composer.get_draft_text()

    def set_draft_text(self, text: str) -> None:
        self.composer.set_draft_text(text)

    def set_rewind_mode(self, on: bool, preview: str = "") -> None:
        self.composer.set_rewind_mode(on, preview)
        if on:
            try:
                self.input.setFocus()
            except Exception:
                pass

    def place_near(self, global_x: int, global_y: int, pet_w: int = 200, pet_h: int = 260):
        """仅在尚未由用户定位、且窗口未显示时贴宠物旁；已打开则绝不挪动。"""
        if self._user_placed or self._zoomed or self.isVisible():
            return
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        w = min(self.width(), screen.width() - 24)
        h = min(self.height(), screen.height() - 24)
        self.resize(w, h)
        x = max(screen.left() + 8, min(global_x - 40, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(global_y - 60, screen.bottom() - self.height() - 8))
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
