"""
桌面宠物主程序 v2
基于 PySide6 实现
改进：图片自适应缩放、小范围踱步、多皮肤系统、流畅动画
"""
import sys
import random

from pathlib import Path
from PySide6.QtWidgets import (QApplication, QWidget, QMenu,
                               QSystemTrayIcon)
from PySide6.QtGui import (QPixmap, QImage, QMouseEvent, QAction, QIcon,
                           QPainter, QRegion, QColor, QGuiApplication)
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal, QObject, QEvent

from agent.agent_runner import AgentRunner
from agent import chat_history
from agent.chat_bubble import BubbleLane, display_ms_for_text
from agent.chat_history_panel import ChatHistoryPanel
from agent.chat_panel import ChatPanel
from agent.hover_tip import HoverTip
from agent.memory_panel import MemoryPanel
from agent.notes_panel import NotesPanel
from agent.prompt_panel import PromptPanel
from agent.quotes_panel import QuotesPanel
from agent.models_panel import ModelsPanel
from agent.extensions_panel import ExtensionsPanel
from agent.ui_bridge import init_bridge
from agent.workspace_panel import WorkspacePanel
from agent.agent_studio import AgentStudio
from agent import quotes_store
from agent.file_workspace import get_active_root
from agent.edit_staging import set_review_enabled


class _CtrlWheelZoomFilter(QObject):
    """全局 Ctrl+滚轮缩放聊天/输入字号。"""

    def eventFilter(self, obj, event):
        if event.type() != QEvent.Type.Wheel:
            return False
        if not (event.modifiers() & Qt.ControlModifier):
            return False
        dy = event.angleDelta().y()
        if dy == 0:
            return False
        try:
            from agent import ui_zoom

            if not ui_zoom.bump(1 if dy > 0 else -1):
                return True
            app = QApplication.instance()
            if app is not None:
                for w in app.topLevelWidgets():
                    fn = getattr(w, "apply_ui_font_zoom", None)
                    if callable(fn):
                        try:
                            fn()
                        except Exception:
                            pass
            return True
        except Exception:
            return False


def app_dir() -> Path:
    """开发时用源码目录；打包成 exe 后用 exe 所在目录（便于连同 assets 一起拷贝）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# 资源路径
ASSETS_DIR = app_dir() / "assets"
SKINS_DIR = ASSETS_DIR / "skins"

# 窗口尺寸
WINDOW_WIDTH = 200
WINDOW_HEIGHT = 260

# 显示时人物目标高度（窗口内），保证各帧视觉大小一致
DISPLAY_PERSON_HEIGHT = 230
ALPHA_THRESHOLD = 10

# 各动作的帧间隔（毫秒），数值越小越快
FRAME_INTERVAL = {
    "idle": 300,       # 待机：慢，呼吸眨眼
    "walk_left": 120,  # 行走
    "walk_right": 120,
    "happy": 150,      # 开心：中等
}


class ClickCaptureOverlay(QWidget):
    """全屏近乎透明层：捕获下一次左键点击的屏幕坐标（仅取 X 用于横向移动）。"""
    clicked = Signal(QPoint)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self._cover_virtual_desktop()

    def _cover_virtual_desktop(self):
        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        if geo.isNull():
            geo = QApplication.primaryScreen().geometry()
        self.setGeometry(geo)

    def paintEvent(self, event):
        # 极低透明度，保证能收到鼠标事件，同时几乎看不见
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 120, 215, 18))

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(event.globalPosition().toPoint())
            event.accept()
        elif event.button() == Qt.RightButton:
            self.cancelled.emit()
            event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class DesktopPet(QWidget):
    def __init__(self):
        super().__init__()
        
        # 皮肤系统
        self.available_skins = self.discover_skins()
        self.current_skin = self.available_skins[0] if self.available_skins else "default"
        
        # 家位置（小范围活动的中心点）
        self.home_x = None
        self.walk_range = 80  # 在家位置左右各80px范围内活动
        
        self.init_window()
        self.init_animations()
        self.init_behavior()
        self.init_tray()
        
        # 初始状态
        self.current_state = "idle"
        self.current_frame = 0
        self.update_image()
        
        # 动画定时器，初始为待机速度
        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.next_frame)
        self.anim_timer.start(FRAME_INTERVAL["idle"])
        
        # 行为定时器（自动踱步决策，间隔加长以降低打扰）
        self.behavior_timer = QTimer(self)
        self.behavior_timer.timeout.connect(self.random_behavior)
        self.behavior_timer.start(5000)
        
        # 移动相关
        self.walk_speed = 2
        self.walk_target = None
        self.user_goto = False  # 用户点选移动中，不受自动踱步打断
        self.move_timer = QTimer(self)
        self.move_timer.timeout.connect(self.walk_step)
        self.move_timer.start(40)
        
        # 拖拽相关
        self.is_dragging = False
        self._drag_moved = False
        self.drag_offset = QPoint()
        self._press_global = QPoint()
        
        # 点选移动：仅右键菜单「点选横向移动…」触发，避免单击误触
        self.goto_overlay: ClickCaptureOverlay | None = None

        # Agent 聊天 + 记事面板 + 冒泡
        self.chat_panel = ChatPanel()
        self.chat_panel.send_requested.connect(self.on_chat_send)
        self.chat_panel.closed.connect(self.on_chat_closed)
        self.chat_panel.moved_by_user.connect(self._sync_bubble_avoid)
        self.chat_panel.workspace_requested.connect(self.open_workspace_panel)
        self.chat_panel.new_agent_requested.connect(self.create_new_agent)
        self.chat_panel.agents_requested.connect(self.open_history_panel)
        self.chat_panel.expand_requested.connect(self.open_agent_studio)
        self.chat_panel.stop_requested.connect(self.on_agent_stop)
        self.chat_panel.rewind_cancel_requested.connect(self.cancel_rewind_edit)
        self.notes_panel = NotesPanel()
        self.notes_panel.closed.connect(self.on_notes_closed)
        self.memory_panel = MemoryPanel()
        self.memory_panel.closed.connect(self.on_memory_closed)
        self.workspace_panel = WorkspacePanel()
        self.workspace_panel.closed.connect(self.on_workspace_closed)
        self.workspace_panel.changed.connect(self._on_workspace_changed)
        self.quotes_panel = QuotesPanel()
        self.quotes_panel.closed.connect(self.on_quotes_closed)
        self.quotes_panel.settings_changed.connect(self._restart_quote_timer)
        self.prompt_panel = PromptPanel()
        self.prompt_panel.closed.connect(self.on_prompt_closed)
        self.prompt_panel.prompt_changed.connect(self.on_prompt_changed)
        self.models_panel = ModelsPanel()
        self.models_panel.closed.connect(self.on_models_closed)
        self.models_panel.models_changed.connect(self.on_models_changed)
        self.extensions_panel = ExtensionsPanel()
        self.extensions_panel.closed.connect(self.on_extensions_closed)
        self.extensions_panel.extensions_changed.connect(self.on_extensions_changed)
        self.history_panel = ChatHistoryPanel()
        self.history_panel.closed.connect(self.on_history_closed)
        self.history_panel.session_changed.connect(self.on_session_changed)
        self.agent_studio = AgentStudio()
        self.agent_studio.closed.connect(self.on_studio_closed)
        self.agent_studio.collapse_requested.connect(self.on_studio_collapse)
        self.agent_studio.send_requested.connect(self.on_studio_send)
        self.agent_studio.new_agent_requested.connect(self.create_new_agent)
        self.agent_studio.session_changed.connect(self.on_session_changed)
        self.agent_studio.workspace_requested.connect(self.open_workspace_panel)
        self.agent_studio.workspace_changed.connect(self._on_workspace_changed)
        self.agent_studio.extensions_requested.connect(self.open_extensions_panel)
        self.agent_studio.models_changed.connect(self.on_models_changed)
        self.agent_studio.rewind_requested.connect(self.on_rewind_from_message)
        self.agent_studio.retry_requested.connect(self.on_retry_from_message)
        self.agent_studio.stop_requested.connect(self.on_agent_stop)
        self.agent_studio.rewind_cancel_requested.connect(self.cancel_rewind_edit)
        self.bubble_lane = BubbleLane()
        self.bubble_lane.on_open_full = self.on_bubble_open_full
        self.hover_tip = HoverTip()
        self.setMouseTracking(True)
        self.agent_runner = AgentRunner(self)
        self.agent_runner.reply_ready.connect(self.on_agent_reply)
        self.agent_runner.error.connect(self.on_agent_error)
        self.agent_runner.cancelled.connect(self.on_agent_cancelled)
        self.agent_runner.busy_changed.connect(self.on_agent_busy)
        self.agent_runner.stream_event.connect(self.on_agent_stream_event)
        self._chat_open = False
        self._notes_open = False
        self._memory_open = False
        self._workspace_open = False
        self._studio_open = False
        self._quotes_open = False
        self._prompt_open = False
        self._models_open = False
        self._extensions_open = False
        self._history_open = False
        self._rewind_anchor_id: str | None = None

        self.ui_bridge = init_bridge(self)
        self.ui_bridge.open_notes.connect(self.open_notes_panel)
        self.ui_bridge.open_memory.connect(self.open_memory_panel)
        self.ui_bridge.open_workspace.connect(self.open_workspace_panel)
        self.ui_bridge.open_agent_studio.connect(self.open_agent_studio)
        self.ui_bridge.open_prompt.connect(self.open_prompt_panel)
        self.ui_bridge.edits_changed.connect(self.on_edits_changed)
        self.ui_bridge.show_bubble.connect(self.show_reminder_bubble)
        self.ui_bridge.agent_ui_event.connect(self.on_agent_ui_event)
        self._refresh_workspace_tooltip()
        set_review_enabled(True)

        # 单击延迟：避免双击打开聊天时误触发单击互动
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self.on_click)
        
        # 互动恢复定时器
        self.happy_timer = QTimer(self)
        self.happy_timer.setSingleShot(True)
        self.happy_timer.timeout.connect(self.back_to_idle)

        # 提醒轮询（到期 UI 冒泡）
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(1000)

        # 待机语录
        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self.maybe_say_quote)
        self._restart_quote_timer()

    def discover_skins(self) -> list[str]:
        """发现可用皮肤：仅维护 Q版卡通"""
        preferred = "Q版卡通"
        if not SKINS_DIR.exists():
            return ["default"]
        skins = [d.name for d in SKINS_DIR.iterdir() if d.is_dir()]
        # 只启用 Q版；真人风格不再维护
        if preferred in skins:
            return [preferred]
        q_like = [s for s in skins if "Q" in s or "q" in s]
        return q_like if q_like else (skins if skins else ["default"])

    def init_window(self):
        """初始化窗口：透明、无边框、置顶"""
        # 注意：先设flags再设attribute，Windows下顺序很重要
        self.setWindowFlags(
            Qt.FramelessWindowHint |    # 无边框
            Qt.WindowStaysOnTopHint |   # 始终置顶
            Qt.Tool                     # 不在任务栏显示
        )
        self.setAttribute(Qt.WA_TranslucentBackground)  # 背景透明
        self.setAttribute(Qt.WA_NoSystemBackground)     # 无系统背景
        self.setFixedSize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setToolTip("")  # 不用系统灰框提示

        # 初始位置：屏幕右下角
        screen = QApplication.primaryScreen().geometry()
        initial_x = screen.width() - WINDOW_WIDTH - 50
        initial_y = screen.height() - WINDOW_HEIGHT - 100
        self.move(initial_x, initial_y)
        self.home_x = initial_x

    def paintEvent(self, event):
        """直接用QPainter绘制图片，确保透明背景正常工作"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        frames = self.animations.get(self.current_state, self.animations["idle"])
        if frames and self.current_frame < len(frames):
            pix = frames[self.current_frame]
            if not pix.isNull():
                # normalize_frame 已输出窗口画布；旧素材则底部居中兜底
                if pix.width() == WINDOW_WIDTH and pix.height() == WINDOW_HEIGHT:
                    painter.drawPixmap(0, 0, pix)
                else:
                    x = (WINDOW_WIDTH - pix.width()) // 2
                    y = WINDOW_HEIGHT - pix.height() - 5
                    painter.drawPixmap(x, y, pix)

    def init_animations(self):
        """加载当前皮肤的所有动作帧"""
        idle = self.load_frames("idle")
        walk_left = self.load_frames("walk_left")
        walk_right = self.load_frames("walk_right")

        # 右走缺失或与左走帧数不一致时，用左走水平镜像生成（仅改朝向）
        if walk_left and (not walk_right or len(walk_right) != len(walk_left)
                          or all(p.isNull() for p in walk_right)):
            walk_right = [QPixmap.fromImage(
                f.toImage().mirrored(True, False)
            ) for f in walk_left]

        self.animations = {
            "idle": idle,
            "walk_left": walk_left,
            "walk_right": walk_right,
            "happy": self.load_frames("happy"),
        }

    @staticmethod
    def content_bbox(image: QImage) -> QRect | None:
        """根据 alpha 通道计算人物内容包围盒（缩略扫描，启动更快）"""
        if image.isNull():
            return None
        img = image.convertToFormat(QImage.Format_ARGB32)
        factor = max(1, min(img.width(), img.height()) // 320)
        small = img.scaled(
            max(1, img.width() // factor),
            max(1, img.height() // factor),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        min_x, min_y = small.width(), small.height()
        max_x, max_y = -1, -1
        for y in range(small.height()):
            for x in range(small.width()):
                if (small.pixel(x, y) >> 24) & 0xFF > ALPHA_THRESHOLD:
                    if x < min_x:
                        min_x = x
                    if x > max_x:
                        max_x = x
                    if y < min_y:
                        min_y = y
                    if y > max_y:
                        max_y = y
        if max_x < min_x or max_y < min_y:
            return None
        # 映射回原图像素，并向外扩一点以免裁切到边缘
        pad = factor
        left = max(0, min_x * factor - pad)
        top = max(0, min_y * factor - pad)
        right = min(img.width(), (max_x + 1) * factor + pad)
        bottom = min(img.height(), (max_y + 1) * factor + pad)
        return QRect(left, top, right - left, bottom - top)

    def normalize_frame(self, pix: QPixmap) -> QPixmap:
        """按人物内容高度统一缩放，底部对齐到固定画布，避免忽大忽小"""
        if pix.isNull():
            return pix

        image = pix.toImage()
        bbox = self.content_bbox(image)
        if bbox is None or bbox.height() <= 0:
            return pix.scaled(
                WINDOW_WIDTH, WINDOW_HEIGHT,
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )

        person = QPixmap.fromImage(image.copy(bbox))
        scale = DISPLAY_PERSON_HEIGHT / bbox.height()
        new_w = max(1, int(round(bbox.width() * scale)))
        new_h = DISPLAY_PERSON_HEIGHT

        # 不超过窗口
        if new_w > WINDOW_WIDTH - 4:
            fit = (WINDOW_WIDTH - 4) / new_w
            new_w = max(1, int(round(new_w * fit)))
            new_h = max(1, int(round(new_h * fit)))

        person = person.scaled(new_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        canvas = QPixmap(WINDOW_WIDTH, WINDOW_HEIGHT)
        canvas.fill(Qt.transparent)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        x = (WINDOW_WIDTH - new_w) // 2
        y = WINDOW_HEIGHT - new_h - 5
        painter.drawPixmap(x, y, person)
        painter.end()
        return canvas

    def load_frames(self, action_name: str) -> list[QPixmap]:
        """加载某个动作的所有帧，并按人物内容统一大小"""
        frames = []

        action_dir = SKINS_DIR / self.current_skin / action_name
        if not action_dir.exists():
            action_dir = ASSETS_DIR / action_name

        if action_dir.exists():
            png_files = sorted(action_dir.glob("*.png"))
            for f in png_files:
                pix = QPixmap(str(f))
                if not pix.isNull():
                    frames.append(self.normalize_frame(pix))

        if not frames:
            frames.append(QPixmap())
        return frames

    def reload_all_animations(self):
        """切换皮肤后重新加载所有动画帧"""
        self.init_animations()
        self.current_frame = 0
        self.update_image()
        # 更新托盘图标
        if self.animations["idle"] and not self.animations["idle"][0].isNull():
            self.tray_icon.setIcon(QIcon(self.animations["idle"][0]))

    def init_behavior(self):
        """初始化行为参数"""
        # 无人操作时很少自己踱步（原 0.5；现约每 5 秒 8%）
        self.walk_chance = 0.08
        self.happy_duration = 1200  # 开心状态持续1.2秒
        self.goto_pick_armed = False
        self.goto_walk_speed = 4
        self.idle_walk_speed = 2
        self.walk_speed = self.idle_walk_speed

    def start_goto_pick(self):
        """右键「点选横向移动」：打开全屏层，等待屏幕点击作为横向目标。"""
        self.cancel_goto_pick()
        self.goto_pick_armed = True
        overlay = ClickCaptureOverlay()
        overlay.clicked.connect(self.on_goto_point_picked)
        overlay.cancelled.connect(self.cancel_goto_pick)
        self.goto_overlay = overlay
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.setFocus()
        self.tray_icon.showMessage(
            "点选移动",
            "请点击屏幕上的目标位置（仅左右移动）。右键或 Esc 取消。",
            QSystemTrayIcon.Information,
            2500,
        )

    def cancel_goto_pick(self):
        """关闭点选层"""
        self.goto_pick_armed = False
        if self.goto_overlay is not None:
            overlay = self.goto_overlay
            self.goto_overlay = None
            overlay.hide()
            overlay.close()
            overlay.deleteLater()

    def on_goto_point_picked(self, global_pos: QPoint):
        """只取点击的全局 X，桌宠横向走到该处（窗口中心对齐点击 X）。"""
        self.cancel_goto_pick()
        self.happy_timer.stop()

        geo = QRect()
        for screen in QGuiApplication.screens():
            geo = geo.united(screen.geometry())
        if geo.isNull():
            geo = QApplication.primaryScreen().geometry()

        target_x = int(global_pos.x() - WINDOW_WIDTH // 2)
        target_x = max(geo.left(), min(target_x, geo.right() - WINDOW_WIDTH + 1))

        if abs(target_x - self.x()) < 3:
            self.set_home_x(target_x)
            self.change_state("idle")
            return

        self.user_goto = True
        self.walk_speed = self.goto_walk_speed
        self.walk_target = target_x
        if target_x < self.x():
            self.change_state("walk_left")
        else:
            self.change_state("walk_right")

    def init_tray(self):
        """初始化系统托盘"""
        self.tray_icon = QSystemTrayIcon(self)
        if self.animations["idle"] and not self.animations["idle"][0].isNull():
            self.tray_icon.setIcon(QIcon(self.animations["idle"][0]))
        
        tray_menu = QMenu()
        
        # 显示/隐藏
        show_action = QAction("显示 Mini_Lu", self)
        show_action.triggered.connect(self.show)
        tray_menu.addAction(show_action)
        
        hide_action = QAction("隐藏 Mini_Lu", self)
        hide_action.triggered.connect(self.hide)
        tray_menu.addAction(hide_action)
        
        tray_menu.addSeparator()

        chat_action = QAction("打开聊天…", self)
        chat_action.triggered.connect(self.open_chat)
        tray_menu.addAction(chat_action)

        rename_action = QAction("给它取名…", self)
        rename_action.triggered.connect(self.rename_agent)
        tray_menu.addAction(rename_action)

        notes_action = QAction("查看记事内容", self)
        notes_action.triggered.connect(self.open_notes_panel)
        tray_menu.addAction(notes_action)

        mem_action = QAction("查看记忆", self)
        mem_action.triggered.connect(self.open_memory_panel)
        tray_menu.addAction(mem_action)

        quotes_action = QAction("待机语录…", self)
        quotes_action.triggered.connect(self.open_quotes_panel)
        tray_menu.addAction(quotes_action)

        prompt_action = QAction("Prompt 设置…", self)
        prompt_action.triggered.connect(self.open_prompt_panel)
        tray_menu.addAction(prompt_action)

        models_action = QAction("模型设置…", self)
        models_action.triggered.connect(self.open_models_panel)
        tray_menu.addAction(models_action)

        ext_action = QAction("扩展（MCP/Skills）…", self)
        ext_action.triggered.connect(self.open_extensions_panel)
        tray_menu.addAction(ext_action)

        hist_action = QAction("聊天记录…", self)
        hist_action.triggered.connect(self.open_history_panel)
        tray_menu.addAction(hist_action)

        new_agent_action = QAction("新对话（New Agent）", self)
        new_agent_action.triggered.connect(self.create_new_agent)
        tray_menu.addAction(new_agent_action)

        studio_action = QAction("Agent 工作台…", self)
        studio_action.triggered.connect(self.open_agent_studio)
        tray_menu.addAction(studio_action)

        ws_action = QAction("工作区…", self)
        ws_action.triggered.connect(self.open_workspace_panel)
        tray_menu.addAction(ws_action)

        goto_action = QAction("点选横向移动…", self)
        goto_action.triggered.connect(self.start_goto_pick)
        tray_menu.addAction(goto_action)

        tray_menu.addSeparator()
        
        # 皮肤切换子菜单
        skin_menu = tray_menu.addMenu("切换皮肤")

        
        for skin_name in self.available_skins:
            action = QAction(skin_name, self)
            action.triggered.connect(lambda checked, name=skin_name: self.switch_skin(name))
            skin_menu.addAction(action)
        
        tray_menu.addSeparator()
        
        # 退出
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(QApplication.quit)
        tray_menu.addAction(quit_action)
        
        self.tray_icon.setContextMenu(tray_menu)
        self._refresh_tray_tooltip()
        self.tray_icon.show()

    def _refresh_tray_tooltip(self):
        try:
            from agent.identity import display_name, product_name

            name = display_name()
            tip = product_name() if name == product_name() else f"{product_name()} · {name}"
        except Exception:
            tip = "Mini_Lu"
        if hasattr(self, "tray_icon") and self.tray_icon:
            self.tray_icon.setToolTip(tip)

    def switch_skin(self, skin_name: str):
        """切换皮肤"""
        if skin_name in self.available_skins and skin_name != self.current_skin:
            self.current_skin = skin_name
            self.reload_all_animations()

    def update_image(self):
        """触发重绘并更新窗口形状掩码"""
        self.update()
        self.update_window_mask()

    def update_window_mask(self):
        """根据当前图片设置窗口形状，实现真正的透明（无任何背景）"""
        frames = self.animations.get(self.current_state, self.animations["idle"])
        if frames and self.current_frame < len(frames):
            pix = frames[self.current_frame]
            if not pix.isNull() and pix.hasAlphaChannel():
                mask = pix.mask()
                region = QRegion(mask)
                if not (pix.width() == WINDOW_WIDTH and pix.height() == WINDOW_HEIGHT):
                    x = (WINDOW_WIDTH - pix.width()) // 2
                    y = WINDOW_HEIGHT - pix.height() - 5
                    region.translate(x, y)
                self.setMask(region)

    def next_frame(self):
        """切换到下一帧（动画循环）"""
        frames = self.animations.get(self.current_state, self.animations["idle"])
        if frames and len(frames) > 1:
            self.current_frame = (self.current_frame + 1) % len(frames)
            self.update_image()

    def change_state(self, new_state: str):
        """切换动作状态，并调整动画帧率"""
        if self.current_state != new_state:
            self.current_state = new_state
            self.current_frame = 0
            interval = FRAME_INTERVAL.get(new_state, 200)
            self.anim_timer.setInterval(interval)
            self.update_image()

    def back_to_idle(self):
        """恢复待机状态"""
        if self.user_goto and self.walk_target is not None:
            return
        self.change_state("idle")
        if not self.user_goto:
            self.walk_target = None

    def random_behavior(self):
        """随机行为：小范围内踱步（低概率），不大范围乱跑"""
        if self.current_state == "happy" or self.is_dragging:
            return
        if self.goto_pick_armed or self.user_goto:
            return
        if self._chat_open or self.agent_runner.busy:
            return
        if self.walk_target:
            return

        if random.random() < self.walk_chance:
            self.walk_speed = self.idle_walk_speed
            offset = random.randint(-self.walk_range, self.walk_range)
            target_x = self.home_x + offset

            screen = QApplication.primaryScreen().geometry()
            target_x = max(20, min(target_x, screen.width() - WINDOW_WIDTH - 20))

            if target_x < self.x():
                self.change_state("walk_left")
            else:
                self.change_state("walk_right")

            self.walk_target = target_x
        else:
            self.change_state("idle")
            self.walk_target = None

    def set_home_x(self, x: int | None = None):
        """更新自动踱步中心（仅应由用户拖拽 / 点选移动调用）。"""
        self.home_x = self.x() if x is None else int(x)

    def walk_step(self):
        """行走的每一步移动（只改 X，Y 不变）"""
        if not self.walk_target or self.is_dragging:
            return

        current_x = self.x()
        target = self.walk_target
        y = self.y()

        if abs(current_x - target) < self.walk_speed:
            self.move(target, y)
            self.walk_target = None
            # 只有用户点选移动才把「家」迁到终点；自动踱步不改 home，避免冲掉拖拽位置
            if self.user_goto:
                self.set_home_x(target)
            self.user_goto = False
            self.walk_speed = self.idle_walk_speed
            self.change_state("idle")
        elif current_x < target:
            self.move(current_x + self.walk_speed, y)
        else:
            self.move(current_x - self.walk_speed, y)

    # ===== 鼠标事件 =====
    def enterEvent(self, event):
        super().enterEvent(event)
        if not self.is_dragging and not self.goto_pick_armed:
            self.hover_tip.schedule_show(self.cursor().pos())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.hover_tip.cancel()

    def mousePressEvent(self, event: QMouseEvent):
        self.hover_tip.cancel()
        if event.button() == Qt.LeftButton:
            self.cancel_goto_pick()
            self.is_dragging = True
            self._drag_moved = False
            self._press_global = event.globalPosition().toPoint()
            self.drag_offset = self._press_global - self.frameGeometry().topLeft()
            self.walk_target = None
            self.user_goto = False
            self.walk_speed = self.idle_walk_speed
            self.change_state("idle")
            self.activateWindow()
            self.setFocus()
        elif event.button() == Qt.RightButton:
            self.cancel_goto_pick()
            self.show_context_menu(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_dragging and event.buttons() & Qt.LeftButton:
            self.hover_tip.cancel()
            new_pos = event.globalPosition().toPoint() - self.drag_offset
            self.move(new_pos)
            # 拖拽过程中实时更新踱步中心，避免松手逻辑被打断时仍用旧 home
            if (event.globalPosition().toPoint() - self._press_global).manhattanLength() >= 6:
                self._drag_moved = True
                self.set_home_x(self.x())
        elif not self.is_dragging and self.underMouse():
            # 悬停移动时刷新出现位置（未显示则重新计时）
            if not self.hover_tip.isVisible():
                self.hover_tip.schedule_show(event.globalPosition().toPoint())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self.is_dragging:
            self.is_dragging = False
            self.set_home_x(self.x())
            dist = (event.globalPosition().toPoint() - self._press_global).manhattanLength()
            if dist < 6 and not self._drag_moved:
                # 等待是否构成双击
                self._single_click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            self._single_click_timer.stop()
            self.cancel_goto_pick()
            self.open_chat()

    def moveEvent(self, event):
        super().moveEvent(event)
        # 仅小聊天条在未钉住时跟随；扩展/记事/记忆等工具窗独立，不绑桌宠位置
        if self._chat_open and self.chat_panel.isVisible() and not self.chat_panel.is_pinned():
            self._reposition_chat()
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self._sync_bubble_avoid()

    def keyPressEvent(self, event):
        """Esc：取消点选；否则退出程序"""
        if event.key() == Qt.Key_Escape:
            if self.goto_pick_armed:
                self.cancel_goto_pick()
                return
            if self._notes_open:
                self.notes_panel.hide_panel()
                return
            if self._memory_open:
                self.memory_panel.hide_panel()
                return
            if self._quotes_open:
                self.quotes_panel.hide_panel()
                return
            if self._prompt_open:
                self.prompt_panel.hide_panel()
                return
            if self._models_open:
                self.models_panel.hide_panel()
                return
            if self._extensions_open:
                self.extensions_panel.hide_panel()
                return
            if self._history_open:
                self.history_panel.hide_panel()
                return
            if self._workspace_open:
                self.workspace_panel.hide_panel()
                return
            if self._studio_open:
                self.agent_studio.hide_panel()
                return
            if self._chat_open:
                self.chat_panel.hide_panel()
                return
            QApplication.quit()

    def on_click(self):
        """单击：开心互动（不再自动进入点选移动，避免误触）。"""
        self.change_state("happy")
        self.happy_timer.start(self.happy_duration)

    def open_chat(self):
        """打开输入条；若大窗口已开则聚焦工作台（不叠小条）。"""
        self.cancel_goto_pick()
        if self._studio_open and self.agent_studio.isVisible():
            self.agent_studio.raise_()
            self.agent_studio.activateWindow()
            try:
                self.agent_studio.input.setFocus()
            except Exception:
                pass
            return
        self._chat_open = True
        self.chat_panel.refresh_session_hint()
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self.chat_panel.show_near(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self._sync_bubble_avoid()

    def _reposition_chat(self):
        if self.chat_panel.is_pinned():
            return
        self.chat_panel.place_near(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self._sync_bubble_avoid()

    def _sync_bubble_avoid(self):
        """气泡排布时避开聊天输入框。"""
        from PySide6.QtCore import QRect

        if self._chat_open and self.chat_panel.isVisible():
            g = self.chat_panel.frameGeometry()
            self.bubble_lane.set_avoid_rect(
                QRect(g.x(), g.y(), g.width(), g.height())
            )
        else:
            self.bubble_lane.set_avoid_rect(None)

    def on_chat_closed(self):
        self._chat_open = False
        self.bubble_lane.set_avoid_rect(None)

    def on_chat_send(self, text: str, attachments: list | None = None):
        """用户发送：文档走正文提取；按 Chat 能力原生传图或 vision→文本降级。"""
        from pathlib import Path

        from agent.providers.media_gateway import resolve_turn

        # 「从此重开」：只有发送时才截断重跑；未发送则原对话不变
        rewind_id = self._rewind_anchor_id
        if rewind_id:
            self._commit_rewind_send(text, attachments, rewind_id)
            return

        attachments = list(attachments or [])
        # 兼容旧签名：若传入的是纯路径字符串列表
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

        self.walk_target = None
        self.user_goto = False
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
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
            self.create_new_agent()
            return
        if any(k in tstrip for k in ("新对话", "新建对话", "New Agent", "换个话题")) and not attachments and len(tstrip) < 12:
            self.create_new_agent()
            return

        if not (self._studio_open and self.agent_studio.isVisible()):
            self.bubble_lane.push(user_bubble, role="user", ms=5000)
        user_msg_id = ""
        try:
            item = chat_history.add_message(
                "user", user_bubble, meta={"prompt": ""}  # prompt 稍后补
            )
            user_msg_id = str((item or {}).get("id") or "")
        except Exception:
            pass
        if self._studio_open and self.agent_studio.isVisible():
            try:
                self.agent_studio.reload()
            except Exception:
                pass
        if any(k in text for k in ("查看记事", "记事内容", "打开记事", "记事本")):
            self.open_notes_panel()
        if any(k in text for k in ("查看记忆", "打开记忆", "记忆面板")):
            self.open_memory_panel()
        if any(k in text for k in ("聊天记录", "查看对话", "历史对话", "对话记录", "切换对话", "对话列表")):
            self.open_history_panel()
        if any(k in text for k in ("工作区", "打开文件夹", "切换项目", "代码目录")):
            self.open_workspace_panel()

        try:
            turn = resolve_turn(text, doc_paths=docs, media_items=media)
            prompt = turn.text_prompt
            user_content = turn.user_content
        except Exception as e:
            self.bubble_lane.push(f"附件处理失败：{e}", role="assistant", ms=9000)
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

        self.chat_panel.set_busy(True)
        studio_vis = self._studio_open and self.agent_studio.isVisible()
        if studio_vis:
            try:
                self.agent_studio.begin_stream()
            except Exception:
                pass
        else:
            self.bubble_lane.show_thinking()
        if not self.agent_runner.ask(
            prompt,
            memory_user_text=mem_text or None,
            user_msg_id=user_msg_id,
            user_content=user_content,
        ):
            self.chat_panel.set_busy(False)
            self.bubble_lane.clear_thinking()
            if not (self._studio_open and self.agent_studio.isVisible()):
                self.bubble_lane.push(
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
        kind = str(data.get("kind") or "")
        studio_vis = self._studio_open and self.agent_studio.isVisible()

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
                self.agent_studio.handle_stream_event(data)
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
                self.bubble_lane.show_thinking(tip[:80] if tip else None)
            except Exception:
                pass

    def _prompt_command_approval_dialog(self, data: dict):
        """非工作台时弹出统一确认框（非阻塞，避免卡住主线程导致其它确认 UI 消失）。"""
        from agent.command_approval import resolve_command_approval
        from agent.ui_dialogs import show_choice

        # 若此刻工作台已打开，改走聊天内嵌审批
        if self._studio_open and self.agent_studio.isVisible():
            try:
                self.agent_studio.handle_stream_event(
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
        self.chat_panel.set_busy(busy)
        if self._studio_open:
            self.agent_studio.set_busy(busy)
        if busy and not (self._studio_open and self.agent_studio.isVisible()):
            self.bubble_lane.show_thinking()
        # 空闲时由 reply/error 清 thinking

    def on_agent_reply(self, reply: str):
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        text = reply or "（没有回复）"
        meta = None
        studio_vis = self._studio_open and self.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.agent_studio.finalize_stream()
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
            self.agent_studio.append_assistant(text)
            self.agent_studio.set_busy(False)
        else:
            self.bubble_lane.push(
                text,
                role="assistant",
                ms=display_ms_for_text(text, base=14000),
            )
        self.change_state("happy")
        self.happy_timer.start(max(self.happy_duration, 1600))

    def on_agent_stop(self):
        """UI 请求停止本轮。"""
        if not self.agent_runner.cancel():
            return
        try:
            self.bubble_lane.show_thinking("正在停止…")
        except Exception:
            pass

    def on_agent_cancelled(self, msg: str):
        self.bubble_lane.clear_thinking()
        text = f"已停止：{msg or '本轮任务已取消'}"
        meta = {
            "status": "cancelled",
            "retryable": True,
            "error": msg or "",
        }
        studio_vis = self._studio_open and self.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.agent_studio.finalize_stream()
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
                self.agent_studio.reload()
                self.agent_studio.set_busy(False)
            except Exception:
                pass
        else:
            self.bubble_lane.push(text, role="assistant", ms=8000)

    def on_agent_error(self, err: str):
        self.bubble_lane.clear_thinking()
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
        studio_vis = self._studio_open and self.agent_studio.isVisible()
        if studio_vis:
            try:
                snap = self.agent_studio.finalize_stream()
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
                self.agent_studio.reload()
                self.agent_studio.set_busy(False)
            except Exception:
                pass
        else:
            self.bubble_lane.push(msg, role="assistant", ms=10000)

    def on_retry_from_message(self, message_id: str):
        """从失败/中断的助手消息重试上一请求。"""
        if self.agent_runner.busy:
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
            self.bubble_lane.push("没有可重试的请求。", role="assistant", ms=6000)
            return
        self._start_agent_prompt(prompt, memory_user_text=mem, user_msg_id=uid, add_user=False)

    def cancel_rewind_edit(self):
        """放弃从此重开编辑：不清空历史，只退出编辑态。"""
        was = bool(self._rewind_anchor_id)
        self._rewind_anchor_id = None
        try:
            self.chat_panel.set_rewind_mode(False)
        except Exception:
            pass
        try:
            self.agent_studio.set_rewind_mode(False)
        except Exception:
            pass
        if was:
            try:
                self.bubble_lane.push(
                    "已取消从此重开，对话保持原样。", role="assistant", ms=4500
                )
            except Exception:
                pass

    def on_rewind_from_message(self, message_id: str):
        """进入从此重开编辑：把原文载入输入框，发送才截断；取消则不变。"""
        if self.agent_runner.busy:
            self.bubble_lane.push("请先停止当前任务，再从此处重开。", role="assistant", ms=6000)
            return
        msg = chat_history.get_message(message_id)
        if not msg or msg.get("role") != "user":
            return
        old_text = str(msg.get("text") or "")
        self._rewind_anchor_id = message_id

        # 载入输入框供编辑（工作台优先）
        try:
            self.chat_panel.set_draft_text(old_text)
            self.chat_panel.set_rewind_mode(True, old_text)
        except Exception:
            pass
        try:
            self.agent_studio.set_draft_text(old_text)
            self.agent_studio.set_rewind_mode(True, old_text)
        except Exception:
            pass

        if self._studio_open and self.agent_studio.isVisible():
            try:
                self.agent_studio.raise_()
                self.agent_studio.activateWindow()
                self.agent_studio.input.setFocus()
            except Exception:
                pass
        else:
            # 小条可见时聚焦；否则打开工作台更方便编辑
            try:
                if self.chat_panel.isVisible():
                    self.chat_panel.input.setFocus()
                else:
                    self.open_agent_studio()
                    self.agent_studio.set_draft_text(old_text)
                    self.agent_studio.set_rewind_mode(True, old_text)
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
            self.bubble_lane.push("内容不能为空。", role="assistant", ms=5000)
            return

        # 先退出编辑态（避免递归）
        self._rewind_anchor_id = None
        try:
            self.chat_panel.set_rewind_mode(False)
            self.agent_studio.set_rewind_mode(False)
        except Exception:
            pass

        result = chat_history.truncate_after_message(message_id, keep_anchor=True)
        if not result.get("ok"):
            self.bubble_lane.push(
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
            self.bubble_lane.push(f"附件处理失败：{e}", role="assistant", ms=9000)
            return

        try:
            chat_history.replace_message_text(
                message_id, user_bubble, prompt=prompt
            )
        except Exception:
            pass

        if self._studio_open and self.agent_studio.isVisible():
            try:
                self.agent_studio.reload()
            except Exception:
                pass
        else:
            try:
                self.bubble_lane.push(user_bubble, role="user", ms=5000)
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
        self.chat_panel.set_busy(True)
        studio_vis = self._studio_open and self.agent_studio.isVisible()
        if studio_vis:
            try:
                self.agent_studio.begin_stream()
            except Exception:
                pass
        else:
            self.bubble_lane.show_thinking()
        if not self.agent_runner.ask(
            prompt,
            memory_user_text=memory_user_text,
            user_msg_id=user_msg_id,
            user_content=user_content,
        ):
            self.chat_panel.set_busy(False)
            self.bubble_lane.clear_thinking()

    def on_bubble_open_full(self, text: str, role: str):
        """点击气泡 → 打开聊天记录并展示全文。"""
        tag = {"user": "我", "assistant": "Mini_Lu", "alarm": "闹钟", "quote": "语录"}.get(role, role)
        if role == "assistant":
            try:
                from agent.identity import assistant_label

                tag = assistant_label()
            except Exception:
                pass
        self.open_history_panel()
        self.history_panel.show_plain_text(tag, text)

    def _place_tool_panel_once(self, panel) -> None:
        """工具窗只在首次弹出时靠近桌宠一次；已打开则保持用户拖过的位置。"""
        if panel.isVisible():
            return
        panel.place_near(self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT)

    def open_notes_panel(self):
        """打开记事本面板（列表简略 → 点进全文）。"""
        self.cancel_goto_pick()
        self._notes_open = True
        self._place_tool_panel_once(self.notes_panel)
        self.notes_panel.show_panel()

    def _reposition_notes(self):
        # 工具窗不跟随桌宠
        return

    def on_notes_closed(self):
        self._notes_open = False

    def open_memory_panel(self):
        """打开记忆面板（运行/对话记忆，可删可重置）。"""
        self.cancel_goto_pick()
        self._memory_open = True
        self._place_tool_panel_once(self.memory_panel)
        self.memory_panel.show_panel()

    def open_history_panel(self):
        """打开对话列表 / 聊天记录（多 Agent 切换）。"""
        self.cancel_goto_pick()
        self._history_open = True
        self._place_tool_panel_once(self.history_panel)
        self.history_panel.show_panel()

    def create_new_agent(self):
        """新建独立对话（类似 Cursor New Agent），可主动命名。"""
        from agent.ui_dialogs import ask_text

        self.cancel_goto_pick()
        self.cancel_rewind_edit()
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
        if self._studio_open and self.agent_studio.isVisible():
            self.agent_studio.reload()
        else:
            self.open_chat()
        try:
            self.bubble_lane.push(
                f"已新建对话：{s.get('title') or title}",
                role="assistant",
                ms=3500,
            )
        except Exception:
            pass

    def on_session_changed(self, session_id: str = ""):
        """切换对话后刷新输入栏提示。"""
        if self._rewind_anchor_id:
            self._rewind_anchor_id = None
            try:
                self.chat_panel.set_rewind_mode(False)
                self.agent_studio.set_rewind_mode(False)
            except Exception:
                pass
        if hasattr(self, "chat_panel"):
            self.chat_panel.refresh_session_hint()
        # 工作台内部已自行刷新聊天；仅同步历史面板
        if self._history_open and self.history_panel.isVisible():
            self.history_panel.reload()
        _ = session_id

    def open_agent_studio(self):
        """打开编码大窗口：聊天 + 改动对比；隐藏小输入条。"""
        self.cancel_goto_pick()
        already_open = bool(self._studio_open and self.agent_studio.isVisible())
        self._studio_open = True
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
        rewind_on = bool(self._rewind_anchor_id)
        if self.chat_panel.isVisible():
            self.chat_panel.hide()
        self.agent_studio.place_near(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
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
        self._studio_open = False
        self._chat_open = True
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
                self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
            )

    def on_studio_closed(self):
        """大窗 ×：直接关闭，不自动打开小输入条；草稿写回小条供下次打开。"""
        self._studio_open = False
        self._chat_open = False
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
        if self._studio_open and self.agent_studio.isVisible():
            self.agent_studio.reload_edits()

    def on_studio_send(self, text: str, attachments: list | None = None):
        """大窗口发送：与小输入条同一套 Agent 管线（含附件）。"""
        self.on_chat_send(text, attachments)

    def on_history_closed(self):
        self._history_open = False

    def _reposition_history(self):
        return

    def open_workspace_panel(self):
        """打开工作区管理（选择/切换代码项目文件夹）。"""
        self.cancel_goto_pick()
        self._workspace_open = True
        self._place_tool_panel_once(self.workspace_panel)
        self.workspace_panel.show_panel()

    def on_workspace_closed(self):
        self._workspace_open = False

    def _reposition_workspace(self):
        return

    def _on_workspace_changed(self):
        self._refresh_workspace_tooltip()
        if hasattr(self, "agent_studio"):
            self.agent_studio.refresh_workspace()
        active = get_active_root()
        tip = f"当前项目：{active}" if active else "已更新工作区"
        try:
            self.bubble_lane.push(tip, role="assistant", ms=3500)
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
        self._memory_open = False

    def open_quotes_panel(self):
        self.cancel_goto_pick()
        self._quotes_open = True
        self._place_tool_panel_once(self.quotes_panel)
        self.quotes_panel.show_panel()

    def _reposition_quotes(self):
        return

    def on_quotes_closed(self):
        self._quotes_open = False

    def open_prompt_panel(self):
        """打开 Prompt 版本 / A/B / 反馈改写面板。"""
        self.cancel_goto_pick()
        self._prompt_open = True
        self._place_tool_panel_once(self.prompt_panel)
        self.prompt_panel.show_panel()

    def _reposition_prompt(self):
        return

    def on_prompt_closed(self):
        self._prompt_open = False

    def open_models_panel(self):
        """打开多模型 / API 接入设置。"""
        self.cancel_goto_pick()
        self._models_open = True
        self._place_tool_panel_once(self.models_panel)
        self.models_panel.show_panel()

    def _reposition_models(self):
        return

    def on_models_closed(self):
        self._models_open = False

    def on_models_changed(self):
        """切换 Chat 模型后重建 Agent。"""
        ok = self.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self,
                "模型设置",
                "Agent 忙碌中，当前回复结束后的下一轮将使用新模型。",
            )

    def open_extensions_panel(self):
        self.cancel_goto_pick()
        self._extensions_open = True
        panel = self.extensions_panel
        if not panel.isVisible():
            if self._studio_open and self.agent_studio.isVisible():
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
        self._extensions_open = False

    def on_extensions_changed(self):
        ok = self.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self,
                "扩展",
                "Agent 忙碌中，当前回复结束后的下一轮将带上新 MCP 工具。",
            )

    def on_prompt_changed(self):
        """激活版本或 A/B 变更后重建 Agent。"""
        ok = self.agent_runner.reset_agent()
        if not ok:
            from agent.ui_dialogs import inform

            inform(
                self,
                "Prompt",
                "Agent 忙碌中，稍后空闲会使用新 Prompt（或等当前回复结束后再试）。",
            )

    def _restart_quote_timer(self):
        settings = quotes_store.get_settings()
        ms = max(8, int(settings.get("interval_seconds") or 12)) * 1000
        self.quote_timer.start(ms)

    def maybe_say_quote(self):
        """待机时空闲冒泡语录（聊天风格气泡）。"""
        if self.current_state != "idle":
            return
        if self.is_dragging or self.goto_pick_armed or self.user_goto:
            return
        if self.walk_target is not None:
            return
        if self._chat_open or self.agent_runner.busy:
            return
        if self._notes_open or self._memory_open or self._quotes_open or self._prompt_open or self._models_open or self._extensions_open or self._history_open or self._workspace_open or self._studio_open:
            return
        text = quotes_store.pick_quote()
        if not text:
            return
        settings = quotes_store.get_settings()
        ms = int(settings.get("display_ms") or 8000)
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self.bubble_lane.push(text, role="quote", ms=ms)

    def show_reminder_bubble(self, text: str):
        """闹钟 / 提醒：微信风格气泡。"""
        self.bubble_lane.set_pet_geo(
            self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT
        )
        self.bubble_lane.push(text, role="alarm", ms=12000)
        self.change_state("happy")
        self.happy_timer.start(max(self.happy_duration, 2200))
        if self._notes_open and self.notes_panel.isVisible():
            self.notes_panel.reload()

    def check_reminders(self):
        """每秒检查到期记事/旧版 reminders。"""
        messages: list[str] = []
        try:
            from agent.notes_store import pop_due_notes

            for item in pop_due_notes():
                messages.append(item.get("content") or item.get("summary") or "提醒")
        except Exception:
            pass
        try:
            from agent.reminders import pop_due

            for item in pop_due():
                messages.append(item.get("content") or "提醒")
        except Exception:
            pass
        for text in messages:
            self.show_reminder_bubble(text)

    def show_context_menu(self, pos: QPoint):
        """右键菜单"""
        menu = QMenu(self)

        help_action = QAction("使用帮助", self)
        help_action.triggered.connect(self.show_help)
        menu.addAction(help_action)

        chat_action = QAction("打开聊天…", self)
        chat_action.triggered.connect(self.open_chat)
        menu.addAction(chat_action)

        rename_action = QAction("给它取名…", self)
        rename_action.triggered.connect(self.rename_agent)
        menu.addAction(rename_action)

        notes_action = QAction("查看记事内容", self)
        notes_action.triggered.connect(self.open_notes_panel)
        menu.addAction(notes_action)

        mem_action = QAction("查看记忆", self)
        mem_action.triggered.connect(self.open_memory_panel)
        menu.addAction(mem_action)

        quotes_action = QAction("待机语录…", self)
        quotes_action.triggered.connect(self.open_quotes_panel)
        menu.addAction(quotes_action)

        prompt_action = QAction("Prompt 设置…", self)
        prompt_action.triggered.connect(self.open_prompt_panel)
        menu.addAction(prompt_action)

        models_action = QAction("模型设置…", self)
        models_action.triggered.connect(self.open_models_panel)
        menu.addAction(models_action)

        ext_action = QAction("扩展（MCP/Skills）…", self)
        ext_action.triggered.connect(self.open_extensions_panel)
        menu.addAction(ext_action)

        hist_action = QAction("聊天记录…", self)
        hist_action.triggered.connect(self.open_history_panel)
        menu.addAction(hist_action)

        new_agent_action = QAction("新对话（New Agent）", self)
        new_agent_action.triggered.connect(self.create_new_agent)
        menu.addAction(new_agent_action)

        studio_action = QAction("Agent 工作台…", self)
        studio_action.triggered.connect(self.open_agent_studio)
        menu.addAction(studio_action)

        ws_action = QAction("工作区…", self)
        ws_action.triggered.connect(self.open_workspace_panel)
        menu.addAction(ws_action)

        goto_action = QAction("点选横向移动…", self)
        goto_action.triggered.connect(self.start_goto_pick)
        menu.addAction(goto_action)

        hide_action = QAction("隐藏", self)
        hide_action.triggered.connect(self.hide)
        menu.addAction(hide_action)

        menu.addSeparator()

        skin_menu = menu.addMenu("切换皮肤")
        for skin_name in self.available_skins:
            action = QAction(skin_name, self)
            action.triggered.connect(lambda checked, name=skin_name: self.switch_skin(name))
            skin_menu.addAction(action)

        menu.addSeparator()

        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(QApplication.quit)
        menu.addAction(quit_action)

        menu.exec(pos)

    def rename_agent(self):
        """自定义 Agent 在对话中的称呼。"""
        from agent.identity import DEFAULT_DISPLAY_NAME, display_name, product_name, set_display_name
        from agent.ui_dialogs import ask_text

        current = display_name()
        text, ok = ask_text(
            self,
            "给它取名",
            f"产品名固定为 {product_name()}。\n"
            f"在对话里怎么称呼它？（留空恢复默认「{DEFAULT_DISPLAY_NAME}」）",
            text=current,
            placeholder=DEFAULT_DISPLAY_NAME,
            ok_text="保存",
        )
        if not ok:
            return
        name = set_display_name(text)
        self._refresh_tray_tooltip()
        try:
            self.agent_studio.refresh_identity()
        except Exception:
            pass
        try:
            self.bubble_lane.push(f"以后叫我「{name}」吧～", role="assistant", ms=5000)
        except Exception:
            pass

    def show_help(self):
        """显示使用帮助"""
        try:
            from agent.identity import display_name, product_name

            pname = product_name()
            dname = display_name()
        except Exception:
            pname, dname = "Mini_Lu", "Mini_Lu"
        help_text = f"""
        <h3>{pname} 使用说明</h3>
        <p>当前称呼：<b>{dname}</b>（右键「给它取名…」可改）</p>
        <p><b>左键拖拽</b>：移动形象位置</p>
        <p><b>左键点击</b>：开心互动</p>
        <p><b>双击</b> 或 <b>右键 → 打开聊天</b>：输入框发送；回复以气泡或工作台显示</p>
        <p><b>长回复</b>：气泡显示预览；点气泡或「聊天记录」可看全文</p>
        <p><b>新对话</b>：聊天栏「＋」或右键「新对话」；各对话上下文独立（类 Cursor New Agent）</p>
        <p><b>Agent 工作台</b>：聊天栏「⛶」展开大窗口；改代码会暂存，对比后「保存/丢弃」</p>
        <p><b>模型设置</b>：右键「模型设置…」切换 DeepSeek / 通义 / 智谱 / Kimi / OpenAI / 聚合网关 / Ollama，或自定义 OpenAI 兼容端点</p>
        <p><b>右键 → 查看记事 / 记忆 / 语录 / Prompt 设置 / 模型设置 / 聊天记录 / 工作区</b></p>
        <p><b>Prompt 设置</b>：版本化 system prompt、A/B 分流；工作台消息可 👍👎，差评可生成改写候选并人工采纳</p>
        <p><b>工作区</b>：聊天栏「📁」打开文件夹（类似 VS Code），设定代码读写根目录</p>
        <p><b>待机语录</b>：空闲时偶尔冒泡；可在面板添加/删除，默认见 config/quotes.yaml</p>
        <p><b>聊天举例</b>：「记住我叫 Lee」「查看记忆」「聊天记录」「打开QQ」「工作区」</p>
        <p><b>闹钟</b>：到期时暖色气泡提醒</p>
        <p><b>右键 → 点选横向移动…</b>：再点屏幕目标位置，形象会<strong>只左右</strong>走到该处（Esc / 右键取消）</p>
        <p><b>Esc 键</b>：关闭记事/语录/聊天记录/工作区/聊天 / 取消点选</p>
        <p><b>关闭窗口</b>：最小化到系统托盘，不退出</p>
        <hr>
        <p>提醒需 {pname} 保持运行。需配置 API Key（config/models.local.yaml 或 llm.local.yaml）。</p>
        """

        from agent.ui_dialogs import inform

        inform(self, "使用帮助", help_text, rich=True, width=520, ok_text="关闭")

    def closeEvent(self, event):
        """关闭时最小化到托盘"""
        event.ignore()
        self.cancel_goto_pick()
        if self._chat_open:
            self.chat_panel.hide_panel()
        if self._notes_open:
            self.notes_panel.hide_panel()
        if self._memory_open:
            self.memory_panel.hide_panel()
        if self._quotes_open:
            self.quotes_panel.hide_panel()
        if self._prompt_open:
            self.prompt_panel.hide_panel()
        if self._models_open:
            self.models_panel.hide_panel()
        if self._extensions_open:
            self.extensions_panel.hide_panel()
        if self._history_open:
            self.history_panel.hide_panel()
        if self._workspace_open:
            self.workspace_panel.hide_panel()
        if self._studio_open:
            self.agent_studio.hide_panel()
        self.bubble_lane.hide_all()
        self.hover_tip.cancel()
        self.hide()
        self.tray_icon.showMessage(
            "Mini_Lu 已最小化",
            "正在后台运行，点击托盘图标显示",
            QSystemTrayIcon.Information,
            2000
        )


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    app = QApplication(sys.argv)
    app.setApplicationName("Mini_Lu")
    app.setOrganizationName("Mini_Lu")
    app.setQuitOnLastWindowClosed(False)

    try:
        from agent.ui_fonts import apply_app_defaults

        apply_app_defaults(app)
    except Exception:
        pass

    try:
        from agent.ui_icons import app_icon

        app.setWindowIcon(app_icon())
    except Exception:
        pass

    # 后台预热本机软件索引，避免首次 open_app 卡顿
    try:
        from agent.app_launcher import warmup_index

        warmup_index()
    except Exception:
        pass

    pet = DesktopPet()
    pet.show()

    # Ctrl+滚轮缩放聊天字号（保持引用避免被 GC）
    app._ctrl_wheel_zoom_filter = _CtrlWheelZoomFilter(app)
    app.installEventFilter(app._ctrl_wheel_zoom_filter)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
