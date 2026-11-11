"""主页：工作空间唯一固定入口。

不放业务数据，只做导航与状态汇总：
- 内置功能：已显示 / 已隐藏，点击自动恢复选项卡并跳转；
- 插件：已加载 / 未加载，点击自动加载并打开工作空间；
- 管理员额外显示仪表盘入口与网页后台入口。
"""
from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon as FIF, StrongBodyLabel

from ..paths import VERSION
from ..workspace import PAGE_SPECS
from ..widgets.common import clear_layout, make_button, make_card, status_pill
from .base import BasePage


class HomePage(BasePage):
    page_id = "home"
    title = "主页"
    subtitle = "工作空间导航"

    def __init__(self, ctx, parent=None):
        super().__init__(ctx, parent)
        self.ctx.workspace_changed.connect(self.refresh)
        self.ctx.plugin_processes.changed.connect(self.refresh)
        self.ctx.connection_changed.connect(lambda _online: self.refresh())

    # ------------------------------------------------------------------ 构建
    def build(self) -> None:
        self.overview_card, self.overview_layout = make_card(20, 8)
        self.content_layout.addWidget(self.overview_card)

        self.nav_card, self.nav_layout = make_card(18, 8)
        self.nav_layout.addWidget(StrongBodyLabel("功能导航"))
        hint = CaptionLabel(
            "点击任意条目即可打开；已隐藏的页面会自动恢复选项卡，"
            "未加载的插件会自动加入工作空间。"
        )
        hint.setWordWrap(True)
        self.nav_layout.addWidget(hint)
        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(8)
        self.nav_layout.addLayout(self.rows_layout)
        self.content_layout.addWidget(self.nav_card)

        self.shortcuts_card, self.shortcuts_layout = make_card(18, 8)
        self.shortcuts_layout.addWidget(StrongBodyLabel("快捷入口"))
        self.shortcut_row = QHBoxLayout()
        self.shortcut_row.setSpacing(8)
        self.shortcuts_layout.addLayout(self.shortcut_row)
        self.content_layout.addWidget(self.shortcuts_card)
        self.content_layout.addStretch(1)

        self._render_overview()
        self._render_rows()
        self._render_shortcuts()

    # ------------------------------------------------------------------ 渲染
    def refresh(self) -> None:
        self._render_overview()
        self._render_rows()
        self._render_shortcuts()

    def _render_overview(self) -> None:
        clear_layout(self.overview_layout)
        display = self.ctx.config.display_name or self.ctx.config.username or "本地用户"
        role = "管理员" if self.ctx.is_admin else "普通用户"
        if not self.ctx.online:
            role = "未登录 · 离线体验"
        self.overview_layout.addWidget(StrongBodyLabel(f"你好，{display}"))
        self.overview_layout.addWidget(
            CaptionLabel(f"{role} · NexHub v{VERSION} · {self.ctx.config.server_url}")
        )

        pages = self.ctx.workspace.page_ids()
        loaded = self.ctx.workspace.loaded_plugin_ids()
        installed = self.ctx.plugins.installed_ids()
        summary = QHBoxLayout()
        summary.setSpacing(8)
        summary.addWidget(status_pill(f"侧栏 {len(pages)} 项", FIF.MENU))
        summary.addWidget(
            status_pill(f"插件已加载 {len(loaded)} 个", FIF.DEVELOPER_TOOLS)
        )
        summary.addWidget(status_pill(f"已安装 {len(installed)} 个", FIF.DOWNLOAD))
        if self.ctx.online:
            summary.addWidget(status_pill("ECS 已连接", FIF.CONNECT))
        else:
            summary.addWidget(status_pill("离线体验", FIF.INFO))
        summary.addStretch(1)
        self.overview_layout.addLayout(summary)

    def _render_rows(self) -> None:
        clear_layout(self.rows_layout)
        for spec in PAGE_SPECS:
            if spec.page_id == "home":
                continue
            if spec.admin_only and not self.ctx.is_admin:
                continue
            hidden = self.ctx.workspace.is_hidden(spec.page_id)
            self.rows_layout.addWidget(
                self._nav_row(
                    title=spec.title,
                    subtitle=spec.subtitle,
                    badge="已隐藏" if hidden else "已显示",
                    icon=getattr(FIF, spec.icon, FIF.APPLICATION),
                    on_click=lambda _checked=False, pid=spec.page_id: self._open_page(pid),
                )
            )

        for record in self.ctx.plugins.all_plugins():
            if self.ctx.workspace.is_plugin_loaded(record.plugin_id):
                running = self.ctx.plugin_processes.is_running(record.plugin_id)
                badge = "运行中" if running else "已加载"
            else:
                badge = "插件未加载"
            self.rows_layout.addWidget(
                self._nav_row(
                    title=f"插件：{record.name}",
                    subtitle=f"v{record.version} · {record.description or '暂无简介'}",
                    badge=badge,
                    icon=FIF.DEVELOPER_TOOLS,
                    on_click=lambda _checked=False, pid=record.plugin_id: (
                        self._open_plugin(pid)
                    ),
                )
            )

        self.rows_layout.addWidget(
            self._nav_row(
                title="更多插件",
                subtitle="浏览插件广场，安装更多扩展",
                badge="前往广场",
                icon=FIF.SHOPPING_CART,
                on_click=lambda _checked=False: self._open_page("plugin_market"),
            )
        )

    def _nav_row(
        self,
        *,
        title: str,
        subtitle: str,
        badge: str,
        icon,
        on_click,
    ) -> QWidget:
        card, layout = make_card(14, 4)
        row = QHBoxLayout()
        row.setSpacing(10)
        text_box = QVBoxLayout()
        text_box.setSpacing(1)
        text_box.addWidget(StrongBodyLabel(title))
        caption = CaptionLabel(subtitle)
        caption.setWordWrap(True)
        text_box.addWidget(caption)
        row.addLayout(text_box, 1)
        row.addWidget(status_pill(badge, icon))
        row.addWidget(make_button("打开", icon=icon, on_click=on_click))
        layout.addLayout(row)
        return card

    def _render_shortcuts(self) -> None:
        clear_layout(self.shortcut_row)
        self.shortcut_row.addWidget(
            make_button(
                "添加选项卡",
                primary=True,
                icon=FIF.ADD,
                on_click=self.ctx.open_workspace_manager,
            )
        )
        self.shortcut_row.addWidget(
            make_button(
                "打开插件广场",
                icon=FIF.SHOPPING_CART,
                on_click=lambda: self._open_page("plugin_market"),
            )
        )
        if self.ctx.is_admin:
            self.shortcut_row.addWidget(
                make_button(
                    "打开网页后台",
                    transparent=True,
                    icon=FIF.LINK,
                    on_click=self._open_web_admin,
                )
            )
        self.shortcut_row.addStretch(1)

    # ------------------------------------------------------------------ 动作
    def _open_page(self, page_id: str) -> None:
        if page_id != "home" and self.ctx.workspace.is_hidden(page_id):
            self.ctx.set_workspace_page_hidden(page_id, False)
        self.ctx.navigate(page_id)

    def _open_plugin(self, plugin_id: str) -> None:
        if not self.ctx.workspace.is_plugin_loaded(plugin_id):
            try:
                self.ctx.load_plugin_workspace(plugin_id, start=True)
            except Exception as exc:  # noqa: BLE001
                self.ctx.notify(f"插件加载失败：{exc}", "error")
                return
        self.ctx.navigate(f"plugin:{plugin_id}")

    def _open_web_admin(self) -> None:
        base = (self.ctx.config.server_url or "").rstrip("/")
        if not base:
            self.ctx.notify("请先在设置页填写服务器地址", "warning")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/admin"))
