"""
桌面宠物主程序 v2
基于 PySide6 实现
改进：图片自适应缩放、小范围踱步、多皮肤系统、流畅动画
"""
import sys
import random
from enum import Enum
from pathlib import Path
from PySide6.QtWidgets import (QApplication, QWidget, QMenu,
                               QSystemTrayIcon)
from PySide6.QtGui import (QPixmap, QImage, QMouseEvent, QAction, QIcon,
                           QPainter, QRegion, QColor, QGuiApplication)
from PySide6.QtCore import Qt, QTimer, QPoint, QRect, Signal, QObject, QEvent

from agent.agent_runner import AgentRunner
from agent.chat_bubble import BubbleLane
from agent.desktop import AgentController, PanelManager
from agent.desktop.constants import (
    ALPHA_THRESHOLD,
    DISPLAY_PERSON_HEIGHT,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)
from agent.event_bus import UI_READY, get_event_bus
from agent.hover_tip import HoverTip
from agent.plugin import get_plugin_manager
from agent.ui_bridge import init_bridge
from agent import quotes_store
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



class PetAnim(str, Enum):
    """宠物动画状态；新增动作须同时写入 FRAME_INTERVAL。"""

    IDLE = "idle"
    WALK_LEFT = "walk_left"
    WALK_RIGHT = "walk_right"
    HAPPY = "happy"


# 各动作帧间隔（毫秒）；须覆盖全部 PetAnim，缺项在导入时即失败
FRAME_INTERVAL: dict[PetAnim, int] = {
    PetAnim.IDLE: 300,
    PetAnim.WALK_LEFT: 120,
    PetAnim.WALK_RIGHT: 120,
    PetAnim.HAPPY: 150,
}
if set(FRAME_INTERVAL) != set(PetAnim):
    missing = set(PetAnim) - set(FRAME_INTERVAL)
    extra = set(FRAME_INTERVAL) - set(PetAnim)
    raise RuntimeError(
        f"FRAME_INTERVAL 与 PetAnim 不一致: missing={missing} extra={extra}"
    )


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

        # 帧规范化缓存：(path, mtime_ns, size, 显示参数) → QPixmap
        self._frame_norm_cache: dict[tuple, QPixmap] = {}
        
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
        self.anim_timer.start(FRAME_INTERVAL[PetAnim.IDLE])
        
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

        # 面板 / Agent 控制器（标志位仍在 host）
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

        self.panels = PanelManager(self)
        self.chat_panel = self.panels.chat_panel
        self.notes_panel = self.panels.notes_panel
        self.memory_panel = self.panels.memory_panel
        self.workspace_panel = self.panels.workspace_panel
        self.quotes_panel = self.panels.quotes_panel
        self.prompt_panel = self.panels.prompt_panel
        self.models_panel = self.panels.models_panel
        self.extensions_panel = self.panels.extensions_panel
        self.history_panel = self.panels.history_panel
        self.agent_studio = self.panels.agent_studio

        self.bubble_lane = BubbleLane()
        self.hover_tip = HoverTip()
        self.setMouseTracking(True)
        self.agent_runner = AgentRunner(self)
        self.agent_ctrl = AgentController(self)
        self.bubble_lane.on_open_full = self.agent_ctrl.on_bubble_open_full

        self.chat_panel.send_requested.connect(self.agent_ctrl.on_chat_send)
        self.chat_panel.closed.connect(self.panels.on_chat_closed)
        self.chat_panel.moved_by_user.connect(self.panels._sync_bubble_avoid)
        self.chat_panel.workspace_requested.connect(self.panels.open_workspace_panel)
        self.chat_panel.new_agent_requested.connect(self.panels.create_new_agent)
        self.chat_panel.agents_requested.connect(self.panels.open_history_panel)
        self.chat_panel.expand_requested.connect(self.panels.open_agent_studio)
        self.chat_panel.stop_requested.connect(self.agent_ctrl.on_agent_stop)
        self.chat_panel.rewind_cancel_requested.connect(self.agent_ctrl.cancel_rewind_edit)
        self.notes_panel.closed.connect(self.panels.on_notes_closed)
        self.memory_panel.closed.connect(self.panels.on_memory_closed)
        self.workspace_panel.closed.connect(self.panels.on_workspace_closed)
        self.workspace_panel.changed.connect(self.panels._on_workspace_changed)
        self.quotes_panel.closed.connect(self.panels.on_quotes_closed)
        self.quotes_panel.settings_changed.connect(self._restart_quote_timer)
        self.prompt_panel.closed.connect(self.panels.on_prompt_closed)
        self.prompt_panel.prompt_changed.connect(self.panels.on_prompt_changed)
        self.models_panel.closed.connect(self.panels.on_models_closed)
        self.models_panel.models_changed.connect(self.panels.on_models_changed)
        self.extensions_panel.closed.connect(self.panels.on_extensions_closed)
        self.extensions_panel.extensions_changed.connect(self.panels.on_extensions_changed)
        self.history_panel.closed.connect(self.panels.on_history_closed)
        self.history_panel.session_changed.connect(self.panels.on_session_changed)
        self.agent_studio.closed.connect(self.panels.on_studio_closed)
        self.agent_studio.collapse_requested.connect(self.panels.on_studio_collapse)
        self.agent_studio.send_requested.connect(self.panels.on_studio_send)
        self.agent_studio.new_agent_requested.connect(self.panels.create_new_agent)
        self.agent_studio.session_changed.connect(self.panels.on_session_changed)
        self.agent_studio.workspace_requested.connect(self.panels.open_workspace_panel)
        self.agent_studio.workspace_changed.connect(self.panels._on_workspace_changed)
        self.agent_studio.extensions_requested.connect(self.panels.open_extensions_panel)
        self.agent_studio.models_changed.connect(self.panels.on_models_changed)
        self.agent_studio.rewind_requested.connect(self.agent_ctrl.on_rewind_from_message)
        self.agent_studio.retry_requested.connect(self.agent_ctrl.on_retry_from_message)
        self.agent_studio.stop_requested.connect(self.agent_ctrl.on_agent_stop)
        self.agent_studio.rewind_cancel_requested.connect(self.agent_ctrl.cancel_rewind_edit)

        self.agent_runner.reply_ready.connect(self.agent_ctrl.on_agent_reply)
        self.agent_runner.error.connect(self.agent_ctrl.on_agent_error)
        self.agent_runner.cancelled.connect(self.agent_ctrl.on_agent_cancelled)
        self.agent_runner.busy_changed.connect(self.agent_ctrl.on_agent_busy)
        self.agent_runner.stream_event.connect(self.agent_ctrl.on_agent_stream_event)

        self.ui_bridge = init_bridge(self)
        self.ui_bridge.open_notes.connect(self.panels.open_notes_panel)
        self.ui_bridge.open_memory.connect(self.panels.open_memory_panel)
        self.ui_bridge.open_workspace.connect(self.panels.open_workspace_panel)
        self.ui_bridge.open_agent_studio.connect(self.panels.open_agent_studio)
        self.ui_bridge.open_prompt.connect(self.panels.open_prompt_panel)
        self.ui_bridge.edits_changed.connect(self.panels.on_edits_changed)
        self.ui_bridge.show_bubble.connect(self.show_reminder_bubble)
        self.ui_bridge.reminders_changed.connect(self.schedule_reminder_timer)
        self.ui_bridge.agent_ui_event.connect(self.agent_ctrl.on_agent_ui_event)
        self.panels._refresh_workspace_tooltip()
        set_review_enabled(True)

        # Plugin + EventBus：UI 就绪
        try:
            pm = get_plugin_manager()
            pm.load_skill_plugins()
            pm.notify_ui_ready(self.panels)
            get_event_bus().emit(UI_READY, self.panels)
        except Exception:
            pass

        # 单击延迟：避免双击打开聊天时误触发单击互动
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.timeout.connect(self.on_click)
        
        # 互动恢复定时器
        self.happy_timer = QTimer(self)
        self.happy_timer.setSingleShot(True)
        self.happy_timer.timeout.connect(self.back_to_idle)

        # 提醒：按下次到期动态调度（非每秒空转）
        try:
            from agent.reminders import migrate_legacy_reminders

            migrate_legacy_reminders()
        except Exception:
            pass
        self.reminder_timer = QTimer(self)
        self.reminder_timer.setSingleShot(True)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.schedule_reminder_timer()

        # 待机语录
        self.quote_timer = QTimer(self)
        self.quote_timer.timeout.connect(self.maybe_say_quote)
        self._restart_quote_timer()


    def pet_geo(self) -> tuple[int, int, int, int]:
        """面板定位用：(x, y, w, h)。"""
        return self.x(), self.y(), WINDOW_WIDTH, WINDOW_HEIGHT

    def discover_skins(self) -> list[str]:
        """发现可用皮肤：仅维护 Q版卡通"""
        preferred = "Q版卡通"
        if not SKINS_DIR.exists():
            return ["default"]
        skins = [d.name for d in SKINS_DIR.iterdir() if d.is_dir()]
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
                # 非窗口画布尺寸时底部居中兜底
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
        """根据 alpha 通道计算人物内容包围盒（缩略 + 字节扫描）。"""
        if image.isNull():
            return None
        img = image.convertToFormat(QImage.Format_ARGB32)
        factor = max(1, min(img.width(), img.height()) // 400)
        small = img.scaled(
            max(1, img.width() // factor),
            max(1, img.height() // factor),
            Qt.IgnoreAspectRatio,
            Qt.FastTransformation,
        )
        w, h = small.width(), small.height()
        min_x, min_y = w, h
        max_x, max_y = -1, -1
        # 按行扫 ARGB32 字节，避免逐像素 QImage.pixel() 开销
        bpl = small.bytesPerLine()
        bits = small.constBits()
        if bits is not None:
            mv = memoryview(bits).cast("B")
            thr = ALPHA_THRESHOLD
            for y in range(h):
                row = y * bpl
                for x in range(w):
                    # Format_ARGB32：小端常见为 B,G,R,A
                    a = mv[row + x * 4 + 3]
                    if a > thr:
                        if x < min_x:
                            min_x = x
                        if x > max_x:
                            max_x = x
                        if y < min_y:
                            min_y = y
                        if y > max_y:
                            max_y = y
        else:
            for y in range(h):
                for x in range(w):
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
        pad = max(4, factor * 2)
        left = max(0, min_x * factor - pad)
        top = max(0, min_y * factor - pad)
        right = min(img.width(), (max_x + 1) * factor + pad)
        bottom = min(img.height(), (max_y + 1) * factor + pad)
        return QRect(left, top, right - left, bottom - top)

    def normalize_frame(
        self, pix: QPixmap, *, source_path: Path | None = None
    ) -> QPixmap:
        """按人物内容高度统一缩放；同源路径按 mtime 缓存，换肤/重启少重算。"""
        if pix.isNull():
            return pix

        cache_key: tuple | None = None
        if source_path is not None:
            try:
                st = source_path.stat()
                cache_key = (
                    str(source_path.resolve()),
                    st.st_mtime_ns,
                    st.st_size,
                    DISPLAY_PERSON_HEIGHT,
                    WINDOW_WIDTH,
                    WINDOW_HEIGHT,
                    ALPHA_THRESHOLD,
                )
                hit = self._frame_norm_cache.get(cache_key)
                if hit is not None and not hit.isNull():
                    return hit
            except OSError:
                cache_key = None

        image = pix.toImage()
        bbox = self.content_bbox(image)
        if bbox is None or bbox.height() <= 0:
            out = pix.scaled(
                WINDOW_WIDTH, WINDOW_HEIGHT,
                Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            if cache_key is not None:
                self._frame_norm_cache[cache_key] = out
            return out

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
        if cache_key is not None:
            self._frame_norm_cache[cache_key] = canvas
            # 防止无限增长：只保留最近约 200 帧
            if len(self._frame_norm_cache) > 220:
                for old in list(self._frame_norm_cache.keys())[:40]:
                    self._frame_norm_cache.pop(old, None)
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
                    frames.append(self.normalize_frame(pix, source_path=f))

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
        # 无人操作时降低自动踱步频率
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

    def change_state(self, new_state: str | PetAnim):
        """切换动作状态，并调整动画帧率（间隔取自 FRAME_INTERVAL[PetAnim]）。"""
        if isinstance(new_state, PetAnim):
            anim = new_state
            key = anim.value
        else:
            key = str(new_state or "").strip()
            try:
                anim = PetAnim(key)
            except ValueError:
                # 未登记状态：保留字符串以尝试播帧，帧率回退到 idle 并打日志
                print(
                    f"[pet] 未知动画状态 {key!r}，未在 PetAnim/FRAME_INTERVAL 中定义，"
                    f"使用 {PetAnim.IDLE.value} 帧间隔"
                )
                if self.current_state != key:
                    self.current_state = key
                    self.current_frame = 0
                    self.anim_timer.setInterval(FRAME_INTERVAL[PetAnim.IDLE])
                    self.update_image()
                return
        if self.current_state != key:
            self.current_state = key
            self.current_frame = 0
            self.anim_timer.setInterval(FRAME_INTERVAL[anim])
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

    # 鼠标事件
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
        return self.panels.open_chat()
    def _reposition_chat(self):
        return self.panels._reposition_chat()
    def _sync_bubble_avoid(self):
        return self.panels._sync_bubble_avoid()
    def on_chat_closed(self):
        return self.panels.on_chat_closed()
    @staticmethod
    def _is_panel_open_command(text: str, keywords: tuple[str, ...], *, max_len: int = 28) -> bool:
        return AgentController._is_panel_open_command(text=text, keywords=keywords, max_len=max_len)
    def on_chat_send(self, text: str, attachments: list | None = None):
        return self.agent_ctrl.on_chat_send(text=text, attachments=attachments)
    def on_agent_ui_event(self, ev: object):
        return self.agent_ctrl.on_agent_ui_event(ev=ev)
    def on_agent_stream_event(self, ev: object):
        return self.agent_ctrl.on_agent_stream_event(ev=ev)
    def _prompt_command_approval_dialog(self, data: dict):
        return self.agent_ctrl._prompt_command_approval_dialog(data=data)
    def on_agent_busy(self, busy: bool):
        return self.agent_ctrl.on_agent_busy(busy=busy)
    def on_agent_reply(self, reply: str):
        return self.agent_ctrl.on_agent_reply(reply=reply)
    def on_agent_stop(self):
        return self.agent_ctrl.on_agent_stop()
    def on_agent_cancelled(self, msg: str):
        return self.agent_ctrl.on_agent_cancelled(msg=msg)
    def on_agent_error(self, err: str):
        return self.agent_ctrl.on_agent_error(err=err)
    def on_retry_from_message(self, message_id: str):
        return self.agent_ctrl.on_retry_from_message(message_id=message_id)
    def cancel_rewind_edit(self):
        return self.agent_ctrl.cancel_rewind_edit()
    def on_rewind_from_message(self, message_id: str):
        return self.agent_ctrl.on_rewind_from_message(message_id=message_id)
    def _commit_rewind_send(
        self,
        text: str,
        attachments: list | None,
        message_id: str,
    ) -> None:
        return self.agent_ctrl._commit_rewind_send(text=text, attachments=attachments, message_id=message_id)
    def _start_agent_prompt(
        self,
        prompt: str,
        *,
        memory_user_text: str | None = None,
        user_msg_id: str = "",
        add_user: bool = False,
        user_content=None,
    ) -> None:
        return self.agent_ctrl._start_agent_prompt(prompt=prompt, memory_user_text=memory_user_text, user_msg_id=user_msg_id, add_user=add_user, user_content=user_content)
    def on_bubble_open_full(self, text: str, role: str):
        return self.agent_ctrl.on_bubble_open_full(text=text, role=role)
    def _place_tool_panel_once(self, panel) -> None:
        return self.panels._place_tool_panel_once(panel=panel)
    def open_notes_panel(self):
        return self.panels.open_notes_panel()
    def _reposition_notes(self):
        return self.panels._reposition_notes()
    def on_notes_closed(self):
        return self.panels.on_notes_closed()
    def open_memory_panel(self):
        return self.panels.open_memory_panel()
    def open_history_panel(self):
        return self.panels.open_history_panel()
    def create_new_agent(self):
        return self.panels.create_new_agent()
    def on_session_changed(self, session_id: str = ""):
        return self.panels.on_session_changed(session_id=session_id)
    def open_agent_studio(self):
        return self.panels.open_agent_studio()
    def on_studio_collapse(self):
        return self.panels.on_studio_collapse()
    def on_studio_closed(self):
        return self.panels.on_studio_closed()
    def apply_ui_font_zoom(self):
        return self.panels.apply_ui_font_zoom()
    def _reposition_studio(self):
        return self.panels._reposition_studio()
    def on_edits_changed(self):
        return self.panels.on_edits_changed()
    def on_studio_send(self, text: str, attachments: list | None = None):
        return self.panels.on_studio_send(text=text, attachments=attachments)
    def on_history_closed(self):
        return self.panels.on_history_closed()
    def _reposition_history(self):
        return self.panels._reposition_history()
    def open_workspace_panel(self):
        return self.panels.open_workspace_panel()
    def on_workspace_closed(self):
        return self.panels.on_workspace_closed()
    def _reposition_workspace(self):
        return self.panels._reposition_workspace()
    def _on_workspace_changed(self):
        return self.panels._on_workspace_changed()
    def _refresh_workspace_tooltip(self):
        return self.panels._refresh_workspace_tooltip()
    def _reposition_memory(self):
        return self.panels._reposition_memory()
    def on_memory_closed(self):
        return self.panels.on_memory_closed()
    def open_quotes_panel(self):
        return self.panels.open_quotes_panel()
    def _reposition_quotes(self):
        return self.panels._reposition_quotes()
    def on_quotes_closed(self):
        return self.panels.on_quotes_closed()
    def open_prompt_panel(self):
        return self.panels.open_prompt_panel()
    def _reposition_prompt(self):
        return self.panels._reposition_prompt()
    def on_prompt_closed(self):
        return self.panels.on_prompt_closed()
    def open_models_panel(self):
        return self.panels.open_models_panel()
    def _reposition_models(self):
        return self.panels._reposition_models()
    def on_models_closed(self):
        return self.panels.on_models_closed()
    def on_models_changed(self):
        return self.panels.on_models_changed()
    def open_extensions_panel(self):
        return self.panels.open_extensions_panel()
    def _reposition_extensions(self):
        return self.panels._reposition_extensions()
    def on_extensions_closed(self):
        return self.panels.on_extensions_closed()
    def on_extensions_changed(self):
        return self.panels.on_extensions_changed()
    def on_prompt_changed(self):
        return self.panels.on_prompt_changed()
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

    def schedule_reminder_timer(self):
        """按「下一个闹钟」设定单次 Timer，避免每秒空转。"""
        from datetime import datetime

        try:
            from agent.notes_store import next_due_alarm_at

            nxt = next_due_alarm_at()
        except Exception:
            nxt = None
        self.reminder_timer.stop()
        if nxt is None:
            return
        delay_ms = int((nxt - datetime.now()).total_seconds() * 1000)
        # 已到期或时钟回拨：尽快再查；最远 1 小时醒一次以免漏掉
        if delay_ms <= 0:
            delay_ms = 200
        else:
            delay_ms = min(max(delay_ms, 200), 3_600_000)
        self.reminder_timer.start(delay_ms)

    def check_reminders(self):
        """到期检查；结束后按下次闹钟重调度。"""
        messages: list[str] = []
        try:
            from agent.notes_store import pop_due_notes
            from agent.reminders import migrate_legacy_reminders

            migrate_legacy_reminders()
            for item in pop_due_notes():
                messages.append(item.get("content") or item.get("summary") or "提醒")
        except Exception:
            pass
        for text in messages:
            self.show_reminder_bubble(text)
        self.schedule_reminder_timer()

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
        <p><b>新对话</b>：聊天栏「＋」或右键「新对话」；各对话上下文独立</p>
        <p><b>Agent 工作台</b>：聊天栏「⛶」展开大窗口；改代码会暂存，对比后「保存/丢弃」</p>
        <p><b>模型设置</b>：右键「模型设置…」切换 DeepSeek / 通义 / 智谱 / Kimi / OpenAI / 聚合网关 / Ollama，或自定义 OpenAI 兼容端点</p>
        <p><b>右键 → 查看记事 / 记忆 / 语录 / Prompt 设置 / 模型设置 / 聊天记录 / 工作区</b></p>
        <p><b>Prompt 设置</b>：版本化 system prompt、A/B 分流；工作台消息可 👍👎，差评可生成改写候选并人工采纳</p>
        <p><b>工作区</b>：聊天栏「📁」打开文件夹，设定代码读写根目录</p>
        <p><b>待机语录</b>：空闲时偶尔冒泡；可在面板添加/删除，默认见 config/quotes.yaml</p>
        <p><b>聊天举例</b>：「记住我叫 Mini_Lu」「查看记忆」「聊天记录」「打开QQ」「工作区」</p>
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
