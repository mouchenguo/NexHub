"""应用上下文：集中持有配置、服务与主题，供各页面共享。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from .api_client import ApiClient
from .config import AppConfig
from .embedded_server import EmbeddedServer
from .plugins.memory_plugin import MemoryOptimizerPlugin
from .services.demo_store import DemoStore
from .services.credential_store import CredentialStore
from .services.device_service import DeviceService
from .services.download_service import DownloadService
from .services.plugin_manager import PluginManager
from .services.rdp_service import RdpService
from .services.share_service import ShareService
from .services.tunnel_service import TunnelService
from .theme import ThemeManager

if TYPE_CHECKING:
    from .app import MainWindow


class AppContext(QObject):
    connection_changed = pyqtSignal(bool)
    logout_requested = pyqtSignal()

    def __init__(self, config: AppConfig, theme: ThemeManager, parent: QObject | None = None):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.api = ApiClient(config)
        self.embedded = EmbeddedServer(config.local_api_port)
        self.demo = DemoStore(config)
        self.downloads = DownloadService(config, self.api, self.demo)
        self.shares = ShareService(config, self.api, self.demo, self.embedded, self.downloads)
        self.rdp = RdpService(config)
        self.devices = DeviceService(config, self.api)
        self.tunnels = TunnelService(config, self.api, self.demo)
        self.plugins = PluginManager(set(config.disabled_plugins))
        self.plugins.register(MemoryOptimizerPlugin())
        self.plugins.reload_custom()
        self.main_window: "MainWindow | None" = None
        self._shutdown_done = False
        self.devices.start()

    @property
    def online(self) -> bool:
        return not self.config.demo_mode

    @property
    def is_admin(self) -> bool:
        return self.config.role == "admin"

    def attach_window(self, window: "MainWindow") -> None:
        self.main_window = window

    def notify(self, message: str, tone: str = "success") -> None:
        if self.main_window:
            self.main_window.show_toast(message, tone)

    def navigate(self, page_id: str) -> None:
        if self.main_window:
            self.main_window.navigate(page_id)

    def login(self, username: str, password: str, remember: bool = False) -> dict:
        """登录并切换到在线模式；不把 token / 密码明文写盘。"""
        username = username.strip()
        if not username or not password:
            raise ValueError("请填写账号与密码")
        result = self.api.login(username, password)
        user = result.get("user", {})
        token = str(result.get("token", ""))
        if not token:
            raise ValueError("服务器未返回登录凭证")
        credential_enc = CredentialStore.save(username, password) if remember else ""
        self.config.token = token
        self.config.username = str(user.get("username", username))
        self.config.display_name = str(
            user.get("display_name") or self.config.username
        )
        self.config.role = str(user.get("role", "user"))
        self.config.demo_mode = False
        self.config.remember_me = bool(remember)
        self.config.credential_enc = credential_enc
        self.config.save()
        self.devices.start()
        self.connection_changed.emit(True)
        return user

    def logout(self) -> None:
        """停止心跳并立即回到演示模式。"""
        self.devices.stop()
        self.config.enter_demo()
        self.connection_changed.emit(False)
        self.logout_requested.emit()

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.devices.stop()
        finally:
            try:
                self.embedded.stop()
            finally:
                self.api.close()
