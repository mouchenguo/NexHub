"""插件工作空间页。

每个已加载插件对应一个选项卡：状态、manifest 快捷控件、宿主控制。
真正的插件配置窗口仍在独立进程里通过“打开配置”弹出，不嵌入主窗口。
"""
from __future__ import annotations

import json

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon as FIF, StrongBodyLabel

from ..widgets.common import (
    Banner,
    ask_confirm,
    clear_layout,
    make_button,
    make_card,
    section_header,
)
from ..file_utils import human_size
from ..widgets.plugin_controls import (
    MetricPanel,
    apply_status_pill,
    build_control_widgets,
    plugin_avatar,
    plugin_status_pill,
    state_hints,
)
from ..workers import FunctionWorker
from .base import BasePage


class PluginWorkspacePage(BasePage):
    """单个插件的工作空间页（page_id = ``plugin:<id>``）。"""

    def __init__(self, ctx, plugin_id: str, parent=None):
        self._plugin_id = plugin_id
        record = ctx.plugins.get(plugin_id)
        self.page_id = f"plugin:{plugin_id}"
        self.title = record.name if record else plugin_id
        self.subtitle = (
            f"插件工作空间 · v{record.version}" if record else "插件工作空间"
        )
        self._state: dict = {}
        self._worker: FunctionWorker | None = None
        self._controls_key: tuple | None = None
        self._hints_key: tuple | None = None
        self._actions_key: tuple | None = None
        self._metrics_key: tuple | None = None
        self._metrics_panel: MetricPanel | None = None
        self._last_freed_label: CaptionLabel | None = None
        self._has_config_window = True
        super().__init__(ctx, parent)
        self.ctx.plugin_processes.changed.connect(self.refresh)
        self._monitor = QTimer(self)
        self._monitor.setInterval(1000)
        self._monitor.timeout.connect(self._poll_state)
        self._monitor.start()

    # ------------------------------------------------------------------ 构建
    def build(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        self._has_config_window = record.has_config_window if record else True
        if self._has_config_window:
            hint_text = (
                "插件在独立进程中运行，崩溃不会影响主程序；"
                "完整配置请点击「打开配置窗口」。"
            )
        else:
            hint_text = (
                "插件在独立进程中运行，崩溃不会影响主程序；"
                "该插件没有独立配置窗口，所有操作都在本页完成。"
            )
        self.content_layout.addWidget(Banner(hint_text, FIF.INFO))

        self.info_card, self.info_layout = make_card()
        top = QHBoxLayout()
        top.setSpacing(12)
        self.avatar_host = QWidget()
        self.avatar_layout = QHBoxLayout(self.avatar_host)
        self.avatar_layout.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self.avatar_host)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        self.name_label = StrongBodyLabel("插件")
        self.meta_label = CaptionLabel("")
        self.description_label = CaptionLabel("")
        self.description_label.setWordWrap(True)
        text_box.addWidget(self.name_label)
        text_box.addWidget(self.meta_label)
        text_box.addWidget(self.description_label)
        top.addLayout(text_box, 1)
        self.status_pill = plugin_status_pill(False)
        top.addWidget(self.status_pill)
        self.info_layout.addLayout(top)
        self.content_layout.addWidget(self.info_card)

        self.controls_card, self.controls_layout = make_card()
        header, _actions = section_header("快捷控制", "来自插件 manifest.controls")
        self.controls_layout.addWidget(header)
        self.controls_host = QWidget()
        self.controls_row = QHBoxLayout(self.controls_host)
        self.controls_row.setContentsMargins(0, 0, 0, 0)
        self.controls_row.setSpacing(8)
        self.controls_layout.addWidget(self.controls_host)
        self.content_layout.addWidget(self.controls_card)

        self.metrics_card, self.metrics_layout = make_card()
        metrics_header, _actions = section_header("运行状态", "来自插件宿主实时上报")
        self.metrics_layout.addWidget(metrics_header)
        self.metrics_host = QVBoxLayout()
        self.metrics_host.setContentsMargins(0, 0, 0, 0)
        self.metrics_host.setSpacing(6)
        self.metrics_layout.addLayout(self.metrics_host)
        self.metrics_card.setVisible(False)
        self.content_layout.addWidget(self.metrics_card)

        self.hints_host = QWidget()
        self.hints_layout = QVBoxLayout(self.hints_host)
        self.hints_layout.setContentsMargins(0, 0, 0, 0)
        self.hints_layout.setSpacing(4)
        self.content_layout.addWidget(self.hints_host)

        self.actions_card, self.actions_layout = make_card()
        self.actions_layout.addWidget(
            section_header("宿主与工作空间", "启动 / 停止插件进程，或调整选项卡")[0]
        )
        self.actions_row = QHBoxLayout()
        self.actions_row.setSpacing(8)
        self.actions_layout.addLayout(self.actions_row)
        self.content_layout.addWidget(self.actions_card)
        self.content_layout.addStretch(1)

        self._avatar_key: tuple | None = None
        self._state_signature = ""

    # ------------------------------------------------------------------ 刷新
    def refresh(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        if record is None:
            self._render_missing()
            return
        running = self.ctx.plugin_processes.is_running(self._plugin_id)
        state = self.ctx.plugin_processes.state(self._plugin_id) if running else {}
        self._state = state if isinstance(state, dict) else {}
        self._state_signature = self._signature(self._state)

        self.title_label.setText(record.name)
        self.subtitle_label.setText(f"插件工作空间 · v{record.version}")
        self.name_label.setText(record.name)
        meta = f"ID：{record.plugin_id} · v{record.version} · {record.category}"
        if record.requirements:
            meta += f" · 依赖 {record.requirements}"
        self.meta_label.setText(meta)
        self.description_label.setText(record.description or "暂无简介")

        # 有 enabled 状态的插件（如代理）以服务状态为准，避免把“宿主在跑”显示成运行中
        service_running = (
            bool(self._state.get("enabled")) if "enabled" in self._state else running
        )
        apply_status_pill(self.status_pill, service_running)

        self._render_avatar(record)
        self._render_controls(record, running)
        self._render_metrics(record, running)
        self._render_hints(running)
        self._render_actions(record, running)

    def _render_avatar(self, record) -> None:
        key = (str(record.icon_path or ""), record.glyph)
        if key == self._avatar_key:
            return
        self._avatar_key = key
        clear_layout(self.avatar_layout)
        self.avatar_layout.addWidget(plugin_avatar(record.glyph, record.icon_path))

    def _render_controls(self, record, running: bool) -> None:
        state = self._state
        key = (
            record.plugin_id,
            record.version,
            running,
            bool(state.get("enabled", False)),
            str(state.get("node") or ""),
            str(state.get("mode") or ""),
            bool(state.get("nodes")),
        )
        if key == self._controls_key:
            return
        self._controls_key = key
        clear_layout(self.controls_row)
        for widget in build_control_widgets(
            record, state, running, self._quick_action
        ):
            self.controls_row.addWidget(widget)
        self.controls_row.addStretch(1)

    def _render_metrics(self, record, running: bool) -> None:
        """按 manifest.metrics 展示实时指标。

        只在“结构”变化（插件/版本/运行状态/指标定义）时重建控件，
        数值变化一律就地更新，避免每秒重建导致进度条闪烁。
        """
        metrics = record.metrics
        if not metrics:
            self.metrics_card.setVisible(False)
            self._metrics_key = None
            self._metrics_panel = None
            self._last_freed_label = None
            return
        self.metrics_card.setVisible(True)

        spec_key = (
            record.plugin_id,
            record.version,
            running,
            tuple(str(item.get("key") or "") for item in metrics),
        )
        if spec_key != self._metrics_key:
            self._metrics_key = spec_key
            self._metrics_panel = None
            self._last_freed_label = None
            clear_layout(self.metrics_host)
            if not running:
                self.metrics_host.addWidget(CaptionLabel("启动宿主后显示实时状态。"))
                return
            self._metrics_panel = MetricPanel(metrics)
            self.metrics_host.addWidget(self._metrics_panel)
            self._last_freed_label = CaptionLabel("")
            self._last_freed_label.setWordWrap(True)
            self._last_freed_label.setStyleSheet("color: #3fb950;")
            self._last_freed_label.setVisible(False)
            self.metrics_host.addWidget(self._last_freed_label)

        if self._metrics_panel is not None:
            self._metrics_panel.update_state(self._state)

        if self._last_freed_label is not None:
            freed = self._state.get("last_freed")
            if freed:
                stamp = str(self._state.get("last_optimized_at") or "")
                text = f"上次优化：释放 {human_size(int(freed))}"
                if stamp:
                    text += f"（{stamp}）"
                self._last_freed_label.setText(text)
                self._last_freed_label.setVisible(True)
            else:
                self._last_freed_label.setVisible(False)

    def _render_hints(self, running: bool) -> None:
        hints = state_hints(self._state, running)
        key = tuple(hints)
        if key == self._hints_key:
            return
        self._hints_key = key
        clear_layout(self.hints_layout)
        for text, color in hints:
            label = CaptionLabel(text)
            label.setWordWrap(True)
            if color:
                label.setStyleSheet(f"color: {color};")
            self.hints_layout.addWidget(label)

    def _render_actions(self, record, running: bool) -> None:
        key = (record.plugin_id, running, self._has_config_window)
        if key == self._actions_key:
            return
        self._actions_key = key
        clear_layout(self.actions_row)
        self.actions_row.addWidget(
            make_button(
                "停止宿主" if running else "启动宿主",
                primary=not running,
                icon=FIF.PAUSE if running else FIF.PLAY,
                on_click=self._toggle_host,
            )
        )
        if self._has_config_window:
            self.actions_row.addWidget(
                make_button(
                    "打开配置窗口",
                    icon=FIF.SETTING,
                    on_click=self._open_config,
                )
            )
        self.actions_row.addStretch(1)
        self.actions_row.addWidget(
            make_button(
                "从工作空间移除",
                transparent=True,
                icon=FIF.REMOVE,
                on_click=self._remove_from_workspace,
            )
        )
        self.actions_row.addWidget(
            make_button(
                "卸载插件",
                transparent=True,
                icon=FIF.DELETE,
                on_click=self._uninstall,
            )
        )

    # ------------------------------------------------------------------ 交互
    def _quick_action(self, action: str, payload: dict) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        try:
            if not self.ctx.plugin_processes.is_running(self._plugin_id):
                record = self.ctx.plugins.get(self._plugin_id)
                if record is None:
                    return
                self.ctx.plugin_processes.start(record, hidden=True)
                self.ctx.set_plugin_active(self._plugin_id, True)
            worker = FunctionWorker(
                lambda: self.ctx.plugin_processes.request(
                    self._plugin_id,
                    action,
                    payload,
                    120.0 if action in ("optimize", "choose_node") else 30.0,
                ),
                parent=self,
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"插件操作失败：{exc}", "error")
            self.refresh()
            return
        self._worker = worker

        def succeeded(data) -> None:
            self._worker = None
            state = data.get("state") if isinstance(data, dict) else None
            if isinstance(state, dict):
                self._state = state
            self.refresh()
            if action == "enabled":
                actual = bool(self._state.get("enabled"))
                requested = bool(payload.get("value"))
                if requested and not actual:
                    self.ctx.notify("代理未启动，请先导入节点", "warning")
                else:
                    self.ctx.notify(
                        f"代理已{'启用' if actual else '停用'}",
                        "success" if actual else "info",
                    )
            elif action == "node":
                self.ctx.notify(f"已切换节点：{payload.get('value')}", "success")

        def failed(message: str) -> None:
            self._worker = None
            self.ctx.notify(f"插件操作失败：{message}", "error")
            self.refresh()

        worker.succeeded.connect(succeeded)
        worker.failed.connect(failed)
        worker.start()

    def _toggle_host(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        if record is None:
            return
        if self.ctx.plugin_processes.is_running(self._plugin_id):
            self.ctx.plugin_processes.stop(self._plugin_id)
            self.ctx.set_plugin_active(self._plugin_id, False)
            self.ctx.notify(f"插件「{record.name}」宿主已停止", "info")
        else:
            try:
                self.ctx.plugin_processes.start(record, hidden=True)
            except Exception as exc:  # noqa: BLE001
                self.ctx.notify(f"插件启动失败：{exc}", "error")
                return
            self.ctx.set_plugin_active(self._plugin_id, True)
            self.ctx.notify(f"插件「{record.name}」宿主已在后台启动", "success")
        self.refresh()

    def _open_config(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        if record is None:
            return
        try:
            if not self.ctx.plugin_processes.is_running(self._plugin_id):
                self.ctx.plugin_processes.start(record, hidden=True)
                self.ctx.set_plugin_active(self._plugin_id, True)
            self.ctx.plugin_processes.show(self._plugin_id)
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"插件配置窗口打开失败：{exc}", "error")
            return
        self.ctx.notify(f"插件「{record.name}」配置窗口已打开", "info")

    def _remove_from_workspace(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        name = record.name if record else self._plugin_id
        if not ask_confirm(
            self,
            "从工作空间移除",
            f"将从侧栏移除「{name}」的选项卡并停止宿主；插件包与插件数据会保留。",
        ):
            return
        self.ctx.navigate("home")
        self.ctx.unload_plugin_workspace(self._plugin_id)
        self.ctx.notify(f"已从工作空间移除「{name}」", "info")

    def _uninstall(self) -> None:
        record = self.ctx.plugins.get(self._plugin_id)
        name = record.name if record else self._plugin_id
        if not ask_confirm(
            self,
            "卸载插件",
            f"将停止「{name}」并删除插件包与插件数据，此操作不可撤销。",
        ):
            return
        self.ctx.navigate("home")
        self.ctx.uninstall_plugin(self._plugin_id)

    def _render_missing(self) -> None:
        self.title_label.setText("插件工作空间")
        self.subtitle_label.setText("插件已被移除")
        self.name_label.setText("插件不存在")
        self.meta_label.setText(f"ID：{self._plugin_id}")
        self.description_label.setText("插件包已被删除，可以从插件广场重新安装。")
        apply_status_pill(self.status_pill, False)
        clear_layout(self.controls_row)
        clear_layout(self.hints_layout)
        self.metrics_card.setVisible(False)
        self._actions_key = None
        clear_layout(self.actions_row)
        self.actions_row.addWidget(
            make_button(
                "前往插件广场",
                primary=True,
                icon=FIF.SHOPPING_CART,
                on_click=lambda: self.ctx.navigate("plugin_market"),
            )
        )
        self.actions_row.addStretch(1)

    # ------------------------------------------------------------------ 轮询
    @staticmethod
    def _signature(state: dict) -> str:
        try:
            return json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return ""

    def _poll_state(self) -> None:
        """仅对运行中的插件读取 state.json；内容没变就完全不动 UI。"""
        if not self.ctx.plugin_processes.is_running(self._plugin_id):
            return
        state = self.ctx.plugin_processes.state(self._plugin_id)
        if not isinstance(state, dict) or not state:
            return
        if self._signature(state) == self._state_signature:
            return
        self._state = state
        self.refresh()
