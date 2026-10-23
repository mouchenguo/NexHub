"""工具箱：已安装插件（子进程运行）与插件广场。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    SegmentedWidget,
    StrongBodyLabel,
)

from ..paths import user_plugins_dir
from ..services.plugin_manager import PluginRecord
from ..services.plugin_market import (
    MarketPlugin,
    fetch_market_plugins,
    uninstall_plugin,
)
from ..services.update_service import version_key
from ..widgets.common import (
    ask_confirm,
    clear_layout,
    make_button,
    make_card,
    status_pill,
)
from ..workers import FunctionWorker
from .base import BasePage
from .plugin_manager_dialog import PluginManagerDialog
from .plugin_market_dialog import PluginInstallDialog


class ToolboxPage(BasePage):
    page_id = "toolbox"
    title = "工具箱"
    subtitle = "插件广场、独立进程插件与本地扩展"

    def __init__(self, ctx, parent=None):
        self._market_items: list[MarketPlugin] = []
        self._market_worker: FunctionWorker | None = None
        self._market_loaded = False
        self._market_source = ""
        self._install_dialog: PluginInstallDialog | None = None
        super().__init__(ctx, parent)
        self.ctx.plugin_processes.changed.connect(self._render_installed)

    def build(self) -> None:
        top, top_layout = make_card(20, 8)
        row = QHBoxLayout()
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(StrongBodyLabel("插件工具箱"))
        text_box.addWidget(BodyLabel("插件在独立进程中运行；单个插件崩溃不会退出主程序。"))
        row.addLayout(text_box)
        row.addStretch(1)
        self.plugin_count = status_pill("0 个已启用", FIF.ACCEPT)
        row.addWidget(self.plugin_count)
        row.addWidget(
            make_button(
                "本地添加插件",
                primary=True,
                icon=FIF.ADD,
                on_click=self._import_local,
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

        self.tab_segment = SegmentedWidget()
        self.tab_segment.addItem("installed", "已安装")
        self.tab_segment.addItem("market", "插件广场")
        self.tab_segment.currentItemChanged.connect(self._switch_tab)
        self.content_layout.addWidget(self.tab_segment)

        self.tab_stack = QStackedWidget()
        self.installed_page = QWidget()
        self.installed_layout = QVBoxLayout(self.installed_page)
        self.installed_layout.setContentsMargins(0, 0, 0, 0)
        self.installed_layout.setSpacing(12)
        self.tab_stack.addWidget(self.installed_page)

        self.market_page = QWidget()
        market_layout = QVBoxLayout(self.market_page)
        market_layout.setContentsMargins(0, 0, 0, 0)
        market_layout.setSpacing(10)
        search_row = QHBoxLayout()
        self.search_edit = LineEdit()
        self.search_edit.setPlaceholderText("搜索插件名称、简介或 ID")
        self.search_edit.textChanged.connect(self._render_market)
        search_row.addWidget(self.search_edit, 2)
        self.category_combo = ComboBox()
        self.category_combo.addItem("全部分类")
        self.category_combo.currentTextChanged.connect(self._render_market)
        search_row.addWidget(self.category_combo, 1)
        search_row.addWidget(
            make_button(
                "刷新广场",
                icon=FIF.SYNC,
                on_click=self._load_market,
            )
        )
        market_layout.addLayout(search_row)
        self.market_status = CaptionLabel("打开插件广场后加载远程清单。")
        self.market_status.setWordWrap(True)
        market_layout.addWidget(self.market_status)
        self.market_host = QWidget()
        self.market_grid = QGridLayout(self.market_host)
        self.market_grid.setContentsMargins(0, 0, 0, 0)
        self.market_grid.setSpacing(12)
        market_layout.addWidget(self.market_host)
        market_layout.addStretch(1)
        self.tab_stack.addWidget(self.market_page)
        self.content_layout.addWidget(self.tab_stack)
        self.content_layout.addStretch(1)

        self._render_installed()
        self.tab_segment.setCurrentItem("installed")

    def _switch_tab(self, route_key: str) -> None:
        is_market = route_key == "market"
        self.tab_stack.setCurrentWidget(self.market_page if is_market else self.installed_page)
        if is_market and not self._market_loaded:
            self._load_market()

    def _avatar(self, glyph: str, icon_path: Path | None = None) -> AvatarWidget:
        avatar = AvatarWidget()
        avatar.setRadius(20)
        if icon_path and icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                avatar.setImage(pixmap)
                return avatar
        avatar.setText((glyph or "✦")[:2])
        return avatar

    def _render_installed(self) -> None:
        if not hasattr(self, "installed_layout"):
            return
        clear_layout(self.installed_layout)
        records = self.ctx.plugins.all()
        self.plugin_count.setText(f"{len(records)} 个已启用")
        if not records:
            card, layout = make_card(20, 12)
            layout.addWidget(StrongBodyLabel("还没有已安装插件"))
            hint = CaptionLabel(
                "可以从「插件广场」安装，或点击「本地导入插件」选择 zip 插件包。"
            )
            hint.setWordWrap(True)
            layout.addWidget(hint)
            self.installed_layout.addWidget(card)
        for record in records:
            self.installed_layout.addWidget(self._installed_card(record))
        if self.ctx.plugins.errors:
            error, layout = make_card(16, 8)
            layout.addWidget(StrongBodyLabel("部分插件加载失败"))
            label = CaptionLabel("\n".join(self.ctx.plugins.errors[:5]))
            label.setWordWrap(True)
            layout.addWidget(label)
            self.installed_layout.addWidget(error)
        self.installed_layout.addStretch(1)

    def _installed_card(self, record: PluginRecord) -> QWidget:
        card, layout = make_card(18, 10)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._avatar(record.glyph, record.icon_path))
        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel(record.name))
        title_row.addWidget(CaptionLabel(f"v{record.version}"))
        title_row.addStretch(1)
        running = self.ctx.plugin_processes.is_running(record.plugin_id)
        title_row.addWidget(
            status_pill("运行中" if running else "未运行", FIF.PLAY if running else FIF.PAUSE)
        )
        info.addLayout(title_row)
        description = CaptionLabel(record.description or "暂无简介")
        description.setWordWrap(True)
        info.addWidget(description)
        info.addWidget(CaptionLabel(f"ID：{record.plugin_id}"))
        top.addLayout(info, 1)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        open_button = make_button(
            "打开", primary=True, icon=FIF.PLAY, on_click=lambda: self._open_plugin(record)
        )
        open_button.setEnabled(not running)
        actions.addWidget(open_button)
        stop_button = make_button(
            "停止", transparent=True, icon=FIF.PAUSE, on_click=lambda: self._stop_plugin(record)
        )
        stop_button.setEnabled(running)
        actions.addWidget(stop_button)
        actions.addWidget(
            make_button(
                "卸载",
                transparent=True,
                icon=FIF.DELETE,
                on_click=lambda: self._uninstall_plugin(record),
            )
        )
        top.addLayout(actions)
        layout.addLayout(top)
        return card

    def _open_plugin(self, record: PluginRecord) -> None:
        try:
            self.ctx.plugin_processes.start(record)
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"插件启动失败：{exc}", "error")
            return
        self.ctx.notify(f"插件「{record.name}」已在独立进程启动", "success")

    def _stop_plugin(self, record: PluginRecord) -> None:
        self.ctx.plugin_processes.stop(record.plugin_id)
        self.ctx.notify(f"插件「{record.name}」已停止", "info")

    def _uninstall_plugin(self, record: PluginRecord) -> None:
        if not ask_confirm(self, "卸载插件", f"确定删除插件「{record.name}」及其数据目录吗？"):
            return
        self.ctx.plugin_processes.stop(record.plugin_id)
        try:
            uninstall_plugin(record.plugin_id)
            if record.plugin_id in self.ctx.config.disabled_plugins:
                self.ctx.config.disabled_plugins.remove(record.plugin_id)
                self.ctx.config.save()
            self.ctx.plugins.reload_custom()
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"卸载失败：{exc}", "error")
            return
        self._render_installed()
        self._render_market()
        self.ctx.notify(f"插件「{record.name}」已卸载", "success")


    def _load_market(self) -> None:
        if self._market_worker and self._market_worker.isRunning():
            return
        url = self.ctx.config.plugin_market_url
        self._market_source = url
        self.market_status.setText(f"正在读取插件广场：{url}")
        self._market_worker = FunctionWorker(fetch_market_plugins, url, parent=self)
        self._market_worker.succeeded.connect(self._on_market_loaded)
        self._market_worker.failed.connect(self._on_market_failed)
        self._market_worker.start()

    def _on_market_loaded(self, items: list[MarketPlugin]) -> None:
        self._market_items = list(items)
        self._market_loaded = True
        categories = sorted({item.category for item in self._market_items if item.category})
        current = self.category_combo.currentText()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类")
        for category in categories:
            self.category_combo.addItem(category)
        index = self.category_combo.findText(current)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.category_combo.blockSignals(False)
        self.market_status.setText(f"已加载 {len(self._market_items)} 个插件。")
        self._render_market()

    def _on_market_failed(self, message: str) -> None:
        self._market_loaded = False
        self.market_status.setText(f"插件广场加载失败：{message}")
        self.ctx.notify(f"插件广场加载失败：{message}", "error")

    def _render_market(self, *_args) -> None:
        if not hasattr(self, "market_grid"):
            return
        clear_layout(self.market_grid)
        keyword = self.search_edit.text().strip().lower()
        category = self.category_combo.currentText()
        items = [
            item
            for item in self._market_items
            if (category in ("", "全部分类") or item.category == category)
            and (
                not keyword
                or keyword in item.name.lower()
                or keyword in item.description.lower()
                or keyword in item.plugin_id.lower()
            )
        ]
        if not items:
            label = CaptionLabel("没有匹配的插件。" if self._market_items else "暂无远程插件。")
            label.setWordWrap(True)
            self.market_grid.addWidget(label, 0, 0, 1, 2)
            return
        for index, item in enumerate(items):
            self.market_grid.addWidget(self._market_card(item), index // 2, index % 2)
        for column in range(2):
            self.market_grid.setColumnStretch(column, 1)

    def _market_card(self, item: MarketPlugin) -> QWidget:
        card, layout = make_card(18, 10)
        card.setMinimumHeight(160)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(self._avatar(item.glyph))
        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel(item.name))
        title_row.addWidget(CaptionLabel(f"v{item.version}"))
        title_row.addStretch(1)
        info.addLayout(title_row)
        info.addWidget(CaptionLabel(item.category))
        description = CaptionLabel(item.description or "暂无简介")
        description.setWordWrap(True)
        info.addWidget(description)
        self_installed = self.ctx.plugins.get(item.plugin_id)
        if self_installed:
            info.addWidget(CaptionLabel(f"本地版本 v{self_installed.version}"))
        top.addLayout(info, 1)
        layout.addLayout(top)
        actions = QHBoxLayout()
        actions.addStretch(1)
        if self_installed and version_key(self_installed.version) >= version_key(item.version):
            button = make_button("已是最新", transparent=True)
            button.setEnabled(False)
        else:
            label = "更新" if self_installed else "安装"
            button = make_button(
                label,
                primary=True,
                icon=FIF.DOWNLOAD,
                on_click=lambda: self._install_market(item),
            )
        actions.addWidget(button)
        layout.addLayout(actions)
        return card

    def _install_market(self, item: MarketPlugin) -> None:
        if self._install_dialog is not None:
            return
        self.ctx.plugin_processes.stop(item.plugin_id)
        dialog = PluginInstallDialog(self.ctx, item=item, parent=self)
        self._install_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._install_dialog = None
        if dialog.record is None:
            return
        self.ctx.plugins.reload_custom()
        self._render_installed()
        self._render_market()
        self.ctx.notify(f"插件「{dialog.record.name}」已安装", "success")


    def _import_local(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入本地插件包",
            "",
            "NexHub 插件包 (*.zip)",
        )
        if not path:
            return
        self.ctx.plugin_processes.stop_all()
        dialog = PluginInstallDialog(self.ctx, zip_path=Path(path), parent=self)
        self._install_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._install_dialog = None
        if dialog.record is None:
            return
        self.ctx.plugins.reload_custom()
        self._render_installed()
        self._render_market()
        self.ctx.notify(f"插件「{dialog.record.name}」已安装", "success")

    def _open_plugin_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_plugins_dir())))

    def _manage_plugins(self) -> None:
        dialog = PluginManagerDialog(self.ctx, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._render_installed()
        self.ctx.notify("插件加载设置已保存", "success")

    def on_show(self) -> None:
        super().on_show()
        self.ctx.plugins.reload_custom()
        self._render_installed()
        if self.ctx.config.plugin_market_url != self._market_source:
            self._market_loaded = False
        if self.tab_segment.currentRouteKey() == "market" and not self._market_loaded:
            self._load_market()
