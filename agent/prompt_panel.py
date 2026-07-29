"""
Prompt 设置面板：版本编辑 / A/B / 反馈改写候选（人工确认）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from agent import prompt_store
from agent.frameless_move_resize import attach_move_resize, build_panel_header
from agent.ui_dialogs import ask_text, inform, warn
from .hover_tip import prepare_toplevel_show, seal_hidden_toplevel, screen_geometry_at


class PromptPanel(QWidget):
    closed = Signal()
    prompt_changed = Signal()  # 激活版本变化 → 主程序可 reset agent

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self._current_vid: str | None = None
        self._current_cid: str | None = None

        self.setStyleSheet(
            """
            QWidget#root {
                background: #FFF8F0;
                border: 2px solid #3D3D3D;
                border-radius: 12px;
            }
            QLabel#title { color: #2A2A2A; font-weight: 700; }
            QLabel#hint, QLabel#meta { color: #666; font-size: 11px; }
            QListWidget {
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                outline: none;
            }
            QListWidget::item { padding: 6px; }
            QListWidget::item:selected { background: #E8F0FE; }
            QTextEdit {
                background: #FFFFFF;
                border: 1px solid #D0C4B0;
                border-radius: 8px;
                padding: 6px;
                color: #222;
                font-family: Consolas, "Microsoft YaHei UI";
                font-size: 12px;
            }
            QPushButton {
                background: #4A90D9;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 6px 10px;
                font-weight: 600;
            }
            QPushButton:hover { background: #3A7BC8; }
            QPushButton#ghost {
                background: #F0EBE3;
                color: #444;
            }
            QPushButton#ghost:hover { background: #E4DDD2; }
            QPushButton#ok { background: #5A9E6F; }
            QPushButton#ok:hover { background: #4A8A5C; }
            QPushButton#danger { background: #C75B5B; }
            QPushButton#danger:hover { background: #B04949; }
            QPushButton#closeBtn {
                background: transparent; color: #666;
                font-size: 14px; padding: 2px 8px;
            }
            QPushButton#closeBtn:hover { color: #111; background: #EEE; }
            QCheckBox { color: #333; }
            """
        )

        root = QWidget(self)
        root.setObjectName("root")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)

        title = QLabel("Prompt 设置")
        title.setObjectName("title")
        title.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        close_btn = QPushButton("×")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.hide_panel)
        header = build_panel_header(title, close_btn)
        lay.addWidget(header)

        hint = QLabel(
            "版本化 system prompt · A/B 按会话稳定分流 · 差评可生成改写候选（需人工采纳）"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        ab_row = QHBoxLayout()
        self.ab_cb = QCheckBox("启用 A/B")
        self.ab_cb.stateChanged.connect(self._on_ab_toggle)
        ab_row.addWidget(self.ab_cb)
        ab_row.addWidget(QLabel("B 占比"))
        self.ab_ratio = QDoubleSpinBox()
        self.ab_ratio.setRange(0.0, 1.0)
        self.ab_ratio.setSingleStep(0.1)
        self.ab_ratio.setDecimals(2)
        self.ab_ratio.valueChanged.connect(self._on_ratio)
        ab_row.addWidget(self.ab_ratio)
        self.stats_lab = QLabel("")
        self.stats_lab.setObjectName("meta")
        ab_row.addWidget(self.stats_lab, 1)
        lay.addLayout(ab_row)

        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("版本列表"))
        self.ver_list = QListWidget()
        self.ver_list.currentItemChanged.connect(self._on_ver_select)
        ll.addWidget(self.ver_list, 1)
        vbtn = QHBoxLayout()
        b_a = QPushButton("设为 A")
        b_a.setObjectName("ok")
        b_a.clicked.connect(self._set_a)
        b_b = QPushButton("设为 B")
        b_b.clicked.connect(self._set_b)
        b_del = QPushButton("删除")
        b_del.setObjectName("danger")
        b_del.clicked.connect(self._delete_ver)
        b_sync = QPushButton("同步内置")
        b_sync.setObjectName("ghost")
        b_sync.setToolTip("从代码 DEFAULT 同步最新 system prompt 并激活（含 TSA 工具指引）")
        b_sync.clicked.connect(self._sync_builtin)
        vbtn.addWidget(b_a)
        vbtn.addWidget(b_b)
        vbtn.addWidget(b_sync)
        vbtn.addWidget(b_del)
        ll.addLayout(vbtn)
        split.addWidget(left)

        mid = QWidget()
        ml = QVBoxLayout(mid)
        ml.setContentsMargins(0, 0, 0, 0)
        self.meta_lab = QLabel("选择左侧版本编辑")
        self.meta_lab.setObjectName("meta")
        self.meta_lab.setWordWrap(True)
        ml.addWidget(self.meta_lab)
        self.editor = QTextEdit()
        self.editor.setPlaceholderText("system prompt 正文…")
        ml.addWidget(self.editor, 1)
        ebtn = QHBoxLayout()
        b_save = QPushButton("保存到此版本")
        b_save.clicked.connect(self._save_current)
        b_new = QPushButton("另存为新版本")
        b_new.setObjectName("ghost")
        b_new.clicked.connect(self._save_as_new)
        ebtn.addWidget(b_save)
        ebtn.addWidget(b_new)
        ml.addLayout(ebtn)
        split.addWidget(mid)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(QLabel("改写候选（待确认）"))
        self.cand_list = QListWidget()
        self.cand_list.currentItemChanged.connect(self._on_cand_select)
        rl.addWidget(self.cand_list, 1)
        self.cand_view = QTextEdit()
        self.cand_view.setReadOnly(True)
        self.cand_view.setPlaceholderText("选中候选查看改写正文与说明")
        rl.addWidget(self.cand_view, 1)
        cbtn = QHBoxLayout()
        b_gen = QPushButton("根据差评生成")
        b_gen.clicked.connect(self._generate_rewrite)
        b_acc = QPushButton("采纳并激活")
        b_acc.setObjectName("ok")
        b_acc.clicked.connect(self._accept_cand)
        b_rej = QPushButton("拒绝")
        b_rej.setObjectName("ghost")
        b_rej.clicked.connect(self._reject_cand)
        cbtn.addWidget(b_gen)
        cbtn.addWidget(b_acc)
        cbtn.addWidget(b_rej)
        rl.addLayout(cbtn)
        split.addWidget(right)

        split.setSizes([160, 280, 200])
        lay.addWidget(split, 1)

        self.path_lab = QLabel("")
        self.path_lab.setObjectName("meta")
        self.path_lab.setWordWrap(True)
        lay.addWidget(self.path_lab)

        self.reload()
        attach_move_resize(
            self,
            header,
            width=640,
            height=560,
            min_width=520,
            min_height=440,
        )
        seal_hidden_toplevel(self)

    def reload(self):
        settings = prompt_store.get_settings()
        self.ab_cb.blockSignals(True)
        self.ab_cb.setChecked(bool(settings.get("ab_enabled")))
        self.ab_cb.blockSignals(False)
        self.ab_ratio.blockSignals(True)
        self.ab_ratio.setValue(float(settings.get("ab_ratio_b") or 0.5))
        self.ab_ratio.blockSignals(False)
        st = prompt_store.feedback_stats()
        self.stats_lab.setText(
            f"反馈 👍{st.get('up', 0)} / 👎{st.get('down', 0)} · "
            f"A={settings.get('active_a')} · B={settings.get('active_b')}"
        )
        self.path_lab.setText(f"存储: {settings.get('path')}")

        keep = self._current_vid
        self.ver_list.clear()
        versions = prompt_store.list_versions()
        row = 0
        for i, v in enumerate(versions):
            marks = []
            if v.get("id") == settings.get("active_a"):
                marks.append("A")
            if v.get("id") == settings.get("active_b"):
                marks.append("B")
            tag = f"[{'/'.join(marks)}] " if marks else ""
            stats = v.get("stats") or {}
            item = QListWidgetItem(
                f"{tag}{v.get('name')} · 👍{stats.get('up', 0)}👎{stats.get('down', 0)}"
            )
            item.setData(Qt.UserRole, v.get("id"))
            self.ver_list.addItem(item)
            if keep and v.get("id") == keep:
                row = i
        if versions:
            self.ver_list.setCurrentRow(row)

        self.cand_list.clear()
        self._current_cid = None
        for c in prompt_store.list_candidates(status="pending"):
            item = QListWidgetItem(f"{c.get('id')} · {c.get('ts')}")
            item.setData(Qt.UserRole, c.get("id"))
            self.cand_list.addItem(item)
        self.cand_view.clear()

    def _on_ab_toggle(self, _state):
        prompt_store.set_ab_enabled(self.ab_cb.isChecked())
        self.reload()
        self.prompt_changed.emit()

    def _on_ratio(self, val: float):
        prompt_store.set_ab_ratio_b(val)

    def _on_ver_select(self, current: QListWidgetItem | None, _prev):
        if not current:
            return
        vid = str(current.data(Qt.UserRole))
        self._current_vid = vid
        v = prompt_store.get_version(vid)
        if not v:
            return
        self.meta_lab.setText(
            f"{v.get('name')} · {v.get('id')} · {v.get('source')} · {v.get('created_at')}\n"
            f"{v.get('note') or ''}"
        )
        self.editor.setPlainText(v.get("text") or "")

    def _set_a(self):
        if not self._current_vid:
            return
        prompt_store.set_active_a(self._current_vid)
        self.reload()
        self.prompt_changed.emit()

    def _set_b(self):
        if not self._current_vid:
            return
        prompt_store.set_active_b(self._current_vid)
        self.reload()
        self.prompt_changed.emit()

    def _sync_builtin(self):
        try:
            item = prompt_store.sync_builtin_prompt(activate=True, name="内置默认(TSA)")
        except Exception as e:
            warn(self, "Prompt", f"同步失败: {e}")
            return
        reused = "（已是最新）" if item.get("reused") else ""
        inform(
            self,
            "Prompt",
            f"已同步并激活内置 DEFAULT{reused}\nid={item.get('id')}\n"
            "含 TSA：read_outline / list_symbols / find_callers 等指引。",
        )
        self.reload()
        self.prompt_changed.emit()

    def _delete_ver(self):
        if not self._current_vid:
            return
        msg = prompt_store.delete_version(self._current_vid)
        if msg.startswith("至少") or msg.startswith("版本不存在"):
            warn(self, "Prompt", msg)
            return
        self._current_vid = None
        self.reload()
        self.prompt_changed.emit()

    def _save_current(self):
        if not self._current_vid:
            return
        text = self.editor.toPlainText()
        msg = prompt_store.update_version_text(self._current_vid, text)
        inform(self, "Prompt", msg)
        self.reload()
        self.prompt_changed.emit()

    def _save_as_new(self):
        text = self.editor.toPlainText().strip()
        if not text:
            warn(self, "Prompt", "正文为空")
            return
        name, ok = ask_text(self, "新版本", "版本名称：", text="手动版本", ok_text="创建")
        if not ok:
            return
        ver = prompt_store.add_version(
            text, name=(name or "").strip() or "手动版本", source="manual"
        )
        self._current_vid = ver["id"]
        self.reload()
        inform(self, "Prompt", f"已创建 {ver['id']}（未自动激活）")

    def _on_cand_select(self, current: QListWidgetItem | None, _prev):
        if not current:
            return
        cid = str(current.data(Qt.UserRole))
        self._current_cid = cid
        c = prompt_store.get_candidate(cid)
        if not c:
            return
        base = prompt_store.get_version(str(c.get("base_version_id") or ""))
        self.cand_view.setPlainText(
            f"说明: {c.get('rationale') or ''}\n"
            f"基于版本: {(base or {}).get('name') or c.get('base_version_id')}\n"
            f"{'=' * 40}\n"
            f"{c.get('proposed_text') or ''}"
        )

    def _generate_rewrite(self):
        try:
            from agent.prompt_optimizer import propose_rewrite_from_feedback

            cand = propose_rewrite_from_feedback()
        except Exception as e:
            warn(self, "生成失败", str(e))
            return
        self.reload()
        inform(
            self,
            "Prompt",
            f"已生成候选 {cand.get('id')}，请在右侧审阅后「采纳」或「拒绝」。",
        )

    def _accept_cand(self):
        if not self._current_cid:
            return
        msg = prompt_store.accept_candidate(self._current_cid, activate=True)
        self._current_cid = None
        self.reload()
        self.prompt_changed.emit()
        inform(self, "Prompt", msg)

    def _reject_cand(self):
        if not self._current_cid:
            return
        msg = prompt_store.reject_candidate(self._current_cid)
        self._current_cid = None
        self.reload()
        inform(self, "Prompt", msg)

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

        from agent.hover_tip import screen_geometry_at

        x = global_x - self.width() - 8
        y = global_y + 10
        screen = screen_geometry_at(QPoint(global_x + pet_w // 2, global_y + pet_h // 2))
        if x < screen.left() + 8:
            x = global_x + pet_w + 8
        x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
        y = max(screen.top() + 8, min(y, screen.bottom() - self.height() - 8))
        self.move(x, y)
