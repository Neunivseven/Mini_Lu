"""模型设置：清晰展示当前 API，自选 Chat/ASR/Vision，并标记多模态能力。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.providers import load_models_config, reset_hub
from agent.providers.registry import (
    ensure_custom_openai,
    list_providers,
    provider_summary,
    set_active,
    set_provider_fields,
)
from agent.ui_dialogs import inform, warn
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at


def _cap_label(caps: list[str] | None) -> str:
    caps = list(caps or [])
    bits = []
    if "text" in caps:
        bits.append("文本")
    if "image" in caps:
        bits.append("图片")
    if "video" in caps:
        bits.append("视频")
    return " · ".join(bits) if bits else "未声明"


class ModelsPanel(QWidget):
    closed = Signal()
    models_changed = Signal()
    close_requested = Signal()  # 嵌入工作台时关闭选项卡

    def __init__(self, parent=None, *, embedded: bool = False):
        super().__init__(parent)
        self._embedded = bool(embedded)
        if not self._embedded:
            self.setWindowFlags(
                Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
            )
        self.apply_theme(None)

        root = QWidget(self)
        root.setObjectName("rootEmbed" if self._embedded else "root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)

        main_lay = QVBoxLayout(root)
        main_lay.setContentsMargins(4 if self._embedded else 12, 4 if self._embedded else 10, 4 if self._embedded else 12, 4 if self._embedded else 10)
        main_lay.setSpacing(8)

        if not self._embedded:
            title = QLabel("模型设置")
            title.setObjectName("title")
            title.setFont(QFont("Microsoft YaHei UI", 13, QFont.Bold))
            close_btn = QPushButton("×")
            close_btn.setObjectName("closeBtn")
            close_btn.clicked.connect(self.hide_panel)
            header = build_panel_header(title, close_btn)
            main_lay.addWidget(header)
            self._header = header
        else:
            self._header = None
            emb_row = QHBoxLayout()
            emb_title = QLabel("模型与 API")
            emb_title.setObjectName("sec")
            emb_row.addWidget(emb_title, 1)
            emb_close = QPushButton("关闭")
            emb_close.setObjectName("ghost")
            emb_close.setToolTip("关闭模型配置选项卡，返回聊天")
            emb_close.clicked.connect(self.close_requested.emit)
            emb_row.addWidget(emb_close)
            main_lay.addLayout(emb_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(4, 2, 4, 8)
        lay.setSpacing(10)
        scroll.setWidget(body)
        main_lay.addWidget(scroll, 1)

        self.active_card = QFrame()
        self.active_card.setObjectName("activeCard")
        ac = QVBoxLayout(self.active_card)
        ac.setContentsMargins(12, 10, 12, 10)
        ac.setSpacing(6)
        head_row = QHBoxLayout()
        now_tag = QLabel("当前对话 API")
        now_tag.setObjectName("hint")
        head_row.addWidget(now_tag)
        head_row.addStretch(1)
        self.badge_row = QHBoxLayout()
        self.badge_row.setSpacing(6)
        head_row.addLayout(self.badge_row)
        ac.addLayout(head_row)
        self.active_name = QLabel("—")
        self.active_name.setObjectName("activeName")
        ac.addWidget(self.active_name)
        self.active_sub = QLabel("")
        self.active_sub.setObjectName("activeSub")
        self.active_sub.setWordWrap(True)
        ac.addWidget(self.active_sub)
        self.route_hint = QLabel("")
        self.route_hint.setObjectName("hint")
        self.route_hint.setWordWrap(True)
        ac.addWidget(self.route_hint)
        lay.addWidget(self.active_card)

        sec1 = QLabel("① 选择对话模型")
        sec1.setObjectName("sec")
        lay.addWidget(sec1)
        tip1 = QLabel("点选下方列表切换预览；改完密钥/能力后点「应用并切换」。")
        tip1.setObjectName("hint")
        tip1.setWordWrap(True)
        lay.addWidget(tip1)

        self.provider_list = QListWidget()
        self.provider_list.setObjectName("providerList")
        self.provider_list.setMinimumHeight(160)
        self.provider_list.setMaximumHeight(220)
        self.provider_list.currentRowChanged.connect(self._on_list_select)
        lay.addWidget(self.provider_list)

        # 详情编辑区
        detail = QFrame()
        detail.setObjectName("sectionBox")
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(10, 8, 10, 8)
        dl.setSpacing(6)
        self.detail_title = QLabel("模型详情")
        self.detail_title.setObjectName("sec")
        dl.addWidget(self.detail_title)
        self.meta = QLabel("")
        self.meta.setObjectName("hint")
        self.meta.setWordWrap(True)
        dl.addWidget(self.meta)

        cap_row = QHBoxLayout()
        cap_lab = QLabel("多模态能力（本模型能直接理解）：")
        cap_lab.setObjectName("hint")
        cap_row.addWidget(cap_lab)
        self.chk_text = QCheckBox("文本")
        self.chk_text.setChecked(True)
        self.chk_text.setEnabled(False)  # chat 至少文本
        self.chk_image = QCheckBox("图片")
        self.chk_image.setToolTip(
            "勾选后：附图会直传该 Chat，不再先走独立识图 API。\n"
            "仅当模型本身支持看图时请勾选（如豆包多模态、GPT-4o）。"
        )
        cap_row.addWidget(self.chk_text)
        cap_row.addWidget(self.chk_image)
        cap_row.addStretch(1)
        dl.addLayout(cap_row)

        dl.addWidget(QLabel("API Key（写入本地配置，勿外传）"))
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("留空则不改；环境变量优先")
        dl.addWidget(self.key_edit)

        dl.addWidget(QLabel("接口地址 base_url"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://api.example.com/v1")
        dl.addWidget(self.url_edit)

        dl.addWidget(QLabel("模型名 model"))
        self.model_edit = QLineEdit()
        dl.addWidget(self.model_edit)

        row = QHBoxLayout()
        btn_apply = QPushButton("应用并切换为对话 API")
        btn_apply.clicked.connect(self._apply_chat)
        row.addWidget(btn_apply, 1)
        btn_reload = QPushButton("刷新")
        btn_reload.setObjectName("ghost")
        btn_reload.clicked.connect(self.reload)
        row.addWidget(btn_reload)
        dl.addLayout(row)
        lay.addWidget(detail)

        sec2 = QLabel("② 旁路能力（文本模型看不了图/听不了音时用）")
        sec2.setObjectName("sec")
        lay.addWidget(sec2)
        tip2 = QLabel(
            "对话模型若不支持图片，发送附图会先走「识图」转成文字再交给对话模型。"
        )
        tip2.setObjectName("hint")
        tip2.setWordWrap(True)
        lay.addWidget(tip2)

        side = QFrame()
        side.setObjectName("sectionBox")
        sl = QVBoxLayout(side)
        sl.setContentsMargins(10, 8, 10, 8)
        sl.setSpacing(6)

        sl.addWidget(QLabel("语音识别 ASR"))
        self.asr_combo = QComboBox()
        sl.addWidget(self.asr_combo)

        sl.addWidget(QLabel("图像识别 Vision（降级用）"))
        self.vision_combo = QComboBox()
        sl.addWidget(self.vision_combo)

        btn_side = QPushButton("保存旁路 ASR / Vision")
        btn_side.setObjectName("ghost")
        btn_side.clicked.connect(self._apply_side)
        sl.addWidget(btn_side)
        lay.addWidget(side)

        sec3 = QLabel("③ 自定义 OpenAI 兼容端点")
        sec3.setObjectName("sec")
        lay.addWidget(sec3)
        custom = QFrame()
        custom.setObjectName("sectionBox")
        cl = QVBoxLayout(custom)
        cl.setContentsMargins(10, 8, 10, 8)
        cl.setSpacing(6)
        self.custom_url = QLineEdit()
        self.custom_url.setPlaceholderText("https://api.xxx.com/v1")
        cl.addWidget(self.custom_url)
        self.custom_model = QLineEdit()
        self.custom_model.setPlaceholderText("model id")
        cl.addWidget(self.custom_model)
        self.custom_key = QLineEdit()
        self.custom_key.setEchoMode(QLineEdit.Password)
        self.custom_key.setPlaceholderText("API Key（可选）")
        cl.addWidget(self.custom_key)
        self.custom_vision = QCheckBox("此端点支持看图（多模态）")
        self.custom_vision.setToolTip("勾选后会写入 capabilities: [text, image]")
        cl.addWidget(self.custom_vision)
        btn_custom = QPushButton("保存为 custom_openai 并设为对话 API")
        btn_custom.clicked.connect(self._apply_custom)
        cl.addWidget(btn_custom)
        lay.addWidget(custom)

        lay.addStretch(1)

        self._chat_ids: list[str] = []
        self._asr_ids: list[str] = []
        self._vision_ids: list[str] = []

        if not self._embedded and self._header is not None:
            attach_move_resize(
                self,
                self._header,
                width=460,
                height=640,
                min_width=400,
                min_height=480,
            )
            seal_hidden_toplevel(self)
        elif self._embedded:
            self.setMinimumWidth(280)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


    def _clear_badges(self):
        while self.badge_row.count():
            item = self.badge_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _add_badge(self, text: str, kind: str):
        lab = QLabel(text)
        lab.setObjectName(
            {"text": "badgeText", "image": "badgeImg"}.get(kind, "badgeOff")
        )
        self.badge_row.addWidget(lab)

    def _update_active_card(self, cfg, active_id: str):
        self._clear_badges()
        if not active_id:
            self.active_name.setText("未选择对话模型")
            self.active_sub.setText("请在下方列表选择并点击「应用并切换」。")
            self.route_hint.setText("")
            self._add_badge("未配置", "off")
            return
        try:
            sp = cfg.spec(active_id)
            s = provider_summary(sp)
        except Exception as e:
            self.active_name.setText(active_id)
            self.active_sub.setText(str(e))
            self.route_hint.setText("")
            return

        caps = s.get("capabilities") or []
        self.active_name.setText(f"{s['label']}")
        key_st = "密钥已配置" if s["has_key"] else "密钥未配置"
        self.active_sub.setText(
            f"ID: {s['id']}  ·  模型: {s['model'] or '—'}\n"
            f"{s['base_url'] or '（无 base_url）'}\n"
            f"{key_st}  ·  env={s['api_key_env'] or '—'}"
        )
        if "text" in caps:
            self._add_badge("文本", "text")
        if "image" in caps:
            self._add_badge("图片", "image")
        if "video" in caps:
            self._add_badge("视频", "image")
        if not caps:
            self._add_badge("能力未声明", "off")

        asr = cfg.active_id("asr") or "未启用"
        vision = cfg.active_id("vision") or "未启用"
        if "image" in caps:
            self.route_hint.setText(
                f"附图：直传本对话模型（不经独立识图）。\n"
                f"旁路 · ASR={asr}  ·  Vision={vision}（文本模型降级时仍可用）"
            )
        else:
            self.route_hint.setText(
                f"附图：先走 Vision「{vision}」转文字，再交给本对话模型。\n"
                f"旁路 · ASR={asr}  ·  Vision={vision}"
            )

    def apply_theme(self, theme=None) -> None:
        """按工作台主题刷新样式；theme=None 时用默认白色。"""
        if theme is None:
            try:
                from agent.studio_theme import get_theme

                theme = get_theme("white")
            except Exception:
                theme = None
        if theme is None:
            cloth, bg, surface, panel = "#3D7EA6", "#F3F6FA", "#E8EEF5", "#FFFFFF"
            ink, muted, border = "#1E293B", "#64748B", "#C5D0DC"
            list_sel, list_hover = "#DCEAF5", "#EEF2F7"
            ghost_bg, ghost_hover = "#E8EEF5", "#D5DEE8"
            badge_off_bg = "#F1F5F9"
        else:
            cloth, bg, surface, panel = theme.cloth, theme.bg, theme.surface, theme.panel
            ink, muted, border = theme.ink, theme.muted, theme.border
            list_sel, list_hover = theme.list_sel, theme.list_hover
            ghost_bg, ghost_hover = theme.ghost_bg, theme.ghost_hover
            badge_off_bg = surface
        if theme is not None and getattr(theme, "is_dark", False):
            badge_text_bg, badge_text_fg = "#1E3A5F", "#93C5FD"
            badge_img_bg, badge_img_fg = "#064E3B", "#6EE7B7"
        else:
            badge_text_bg, badge_text_fg = "#DBEAFE", "#1D4ED8"
            badge_img_bg, badge_img_fg = "#D1FAE5", "#047857"
        self.setStyleSheet(
            f"""
            QWidget#root {{
                background: {bg};
                border: 2px solid {cloth};
                border-radius: 12px;
            }}
            QWidget#rootEmbed {{
                background: transparent;
                border: none;
            }}
            QLabel#title {{ color: {ink}; font-weight: 700; }}
            QLabel#sec {{ color: {ink}; font-weight: 700; font-size: 12px; }}
            QLabel#hint {{ color: {muted}; font-size: 11px; }}
            QFrame#activeCard {{
                background: {panel};
                border: 2px solid {cloth};
                border-radius: 10px;
            }}
            QLabel#activeName {{
                color: {ink};
                font-weight: 800;
                font-size: 15px;
            }}
            QLabel#activeSub {{ color: {muted}; font-size: 11px; }}
            QLabel#badgeText, QLabel#badgeImg, QLabel#badgeOff {{
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: 700;
            }}
            QLabel#badgeText {{
                background: {badge_text_bg};
                color: {badge_text_fg};
            }}
            QLabel#badgeImg {{
                background: {badge_img_bg};
                color: {badge_img_fg};
            }}
            QLabel#badgeOff {{
                background: {badge_off_bg};
                color: {muted};
            }}
            QListWidget#providerList {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 8px;
                outline: none;
                padding: 4px;
            }}
            QListWidget#providerList::item {{
                padding: 8px 10px;
                border-radius: 6px;
                margin: 2px 0;
                color: {ink};
            }}
            QListWidget#providerList::item:selected {{
                background: {list_sel};
                border: 1px solid {cloth};
                color: {ink};
                font-weight: 600;
            }}
            QListWidget#providerList::item:hover:!selected {{
                background: {list_hover};
            }}
            QComboBox, QLineEdit {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 6px 8px;
                color: {ink};
            }}
            QCheckBox {{ color: {ink}; font-size: 12px; spacing: 6px; }}
            QPushButton {{
                background: {cloth};
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 700;
            }}
            QPushButton:hover {{ background: {cloth}; }}
            QPushButton#ghost {{
                background: {ghost_bg};
                color: {ink};
            }}
            QPushButton#ghost:hover {{ background: {ghost_hover}; }}
            QPushButton#closeBtn {{
                background: transparent;
                color: {muted};
                font-size: 16px;
                padding: 2px 8px;
            }}
            QFrame#sectionBox {{
                background: {panel};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            """
        )

    def reload(self):
        reset_hub()
        cfg = load_models_config()
        active = cfg.active_id("chat") or ""

        self.provider_list.blockSignals(True)
        self.provider_list.clear()
        self._chat_ids = []
        for sp in list_providers(capability="chat", cfg=cfg):
            s = provider_summary(sp)
            key_mark = "✓" if s["has_key"] else "○"
            caps = _cap_label(s.get("capabilities"))
            using = "  ← 使用中" if sp.id == active else ""
            text = f"{key_mark}  {s['label']}\n     {sp.id}  ·  {caps}{using}"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, sp.id)
            if sp.id == active:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self.provider_list.addItem(item)
            self._chat_ids.append(sp.id)
        if active in self._chat_ids:
            self.provider_list.setCurrentRow(self._chat_ids.index(active))
        elif self._chat_ids:
            self.provider_list.setCurrentRow(0)
        self.provider_list.blockSignals(False)

        # ASR
        self.asr_combo.blockSignals(True)
        self.asr_combo.clear()
        self._asr_ids = []
        self.asr_combo.addItem("（关闭）")
        self._asr_ids.append("")
        for sp in list_providers(capability="asr", cfg=cfg):
            s = provider_summary(sp)
            self.asr_combo.addItem(f"{s['label']} ({sp.id})")
            self._asr_ids.append(sp.id)
        asr_id = cfg.active_id("asr") or ""
        if asr_id in self._asr_ids:
            self.asr_combo.setCurrentIndex(self._asr_ids.index(asr_id))
        self.asr_combo.blockSignals(False)

        # Vision
        self.vision_combo.blockSignals(True)
        self.vision_combo.clear()
        self._vision_ids = []
        self.vision_combo.addItem("（关闭）")
        self._vision_ids.append("")
        for sp in list_providers(capability="vision", cfg=cfg):
            s = provider_summary(sp)
            self.vision_combo.addItem(f"{s['label']} ({sp.id})")
            self._vision_ids.append(sp.id)
        vision_id = cfg.active_id("vision") or ""
        if vision_id in self._vision_ids:
            self.vision_combo.setCurrentIndex(self._vision_ids.index(vision_id))
        self.vision_combo.blockSignals(False)

        self._update_active_card(cfg, active)
        self._on_list_select()

    def _current_chat_id(self) -> str | None:
        item = self.provider_list.currentItem()
        if not item:
            return None
        return str(item.data(Qt.UserRole) or "") or None

    def _on_list_select(self, *_):
        pid = self._current_chat_id()
        if not pid:
            self.meta.setText("")
            self.detail_title.setText("模型详情")
            return
        try:
            cfg = load_models_config()
            sp = cfg.spec(pid)
            s = provider_summary(sp)
            active = cfg.active_id("chat") == pid
            self.detail_title.setText(
                f"编辑：{s['label']}" + ("（当前使用中）" if active else "")
            )
            self.meta.setText(
                f"驱动 {s['driver']}  ·  "
                f"Key {'已配置' if s['has_key'] else '未配置'}  ·  "
                f"env={s['api_key_env'] or '—'}"
            )
            caps = set(s.get("capabilities") or [])
            self.chk_image.blockSignals(True)
            self.chk_image.setChecked("image" in caps)
            self.chk_image.blockSignals(False)
            self.url_edit.setText(s.get("base_url") or "")
            self.model_edit.setText(s["model"])
            self.key_edit.clear()
        except Exception as e:
            self.meta.setText(str(e))

    def _apply_chat(self):
        pid = self._current_chat_id()
        if not pid:
            return
        try:
            model = self.model_edit.text().strip()
            url = self.url_edit.text().strip()
            key = self.key_edit.text().strip()
            caps = ["text"]
            if self.chk_image.isChecked():
                caps.append("image")
            fields: dict = {"capabilities": caps}
            if model:
                fields["model"] = model
            if url:
                fields["base_url"] = url
            if key:
                fields["api_key"] = key
            set_provider_fields(pid, **fields)
            set_active("chat", pid)
        except Exception as e:
            warn(self, "模型设置", str(e))
            return
        self.reload()
        self.models_changed.emit()
        inform(
            self,
            "模型设置",
            f"已切换对话 API → {pid}\n能力：{_cap_label(caps)}",
        )

    def _apply_side(self):
        try:
            ai = self.asr_combo.currentIndex()
            vi = self.vision_combo.currentIndex()
            asr_id = self._asr_ids[ai] if 0 <= ai < len(self._asr_ids) else ""
            vision_id = (
                self._vision_ids[vi] if 0 <= vi < len(self._vision_ids) else ""
            )
            set_active("asr", asr_id or None)
            set_active("vision", vision_id or None)
        except Exception as e:
            warn(self, "旁路能力", str(e))
            return
        self.reload()
        self.models_changed.emit()
        inform(
            self,
            "旁路能力",
            f"ASR → {asr_id or '关闭'}\nVision → {vision_id or '关闭'}",
        )

    def _apply_custom(self):
        try:
            msg = ensure_custom_openai(
                base_url=self.custom_url.text(),
                model=self.custom_model.text(),
                api_key=self.custom_key.text(),
                set_as_chat=True,
            )
            caps = ["text", "image"] if self.custom_vision.isChecked() else ["text"]
            set_provider_fields("custom_openai", capabilities=caps)
        except Exception as e:
            warn(self, "自定义端点", str(e))
            return
        self.reload()
        self.models_changed.emit()
        inform(self, "自定义端点", msg + f"\n能力：{_cap_label(caps)}")

    def show_panel(self):
        prepare_toplevel_show(self, activate=True)
        self.reload()
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
