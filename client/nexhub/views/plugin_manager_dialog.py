"""插件管理弹窗：选择哪些插件加载到工具箱。"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentStyleSheet,
    TitleLabel,
)

from ..widgets.common import make_button


class PluginManagerDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._checks: dict[str, CheckBox] = {}
        self.setWindowTitle("NexHub · 管理插件")
        self.setMinimumSize(560, 460)
        FluentStyleSheet.DIALOG.apply(self)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(TitleLabel("管理插件"))
        hint = CaptionLabel(
            "取消勾选后，插件会从工具箱「已安装」列表隐藏；插件包仍保留在插件目录。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        for plugin in self.ctx.plugins.all_plugins():
            self._add_plugin_row(plugin)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("取消", on_click=self.reject))
        buttons.addWidget(
            make_button("保存加载设置", primary=True, on_click=self._save)
        )
        layout.addLayout(buttons)

    def _add_plugin_row(self, plugin) -> None:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(10)
        check = CheckBox(plugin.name)
        check.setChecked(plugin.plugin_id not in self.ctx.plugins.disabled_ids)
        self._checks[plugin.plugin_id] = check
        row_layout.addWidget(check, 1)
        source = "外部插件" if self.ctx.plugins.is_custom(plugin.plugin_id) else "内置"
        info = CaptionLabel(f"{plugin.plugin_id} · {source} · v{plugin.version}")
        row_layout.addWidget(info)
        row.setToolTip(plugin.description)
        self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def _save(self) -> None:
        enabled_ids = {
            plugin_id
            for plugin_id, check in self._checks.items()
            if check.isChecked()
        }
        self.ctx.plugins.set_enabled_ids(enabled_ids)
        all_ids = {plugin.plugin_id for plugin in self.ctx.plugins.all_plugins()}
        for plugin_id in all_ids - enabled_ids:
            self.ctx.plugin_processes.stop(plugin_id)
        self.ctx.config.disabled_plugins = sorted(all_ids - enabled_ids)
        self.ctx.config.active_plugins = [
            plugin_id
            for plugin_id in self.ctx.config.active_plugins
            if plugin_id in enabled_ids
        ]
        self.ctx.config.save()
        self.accept()
