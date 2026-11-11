"""插件快捷控件组件（从原工具箱抽取）。

只负责“根据 manifest.controls + 宿主 state 生成控件”，不持有业务状态：
所有交互都通过 ``on_action(action, payload)`` 回调交给页面处理。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    AvatarWidget,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    ProgressBar,
    SwitchButton,
    TitleLabel,
)

from ..file_utils import human_size
from ..services.plugin_manager import PluginRecord
from .common import latency_dot, latency_text, make_button

ActionHandler = Callable[[str, dict], None]


def plugin_avatar(glyph: str, icon_path: Path | None = None) -> AvatarWidget:
    avatar = AvatarWidget()
    avatar.setRadius(20)
    if icon_path and icon_path.is_file():
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            avatar.setImage(pixmap)
            return avatar
    avatar.setText((glyph or "✦")[:2])
    return avatar


def apply_status_pill(label: CaptionLabel, running: bool) -> CaptionLabel:
    """就地更新状态胶囊（避免每次刷新重建控件）。"""
    label.setText("● 运行中" if running else "● 关闭")
    if running:
        label.setStyleSheet(
            "CaptionLabel { color: #3fb950; background-color: rgba(63, 185, 80, .10); "
            "border: 1px solid rgba(63, 185, 80, .25); border-radius: 12px; "
            "padding: 1px 8px; }"
        )
    else:
        label.setStyleSheet(
            "CaptionLabel { color: #f85149; background-color: rgba(248, 81, 73, .10); "
            "border: 1px solid rgba(248, 81, 73, .25); border-radius: 12px; "
            "padding: 1px 8px; }"
        )
    return label


def plugin_status_pill(running: bool) -> CaptionLabel:
    label = CaptionLabel("")
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setMinimumWidth(72)
    label.setFixedHeight(24)
    return apply_status_pill(label, running)


def subscription_text(subscription: dict) -> str:
    total = int(subscription.get("total") or 0)
    if total <= 0:
        return ""
    upload = int(subscription.get("upload") or 0)
    download = int(subscription.get("download") or 0)
    remaining = int(subscription.get("remaining") or max(total - upload - download, 0))
    text = f"订阅剩余流量：{human_size(remaining)} / {human_size(total)}"
    days = subscription.get("days")
    if days is not None:
        text += f" · 剩余 {int(days)} 天"
    return text


def _select_options(control: dict, state: dict) -> tuple[list[str], list[str]]:
    """select 控件选项：静态 list，或 state 中某个字段名（如节点列表）。"""
    raw_options = control.get("options")
    if isinstance(raw_options, list):
        values: list[str] = []
        labels: list[str] = []
        for item in raw_options:
            if isinstance(item, dict):
                value = str(item.get("value") or item.get("label") or "")
                values.append(value)
                labels.append(str(item.get("label") or value))
            else:
                values.append(str(item))
                labels.append(str(item))
        return values, labels

    values = []
    labels = []
    raw_items = state.get(str(raw_options or "")) or []
    if not isinstance(raw_items, list):
        return values, labels
    for item in raw_items:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        name = str(item.get("name"))
        text, _color = latency_text(item.get("latency"))
        values.append(name)
        labels.append(f"{latency_dot(item.get('latency'))} {text}    {name}")
    return values, labels


def build_control_widgets(
    record: PluginRecord,
    state: dict,
    running: bool,
    on_action: ActionHandler,
) -> list[QWidget]:
    """按 manifest.controls 生成控件列表；无 controls 时退化为“启用”开关。"""
    widgets: list[QWidget] = []
    controls = record.controls
    if not controls:
        widgets.append(CaptionLabel("启用"))
        switch = SwitchButton()
        switch.setChecked(running)
        switch.checkedChanged.connect(
            lambda checked: on_action("enabled", {"value": bool(checked)})
        )
        widgets.append(switch)
        return widgets

    for control in controls:
        kind = str(control.get("type") or "button")
        control_id = str(control.get("id") or "")
        label = str(control.get("label") or control_id)
        if kind == "button":
            if control_id == "choose_node" and state.get("node"):
                widgets.append(CaptionLabel(f"节点：{state['node']}"))
            widgets.append(
                make_button(
                    label,
                    primary=bool(control.get("primary", True)),
                    icon=FIF.PLAY,
                    on_click=lambda _checked=False, action=control_id: on_action(action, {}),
                )
            )
        elif kind == "switch":
            widgets.append(CaptionLabel(label))
            toggle = SwitchButton()
            toggle.setChecked(bool(state.get(control_id, False)))
            toggle.checkedChanged.connect(
                lambda checked, action=control_id: on_action(
                    action, {"value": bool(checked)}
                )
            )
            widgets.append(toggle)
        elif kind == "select":
            widgets.append(CaptionLabel(label))
            values, labels = _select_options(control, state)
            selector = ComboBox()
            if not labels:
                selector.addItem("启用后加载")
                selector.setEnabled(False)
            else:
                selector.addItems(labels)
                current_value = str(state.get(control_id) or "")
                if current_value in values:
                    selector.setCurrentIndex(values.index(current_value))
                selector.currentIndexChanged.connect(
                    lambda index, action=control_id, options=values: (
                        on_action(action, {"value": options[index]})
                        if 0 <= index < len(options)
                        else None
                    )
                )
            selector.setMinimumWidth(240 if control_id == "node" else 110)
            widgets.append(selector)
    return widgets


def format_metric(value, kind: str) -> str:
    if kind == "percent":
        try:
            return f"{int(float(value or 0))}%"
        except (TypeError, ValueError):
            return "--"
    if kind == "bytes":
        try:
            return human_size(int(value or 0))
        except (TypeError, ValueError):
            return "--"
    if kind == "count":
        try:
            return str(int(value or 0))
        except (TypeError, ValueError):
            return "--"
    return "--" if value is None else str(value)


def percent_tone(percent: int) -> str:
    if percent >= 85:
        return "#f85149"
    if percent >= 60:
        return "#d29922"
    return "#3fb950"


def _percent_advice(percent: int) -> str:
    if percent >= 85:
        return "占用偏高，建议立即优化"
    if percent >= 60:
        return "占用略高，可以优化一次"
    return "运行正常"


class MetricPanel(QWidget):
    """按 manifest.metrics 渲染指标。

    控件只创建一次，之后 ``update_state`` 只改文本/进度值，
    避免每秒重建导致的闪烁。
    """

    def __init__(self, metrics: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self._metrics = list(metrics)
        self._value_labels: dict[str, TitleLabel] = {}
        self._hint_labels: dict[str, tuple[CaptionLabel, str, str, str]] = {}
        self._bar: ProgressBar | None = None
        self._advice: CaptionLabel | None = None
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(28)
        show_bar = False
        for metric in self._metrics:
            key = str(metric.get("key") or "")
            kind = str(metric.get("type") or "text")
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(CaptionLabel(str(metric.get("label") or key)))

            value_label = TitleLabel("--")
            self._value_labels[key] = value_label
            box.addWidget(value_label)

            hint_key = str(metric.get("hint_key") or "")
            if hint_key:
                hint = CaptionLabel("")
                self._hint_labels[key] = (
                    hint,
                    hint_key,
                    str(metric.get("hint_type") or kind),
                    str(metric.get("hint_prefix") or ""),
                )
                box.addWidget(hint)

            row.addLayout(box, 1)
            if kind == "percent" and metric.get("bar"):
                show_bar = True
        row.addStretch(1)
        layout.addLayout(row)

        if show_bar:
            self._bar = ProgressBar()
            self._bar.setRange(0, 100)
            self._bar.setValue(0)
            layout.addWidget(self._bar)
            self._advice = CaptionLabel("")
            layout.addWidget(self._advice)

    def update_state(self, state: dict) -> None:
        percent = 0
        for metric in self._metrics:
            key = str(metric.get("key") or "")
            kind = str(metric.get("type") or "text")
            value = state.get(key)

            value_label = self._value_labels.get(key)
            if value_label is not None:
                value_label.setText(format_metric(value, kind))
                if kind == "percent":
                    try:
                        percent = int(float(value or 0))
                    except (TypeError, ValueError):
                        percent = 0
                    value_label.setStyleSheet(f"color: {percent_tone(percent)};")

            hint = self._hint_labels.get(key)
            if hint is not None:
                label, hint_key, hint_kind, prefix = hint
                label.setText(
                    f"{prefix}{format_metric(state.get(hint_key), hint_kind)}"
                )

        if self._bar is not None:
            self._bar.setValue(max(0, min(100, percent)))
        if self._advice is not None:
            self._advice.setText(_percent_advice(percent))
            self._advice.setStyleSheet(f"color: {percent_tone(percent)};")


def build_metrics_panel(state: dict, metrics: list[dict]) -> QWidget:
    """便捷构造：创建面板并立即填充一次状态。"""
    panel = MetricPanel(metrics)
    panel.update_state(state)
    return panel


def state_hints(state: dict, running: bool) -> list[tuple[str, str]]:
    """把宿主 state 转成提示文本列表：(文本, 颜色)；颜色为空表示默认样式。"""
    hints: list[tuple[str, str]] = []
    subscription = state.get("subscription")
    if isinstance(subscription, dict):
        text = subscription_text(subscription)
        if text:
            hints.append((text, ""))
    if state.get("rules_count"):
        source = {"subscription": "订阅导入", "clash_verge": "Clash Verge"}.get(
            str(state.get("rules_source") or ""), "内置"
        )
        hints.append((f"规则：{state['rules_count']} 条（{source}）", ""))
    conflict = str(state.get("clash_conflict") or "")
    if conflict:
        hints.append((f"⚠ {conflict}", "#f85149"))
    if state.get("system_proxy_active"):
        hints.append(
            (f"系统代理：{state.get('system_proxy')}（浏览器已可走 NexHub）", "#3fb950")
        )
    elif running and state.get("enabled") and not state.get("system_proxy_active"):
        hints.append(
            ("系统代理未接管：请关闭 Clash Verge 的系统代理后重新打开开关。", "#f85149")
        )
    return hints
