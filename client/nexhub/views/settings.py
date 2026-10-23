"""设置页：Windows 设置风格的可折叠子模块。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QIntValidator
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    SegmentedWidget,
    SpinBox,
)

from ..api_client import ApiClient
from ..compat import ServerCompatibility
from ..config import build_server_url, normalize_server_url, split_server_url
from ..paths import VERSION, app_data_dir, config_path
from ..services.update_service import UpdateInfo, check_for_updates
from ..widgets.common import (
    Banner,
    FolderDetail,
    FolderRow,
    form_field,
    make_button,
)
from ..workers import FunctionWorker
from .base import BasePage
from .npc import NpcSettingsPanel
from .nps_tunnels import NpsTunnelPanel


class SettingsPage(BasePage):
    page_id = "settings"
    title = "设置"
    subtitle = "按模块管理外观、连接、穿透与本地数据"

    def __init__(self, ctx, parent=None):
        self._worker: FunctionWorker | None = None
        self._update_worker: FunctionWorker | None = None
        self._latest_update: UpdateInfo | None = None
        self._syncing_theme = False
        self._folders: list[tuple[FIF, str, str, QWidget]] = []
        super().__init__(ctx, parent)
        self.ctx.theme.theme_changed.connect(self._sync_theme_segment)
        self.ctx.server_compat_changed.connect(self._on_compat_changed)

    # ------------------------------------------------------------------
    def build(self) -> None:
        self._build_appearance()
        self._build_server()
        self._build_npc()
        self._build_transfer()
        self._build_about()
        self._build_navigation()
        self._sync_theme_segment()

    @staticmethod
    def _group_content() -> tuple[QWidget, QVBoxLayout]:
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        return content, layout

    def _add_group(
        self,
        icon: FIF,
        title: str,
        description: str,
        content: QWidget,
    ) -> None:
        self._folders.append((icon, title, description, content))

    def _build_navigation(self) -> None:
        self._settings_stack = QStackedWidget()
        self._settings_stack.setSizePolicy(
            self._settings_stack.sizePolicy().horizontalPolicy(),
            self._settings_stack.sizePolicy().verticalPolicy(),
        )

        list_page = QWidget()
        list_layout = QVBoxLayout(list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(10)
        for icon, title, description, content in self._folders:
            row = FolderRow(icon, title, description)
            row.clicked.connect(
                lambda _checked=False, item=content, name=title, desc=description: self._open_folder(
                    item, name, desc
                )
            )
            list_layout.addWidget(row)
        list_layout.addStretch(1)

        self._folder_detail = FolderDetail("", "")
        self._folder_detail.back_requested.connect(self._close_folder)
        self._tunnel_detail = FolderDetail(
            "隧道管理",
            "每台内网主机自动创建独立 NPS 客户端与 vkey",
        )
        self._tunnel_detail.set_content(self.nps_tunnel_panel)
        self._tunnel_detail.back_requested.connect(self._close_tunnel_detail)
        self._settings_stack.addWidget(list_page)
        self._settings_stack.addWidget(self._folder_detail)
        self._settings_stack.addWidget(self._tunnel_detail)
        self.content_layout.addWidget(self._settings_stack)

    def _open_folder(self, content: QWidget, title: str, description: str) -> None:
        self._folder_detail.set_title(title, description)
        self._folder_detail.set_content(content)
        self._settings_stack.setCurrentWidget(self._folder_detail)
        self.scroll.verticalScrollBar().setValue(0)

    def _close_folder(self) -> None:
        self._folder_detail.set_content(None)
        self._settings_stack.setCurrentIndex(0)
        self.scroll.verticalScrollBar().setValue(0)

    def _open_tunnel_detail(self) -> None:
        self._settings_stack.setCurrentWidget(self._tunnel_detail)
        self.nps_tunnel_panel.on_show()
        self.scroll.verticalScrollBar().setValue(0)

    def _close_tunnel_detail(self) -> None:
        self._settings_stack.setCurrentWidget(self._folder_detail)
        self.scroll.verticalScrollBar().setValue(0)

    # ------------------------------------------------------------------ 外观
    def _build_appearance(self) -> None:
        content, layout = self._group_content()
        self.theme_segment = SegmentedWidget()
        self.theme_segment.addItem("dark", "深色")
        self.theme_segment.addItem("light", "浅色")
        self.theme_segment.addItem("system", "跟随系统")
        self.theme_segment.currentItemChanged.connect(self._on_theme_segment)
        layout.addWidget(self.theme_segment)
        hint = CaptionLabel("主题选择会保存到本地 config.json，所有 Fluent 控件立即刷新。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._add_group(
            FIF.BRIGHTNESS,
            "外观",
            "深色 / 浅色 / 跟随系统",
            content,
        )

    def _on_theme_segment(self, route_key: str) -> None:
        if self._syncing_theme:
            return
        self.ctx.theme.set_theme(route_key)

    def _sync_theme_segment(self, _theme: str | None = None) -> None:
        if not hasattr(self, "theme_segment"):
            return
        self._syncing_theme = True
        try:
            self.theme_segment.setCurrentItem(self.ctx.config.theme)
        finally:
            self._syncing_theme = False

    # ------------------------------------------------------------------ 服务器
    def _build_server(self) -> None:
        content, layout = self._group_content()
        server_widget = QWidget()
        outer = QVBoxLayout(server_widget)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        address_row = QHBoxLayout()
        address_row.setSpacing(8)
        self.server_host_edit = LineEdit()
        self.server_host_edit.setPlaceholderText("例如 47.108.130.177 或 hub.example.com")
        address_row.addWidget(self.server_host_edit, 1)
        address_row.addWidget(CaptionLabel("端口"))
        self.server_port_edit = LineEdit()
        self.server_port_edit.setValidator(QIntValidator(1, 65535, self))
        self.server_port_edit.setPlaceholderText("8000")
        self.server_port_edit.setFixedWidth(90)
        address_row.addWidget(self.server_port_edit)
        outer.addLayout(address_row)
        outer.addWidget(CaptionLabel("协议"))
        self.server_protocol = SegmentedWidget()
        self.server_protocol.addItem("http", "HTTP")
        self.server_protocol.addItem("https", "HTTPS")
        outer.addWidget(self.server_protocol)
        self._sync_server_fields()

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.test_button = make_button(
            "测试连接", icon=FIF.CONNECT, on_click=self._test_server
        )
        action_row.addWidget(self.test_button)
        action_row.addWidget(
            make_button(
                "保存地址", primary=True, icon=FIF.SAVE, on_click=self._save_server
            )
        )
        action_row.addStretch(1)
        outer.addLayout(action_row)
        layout.addLayout(
            form_field(
                "ECS 服务器",
                server_widget,
                "填写服务器 IP/域名与端口，并选择 HTTP 或 HTTPS；修改后立即生效",
            )
        )
        self.server_status = CaptionLabel("")
        self.server_status.setWordWrap(True)
        layout.addWidget(self.server_status)
        self.compat_status = CaptionLabel("")
        self.compat_status.setWordWrap(True)
        layout.addWidget(self.compat_status)
        self._sync_compat_label(self.ctx.server_compat)
        self._add_group(
            FIF.GLOBE,
            "ECS 服务器",
            "登录 API、管理后台与公网地址",
            content,
        )

    def _sync_server_fields(self) -> None:
        scheme, host, port = split_server_url(self.ctx.config.server_url)
        self.server_host_edit.setText(host)
        self.server_port_edit.setText(str(port))
        self.server_protocol.setCurrentItem(scheme)

    def _server_port(self) -> int | None:
        text = self.server_port_edit.text().strip()
        if not text.isdigit():
            return None
        port = int(text)
        return port if 1 <= port <= 65535 else None

    def _server_url_from_fields(self) -> str:
        port = self._server_port()
        if port is None:
            return ""
        scheme = self.server_protocol.currentRouteKey() or "http"
        return build_server_url(scheme, self.server_host_edit.text().strip(), port)

    def _test_server(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        if not self.server_host_edit.text().strip():
            InfoBar.warning(
                "地址为空",
                "请填写服务器 IP 或域名",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        if self._server_port() is None:
            InfoBar.warning(
                "端口无效",
                "请输入 1-65535 之间的端口号",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        url = self._server_url_from_fields()
        self.test_button.setEnabled(False)
        self.test_button.setText("测试中...")
        self.server_status.setText("正在连接服务器...")
        self._worker = FunctionWorker(ApiClient.test_connection, url, parent=self)
        self._worker.succeeded.connect(self._on_test_success)
        self._worker.failed.connect(self._on_test_failed)
        self._worker.start()

    def _on_test_success(self, payload: dict) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        compatibility = ServerCompatibility.from_payload(payload)
        version = compatibility.server_version or payload.get("version") or "未知"
        self.server_status.setText(f"连接成功 · 服务端 v{version}")
        self._sync_compat_label(compatibility)
        if normalize_server_url(self.ctx.config.server_url) == normalize_server_url(
            self._server_url_from_fields()
        ):
            self.ctx.apply_server_compat(payload)
        detail = compatibility.summary
        if compatibility.message:
            detail += f"\n{compatibility.message}"
        if compatibility.error:
            InfoBar.error(
                "客户端版本不兼容",
                compatibility.error,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
            )
        elif compatibility.warning:
            InfoBar.warning(
                "连接成功，存在兼容提示",
                detail,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=6000,
            )
        else:
            InfoBar.success(
                "连接成功",
                detail,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )

    def _on_compat_changed(self, compatibility: ServerCompatibility) -> None:
        self._sync_compat_label(compatibility)

    def _sync_compat_label(self, compatibility: ServerCompatibility) -> None:
        text = compatibility.summary
        if compatibility.message:
            text += f"\n{compatibility.message}"
        self.compat_status.setText(text)

    def _on_test_failed(self, message: str) -> None:
        self.test_button.setEnabled(True)
        self.test_button.setText("测试连接")
        self.server_status.setText(message)
        InfoBar.error("连接失败", message, parent=self, position=InfoBarPosition.TOP_RIGHT)

    def _save_server(self) -> None:
        if not self.server_host_edit.text().strip():
            InfoBar.warning(
                "地址为空",
                "请填写服务器 IP 或域名",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        if self._server_port() is None:
            InfoBar.warning(
                "端口无效",
                "请输入 1-65535 之间的端口号",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        url = self._server_url_from_fields()
        changed = normalize_server_url(url) != normalize_server_url(
            self.ctx.config.server_url
        )
        self.ctx.config.server_url = url
        self.ctx.config.save()
        if changed:
            self.ctx.apply_server_compat({})
        self._sync_server_fields()
        if self.ctx.main_window:
            self.ctx.main_window.refresh_shell()
        self.server_status.setText(f"服务器地址已保存：{url}")
        InfoBar.success(
            "已保存",
            "服务器地址已更新",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    # ------------------------------------------------------------------ NPC / nps
    def _build_npc(self) -> None:
        self.npc_panel = NpcSettingsPanel(self.ctx, self)
        self.nps_tunnel_panel = NpsTunnelPanel(self.ctx, self, embedded=True)
        content, layout = self._group_content()
        layout.addWidget(self.npc_panel)
        self.tunnel_entry = FolderRow(
            FIF.CONNECT,
            "隧道管理",
            "每台内网主机自动创建独立 NPS 客户端与 vkey",
        )
        self.tunnel_entry.clicked.connect(self._open_tunnel_detail)
        layout.addWidget(self.tunnel_entry)
        self._add_group(
            FIF.CONNECT,
            "NPC 管理",
            "NPC 安装、npc.conf、nps 域名与独立隧道 vkey",
            content,
        )

    # ------------------------------------------------------------------ 传输
    def _build_transfer(self) -> None:
        content, layout = self._group_content()
        path_widget = QWidget()
        path_row = QHBoxLayout(path_widget)
        path_row.setContentsMargins(0, 0, 0, 0)
        path_row.setSpacing(8)
        self.download_edit = LineEdit()
        self.download_edit.setText(str(self.ctx.config.download_path))
        path_row.addWidget(self.download_edit, 1)
        path_row.addWidget(
            make_button("浏览...", icon=FIF.FOLDER, on_click=self._choose_download)
        )
        layout.addLayout(form_field("默认下载路径", path_widget, "下载弹窗会默认使用该目录"))

        self.local_port = SpinBox()
        self.local_port.setRange(1024, 65535)
        self.local_port.setValue(self.ctx.config.local_api_port)
        layout.addLayout(
            form_field(
                "本地服务端口",
                self.local_port,
                "临时共享内嵌 FastAPI 监听 127.0.0.1；修改后需重启客户端",
            )
        )
        layout.addWidget(
            Banner(
                "OSS 上传/删除/容量统计全部由 ECS 后端代理，客户端不保存云端密钥。",
                FIF.INFO,
            )
        )
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(
            make_button(
                "保存传输设置", primary=True, icon=FIF.SAVE, on_click=self._save_transfer
            )
        )
        layout.addLayout(actions)
        self._add_group(
            FIF.DOWNLOAD,
            "传输与本地服务",
            "下载目录、本地端口与 OSS 代理说明",
            content,
        )

    def _choose_download(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择默认下载目录", self.download_edit.text()
        )
        if folder:
            self.download_edit.setText(folder)

    def _save_transfer(self) -> None:
        folder = Path(self.download_edit.text().strip()).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            InfoBar.error("无法创建目录", str(exc), parent=self, position=InfoBarPosition.TOP)
            return
        self.ctx.config.download_dir = str(folder)
        self.ctx.config.local_api_port = self.local_port.value()
        self.ctx.config.nps.local_port = self.local_port.value()
        self.ctx.config.save()
        self.download_edit.setText(str(folder))
        InfoBar.success(
            "已保存",
            "传输设置已保存",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def refresh(self) -> None:
        if hasattr(self, "server_host_edit"):
            self._sync_server_fields()
        if hasattr(self, "npc_panel"):
            self.npc_panel.refresh()

    # ------------------------------------------------------------------ 关于
    def _build_about(self) -> None:
        content, layout = self._group_content()
        rows = [
            ("客户端版本", f"NexHub v{VERSION}"),
            ("UI 框架", "PyQt6-Fluent-Widgets"),
            ("服务器地址", self.ctx.config.server_url),
            ("配置文件", str(config_path())),
            ("数据目录", str(app_data_dir())),
        ]
        for label, value in rows:
            row = QHBoxLayout()
            row.addWidget(CaptionLabel(label))
            row.addStretch(1)
            body = BodyLabel(value)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setWordWrap(True)
            row.addWidget(body)
            layout.addLayout(row)
        self.update_status = CaptionLabel(f"当前版本 v{VERSION}")
        self.update_status.setWordWrap(True)
        layout.addWidget(self.update_status)
        actions = QHBoxLayout()
        actions.addStretch(1)
        self.check_update_button = make_button(
            "检查更新", icon=FIF.SYNC, on_click=self._check_update
        )
        actions.addWidget(self.check_update_button)
        actions.addWidget(
            make_button("打开下载页", icon=FIF.GLOBE, on_click=self._open_download_page)
        )
        actions.addWidget(
            make_button("打开数据目录", icon=FIF.FOLDER, on_click=self._open_data_dir)
        )
        actions.addWidget(
            make_button("打开配置文件", icon=FIF.DOCUMENT, on_click=self._open_config)
        )
        layout.addLayout(actions)
        self._add_group(
            FIF.INFO,
            "关于与本地数据",
            "版本信息、配置位置与快捷打开",
            content,
        )

    def _check_update(self) -> None:
        if self._update_worker and self._update_worker.isRunning():
            return
        self.check_update_button.setEnabled(False)
        self.check_update_button.setText("检查中...")
        self.update_status.setText("正在检查更新...")
        self._update_worker = FunctionWorker(
            check_for_updates, self.ctx.config, parent=self
        )
        self._update_worker.succeeded.connect(self._on_update_checked)
        self._update_worker.failed.connect(self._on_update_failed)
        self._update_worker.start()

    def _on_update_checked(self, info: UpdateInfo) -> None:
        self._latest_update = info
        self.check_update_button.setEnabled(True)
        self.check_update_button.setText("检查更新")
        if info.update_available:
            self.update_status.setText(
                f"发现新版本 v{info.latest_version}（当前 v{info.current_version}）"
            )
            InfoBar.success(
                "发现新版本",
                f"可下载 v{info.latest_version}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=5000,
            )
        else:
            self.update_status.setText(
                f"已是最新版本 v{info.current_version} · 下载页：{info.page_url}"
            )
            InfoBar.info(
                "检查完成",
                f"当前已是最新版本 v{info.current_version}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2600,
            )

    def _on_update_failed(self, message: str) -> None:
        self.check_update_button.setEnabled(True)
        self.check_update_button.setText("检查更新")
        self.update_status.setText(f"检查更新失败：{message}")
        InfoBar.error(
            "检查更新失败",
            message,
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _open_download_page(self) -> None:
        if self._latest_update and self._latest_update.page_url:
            target = self._latest_update.page_url
        else:
            target = f"{self.ctx.config.server_url.rstrip('/')}/downloads/"
        QDesktopServices.openUrl(QUrl(target))

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(app_data_dir())))

    def _open_config(self) -> None:
        target = config_path()
        if target.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        else:
            self._open_data_dir()
