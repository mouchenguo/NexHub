"""工具箱：Fluent 插件化功能入口。"""
from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    StrongBodyLabel,
)

from ..paths import user_plugins_dir

from ..widgets.common import (
    clear_layout,
    icon_widget,
    make_button,
    make_card,
    status_pill,
)
from .base import BasePage
from .plugin_dialog import AddPluginDialog
from .plugin_manager_dialog import PluginManagerDialog

PLANNED_PLUGINS = [
    (FIF.MAIL, "Outlook 集成", "收发邮件与附件共享"),
    (FIF.PHONE, "短信代收", "验证码与通知聚合"),
    (FIF.ADD, "更多插件", "插件接口已预留，持续扩展"),
]


class ToolboxPage(BasePage):
    page_id = "toolbox"
    title = "工具箱"
    subtitle = "插件化效率工具，按需扩展"

    def __init__(self, ctx, parent=None):
        self._plugin_widgets: dict[str, QWidget] = {}
        super().__init__(ctx, parent)

    def build(self) -> None:
        top, top_layout = make_card(20, 8)
        row = QHBoxLayout()
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(StrongBodyLabel("插件工具箱"))
        text_box.addWidget(BodyLabel("插件通过统一注册中心加载，新增功能无需改造主程序。"))
        row.addLayout(text_box)
        row.addStretch(1)
        self.plugin_count = status_pill("0 个已启用", FIF.ACCEPT)
        row.addWidget(self.plugin_count)
        row.addWidget(
            make_button(
                "添加插件",
                primary=True,
                icon=FIF.ADD,
                on_click=self._add_plugin,
            )
        )
        row.addWidget(
            make_button(
                "打开插件目录",
                transparent=True,
                icon=FIF.FOLDER,
                on_click=self._open_plugin_dir,
            )
        )
        row.addWidget(
            make_button(
                "管理插件",
                transparent=True,
                icon=FIF.SETTING,
                on_click=self._manage_plugins,
            )
        )
        top_layout.addLayout(row)
        self.content_layout.addWidget(top)

        self.plugin_host = QWidget()
        self.plugin_layout = QVBoxLayout(self.plugin_host)
        self.plugin_layout.setContentsMargins(0, 0, 0, 0)
        self.plugin_layout.setSpacing(14)
        self.content_layout.addWidget(self.plugin_host)
        self._render_plugins()

        planned_row = QHBoxLayout()
        planned_row.addWidget(StrongBodyLabel("即将上线"))
        planned_row.addWidget(CaptionLabel("扩展占位"))
        planned_row.addStretch(1)
        self.content_layout.addLayout(planned_row)

        grid = QGridLayout()
        grid.setSpacing(12)
        for index, (icon, name, desc) in enumerate(PLANNED_PLUGINS):
            card = CardWidget()
            card.setBorderRadius(12)
            card.setMinimumHeight(120)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(18, 16, 18, 16)
            layout.setSpacing(6)
            top_row = QHBoxLayout()
            top_row.addWidget(icon_widget(icon, 22))
            top_row.addWidget(StrongBodyLabel(name))
            top_row.addStretch(1)
            top_row.addWidget(status_pill("规划中", FIF.INFO))
            layout.addLayout(top_row)
            desc_label = CaptionLabel(desc)
            desc_label.setWordWrap(True)
            layout.addWidget(desc_label)
            layout.addStretch(1)
            grid.addWidget(card, 0, index)
            grid.setColumnStretch(index, 1)
        self.content_layout.addLayout(grid)
        self.content_layout.addStretch(1)

    def _render_plugins(self) -> None:
        clear_layout(self.plugin_layout)
        self._plugin_widgets.clear()
        plugins = self.ctx.plugins.all()
        self.plugin_count.setText(f"{len(plugins)} 个已启用")
        for plugin in plugins:
            try:
                widget = plugin.create_widget(self.ctx, self)
                if widget is None:
                    raise ValueError("create_widget() 返回了空对象")
            except Exception as exc:  # noqa: BLE001 - 单个插件不能拖垮工具箱
                error_card, error_layout = make_card(16, 6)
                error_layout.addWidget(
                    CaptionLabel(f"插件「{plugin.name}」加载失败：{exc}")
                )
                self.plugin_layout.addWidget(error_card)
                continue
            self._plugin_widgets[plugin.plugin_id] = widget
            self.plugin_layout.addWidget(widget)

    def _add_plugin(self) -> None:
        dialog = AddPluginDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        errors = self.ctx.plugins.reload_custom()
        self._render_plugins()
        if errors:
            self.ctx.notify(f"插件加载失败：{errors[0]}", "error")
        else:
            self.ctx.notify("自定义插件已加载", "success")

    def _open_plugin_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_plugins_dir())))

    def on_show(self) -> None:
        super().on_show()
        for widget in self._plugin_widgets.values():
            refresh = getattr(widget, "refresh_stats", None)
            if callable(refresh):
                refresh()

    def _manage_plugins(self) -> None:
        dialog = PluginManagerDialog(self.ctx, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._render_plugins()
        self.ctx.notify("插件加载设置已保存", "success")
