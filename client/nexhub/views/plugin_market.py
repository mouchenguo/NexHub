"""插件广场：浏览远程清单、安装/更新插件、导入本地 ZIP。

安装与加载分离：安装只写插件包；是否加入工作空间由用户决定。
"""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    MessageBox,
    StrongBodyLabel,
)

from ..paths import user_plugins_dir
from ..services.plugin_market import MarketPlugin, fetch_market_plugins
from ..services.update_service import version_key
from ..widgets.common import clear_layout, make_button, make_card, status_pill
from ..widgets.plugin_controls import plugin_avatar
from ..workers import FunctionWorker
from .base import BasePage
from .plugin_market_dialog import PluginInstallDialog


def peek_zip_plugin_id(zip_path: Path) -> str:
    """安装前先从 zip 里读出插件 ID，用于停止对应宿主。"""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [name for name in archive.namelist() if name.endswith("manifest.json")]
            names.sort(key=len)
            for name in names[:1]:
                payload = json.loads(archive.read(name).decode("utf-8"))
                return str(payload.get("id") or "")
    except Exception:  # noqa: BLE001 - 读不到就当作未知插件
        return ""
    return ""


class PluginMarketPage(BasePage):
    page_id = "plugin_market"
    title = "插件广场"
    subtitle = "安装、更新插件；安装后由你决定是否加入工作空间"

    def __init__(self, ctx, parent=None):
        self._items: list[MarketPlugin] = []
        self._worker: FunctionWorker | None = None
        self._loaded = False
        self._source = ""
        self._install_dialog: PluginInstallDialog | None = None
        super().__init__(ctx, parent)
        self.ctx.plugin_processes.changed.connect(self._render_grid)
        self.ctx.workspace_changed.connect(self._render_grid)

    # ------------------------------------------------------------------ 构建
    def build(self) -> None:
        top, top_layout = make_card(20, 8)
        row = QHBoxLayout()
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(StrongBodyLabel("插件广场"))
        text_box.addWidget(
            BodyLabel("插件在独立进程中运行；安装后不会自动出现在侧栏。")
        )
        row.addLayout(text_box)
        row.addStretch(1)
        row.addWidget(
            make_button(
                "导入本地 ZIP",
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
        top_layout.addLayout(row)
        self.content_layout.addWidget(top)

        search_row = QHBoxLayout()
        self.search_edit = LineEdit()
        self.search_edit.setPlaceholderText("搜索插件名称、简介或 ID")
        self.search_edit.textChanged.connect(self._render_grid)
        search_row.addWidget(self.search_edit, 2)
        self.category_combo = ComboBox()
        self.category_combo.addItem("全部分类")
        self.category_combo.currentTextChanged.connect(self._render_grid)
        search_row.addWidget(self.category_combo, 1)
        search_row.addWidget(
            make_button("刷新广场", icon=FIF.SYNC, on_click=self._load_market)
        )
        self.content_layout.addLayout(search_row)

        self.status_label = CaptionLabel("打开本页后加载远程清单。")
        self.status_label.setWordWrap(True)
        self.content_layout.addWidget(self.status_label)

        self.host = QWidget()
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.content_layout.addWidget(self.host)
        self.content_layout.addStretch(1)

    # ------------------------------------------------------------------ 清单
    def on_show(self) -> None:
        super().on_show()
        if self.ctx.config.plugin_market_url != self._source:
            self._loaded = False
        if not self._loaded:
            self._load_market()

    def _load_market(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        url = self.ctx.config.plugin_market_url
        self._source = url
        self.status_label.setText(f"正在读取插件广场：{url}")
        self._worker = FunctionWorker(fetch_market_plugins, url, parent=self)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_loaded(self, items: list[MarketPlugin]) -> None:
        self._items = list(items)
        self._loaded = True
        categories = sorted({item.category for item in self._items if item.category})
        current = self.category_combo.currentText()
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("全部分类")
        for category in categories:
            self.category_combo.addItem(category)
        index = self.category_combo.findText(current)
        self.category_combo.setCurrentIndex(index if index >= 0 else 0)
        self.category_combo.blockSignals(False)
        self.status_label.setText(f"已加载 {len(self._items)} 个插件。")
        self._render_grid()

    def _on_failed(self, message: str) -> None:
        self._loaded = False
        self.status_label.setText(f"插件广场加载失败：{message}")
        self.ctx.notify(f"插件广场加载失败：{message}", "error")

    def _render_grid(self, *_args) -> None:
        if not hasattr(self, "grid"):
            return
        clear_layout(self.grid)
        keyword = self.search_edit.text().strip().lower()
        category = self.category_combo.currentText()
        items = [
            item
            for item in self._items
            if (category in ("", "全部分类") or item.category == category)
            and (
                not keyword
                or keyword in item.name.lower()
                or keyword in item.description.lower()
                or keyword in item.plugin_id.lower()
            )
        ]
        if not items:
            empty = CaptionLabel("没有匹配的插件。" if self._items else "暂无远程插件。")
            empty.setWordWrap(True)
            self.grid.addWidget(empty, 0, 0, 1, 2)
            return
        for index, item in enumerate(items):
            self.grid.addWidget(self._card(item), index // 2, index % 2)
        for column in range(2):
            self.grid.setColumnStretch(column, 1)

    def _card(self, item: MarketPlugin) -> QWidget:
        card, layout = make_card(18, 10)
        card.setMinimumHeight(176)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(plugin_avatar(item.glyph))

        info = QVBoxLayout()
        info.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel(item.name))
        title_row.addWidget(CaptionLabel(f"v{item.version}"))
        title_row.addStretch(1)
        installed = self.ctx.plugins.get(item.plugin_id)
        loaded = self.ctx.workspace.is_plugin_loaded(item.plugin_id)
        running = self.ctx.plugin_processes.is_running(item.plugin_id)
        if not installed:
            title_row.addWidget(status_pill("未安装", FIF.INFO))
        elif loaded:
            title_row.addWidget(
                status_pill("已加载 · 运行中" if running else "已加载 · 已停止", FIF.ACCEPT)
            )
        else:
            title_row.addWidget(status_pill("已安装 · 未加载", FIF.DOWNLOAD))
        info.addLayout(title_row)
        info.addWidget(CaptionLabel(item.category))
        description = CaptionLabel(item.description or "暂无简介")
        description.setWordWrap(True)
        info.addWidget(description)
        if installed:
            info.addWidget(CaptionLabel(f"本地版本 v{installed.version}"))
        top.addLayout(info, 1)
        layout.addLayout(top)

        actions = QHBoxLayout()
        actions.addStretch(1)
        if installed and version_key(installed.version) >= version_key(item.version):
            latest = make_button("已是最新", transparent=True)
            latest.setEnabled(False)
            actions.addWidget(latest)
        else:
            actions.addWidget(
                make_button(
                    "更新" if installed else "安装",
                    primary=True,
                    icon=FIF.DOWNLOAD,
                    on_click=lambda _checked=False, current=item: self._install(current),
                )
            )
        if installed and not loaded:
            actions.addWidget(
                make_button(
                    "加载到工作空间",
                    icon=FIF.ADD,
                    on_click=lambda _checked=False, pid=item.plugin_id: self._load_plugin(pid),
                )
            )
        elif loaded:
            actions.addWidget(
                make_button(
                    "打开工作空间",
                    transparent=True,
                    icon=FIF.LINK,
                    on_click=lambda _checked=False, pid=item.plugin_id: (
                        self.ctx.navigate(f"plugin:{pid}")
                    ),
                )
            )
        layout.addLayout(actions)
        return card

    # ------------------------------------------------------------------ 安装/更新
    def _install(self, item: MarketPlugin) -> None:
        if self._install_dialog is not None:
            return
        plugin_id = item.plugin_id
        updating = self.ctx.plugins.get(plugin_id) is not None
        was_running = self.ctx.plugin_processes.is_running(plugin_id)
        if was_running:
            # install_zip 会重建插件目录，必须先停掉宿主
            self.ctx.plugin_processes.stop(plugin_id)
        dialog = PluginInstallDialog(self.ctx, item=item, parent=self)
        self._install_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._install_dialog = None
        record = dialog.record
        if record is None:
            return
        self.ctx.plugins.reload_custom()
        self.ctx.save_workspace()
        self._render_grid()
        if updating:
            # 更新：保留原选项卡；原来在运行就自动重启，不再询问加入工作空间
            if was_running:
                self._restart(record.plugin_id)
            self.ctx.notify(
                f"插件「{record.name}」已更新到 v{record.version}", "success"
            )
            return
        self.ctx.notify(f"插件「{record.name}」已安装", "success")
        if self._ask_add_to_workspace(record):
            self._load_plugin(record.plugin_id)

    def _import_local(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入本地插件包",
            "",
            "NexHub 插件包 (*.zip)",
        )
        if not path:
            return
        zip_path = Path(path)
        plugin_id = peek_zip_plugin_id(zip_path)
        updating = bool(plugin_id) and self.ctx.plugins.get(plugin_id) is not None
        was_running = bool(plugin_id) and self.ctx.plugin_processes.is_running(plugin_id)
        if was_running:
            self.ctx.plugin_processes.stop(plugin_id)
        dialog = PluginInstallDialog(self.ctx, zip_path=zip_path, parent=self)
        self._install_dialog = dialog
        try:
            dialog.exec()
        finally:
            self._install_dialog = None
        record = dialog.record
        if record is None:
            return
        self.ctx.plugins.reload_custom()
        self.ctx.save_workspace()
        self._render_grid()
        if updating:
            if was_running:
                self._restart(record.plugin_id)
            self.ctx.notify(
                f"插件「{record.name}」已更新到 v{record.version}", "success"
            )
            return
        self.ctx.notify(f"插件「{record.name}」已安装", "success")
        if self._ask_add_to_workspace(record):
            self._load_plugin(record.plugin_id)

    def _load_plugin(self, plugin_id: str) -> None:
        try:
            self.ctx.load_plugin_workspace(plugin_id, start=True)
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"加载插件失败：{exc}", "error")
            return
        self._render_grid()
        self.ctx.navigate(f"plugin:{plugin_id}")

    def _restart(self, plugin_id: str) -> None:
        record = self.ctx.plugins.get(plugin_id)
        if record is None:
            return
        try:
            self.ctx.plugin_processes.start(record, hidden=True)
            self.ctx.set_plugin_active(plugin_id, True)
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"插件重启失败：{exc}", "warning")

    def _ask_add_to_workspace(self, record) -> bool:
        box = MessageBox(
            "安装完成",
            f"插件「{record.name}」安装完成。\n是否添加到工作空间？",
            self,
        )
        box.yesButton.setText("添加到工作空间")
        box.cancelButton.setText("稍后")
        return bool(box.exec())

    def _open_plugin_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(user_plugins_dir())))
