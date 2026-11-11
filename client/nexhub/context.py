"""应用上下文：集中持有配置、服务与主题，供各页面共享。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QObject, pyqtSignal

from .api_client import ApiClient
from .compat import FEATURE_PROFILE_UPDATE, ServerCompatibility
from .config import AppConfig
from .embedded_server import EmbeddedServer
from .services.demo_store import DemoStore
from .services.credential_store import CredentialStore
from .services.device_service import DeviceService
from .services.download_service import DownloadService
from .services.plugin_manager import PluginManager
from .services.plugin_processes import PluginProcessManager
from .services.rdp_service import RdpService
from .services.share_service import ShareService
from .services.tunnel_service import TunnelService
from .theme import ThemeManager
from .workspace import WorkspaceRegistry

if TYPE_CHECKING:
    from .app import MainWindow


class AppContext(QObject):
    connection_changed = pyqtSignal(bool)
    server_compat_changed = pyqtSignal(object)
    profile_changed = pyqtSignal(dict)
    logout_requested = pyqtSignal()
    workspace_changed = pyqtSignal()

    def __init__(
        self,
        config: AppConfig,
        theme: ThemeManager,
        parent: QObject | None = None,
        server_payload: dict | None = None,
    ):
        super().__init__(parent)
        self.config = config
        self.theme = theme
        self.api = ApiClient(config)
        self.server_compat = ServerCompatibility()
        if server_payload:
            self.apply_server_compat(server_payload)
        self.embedded = EmbeddedServer(config.local_api_port)
        self.demo = DemoStore(config)
        self.downloads = DownloadService(config, self.api, self.demo)
        self.shares = ShareService(config, self.api, self.demo, self.embedded, self.downloads)
        self.rdp = RdpService(config)
        self.devices = DeviceService(config, self.api)
        self.tunnels = TunnelService(config, self.api, self.demo)
        self.plugins = PluginManager(set(config.disabled_plugins))
        self.plugins.reload_custom()
        self.plugin_processes = PluginProcessManager(self)
        self.workspace = WorkspaceRegistry(config, is_admin=self.is_admin)
        # 首次升级时按“已安装且未禁用”初始化工作空间插件
        if self.workspace.normalize(self.plugins.installed_ids()):
            config.save()
        self.theme.theme_changed.connect(self._sync_plugin_theme)
        self.main_window: "MainWindow | None" = None
        self._shutdown_done = False
        self.devices.start()

    @property
    def online(self) -> bool:
        return not self.config.demo_mode

    @property
    def is_admin(self) -> bool:
        return self.config.role == "admin"

    @property
    def can_update_nickname(self) -> bool:
        """旧服务端未声明 profile_update 时隐藏昵称编辑入口。"""
        if not self.online:
            return False
        compat = self.server_compat
        if not compat.checked:
            return True
        if compat.legacy:
            return False
        return compat.supports(FEATURE_PROFILE_UPDATE)

    def attach_window(self, window: "MainWindow") -> None:
        self.main_window = window

    def notify(self, message: str, tone: str = "success") -> None:
        if self.main_window:
            self.main_window.show_toast(message, tone)

    def navigate(self, page_id: str) -> None:
        if self.main_window:
            self.main_window.navigate(page_id)

    # ------------------------------------------------------------------ 工作空间
    def save_workspace(self) -> None:
        """清洗并持久化工作空间配置，随后通知界面重建导航。"""
        self.workspace.normalize(self.plugins.installed_ids())
        self.config.save()
        self.workspace_changed.emit()

    def set_workspace_page_hidden(self, page_id: str, hidden: bool) -> bool:
        if not self.workspace.set_hidden(page_id, hidden):
            return False
        self.save_workspace()
        return True

    def load_plugin_workspace(self, plugin_id: str, *, start: bool = True) -> bool:
        """把插件加载到工作空间：创建选项卡，可选同时启动宿主进程。"""
        record = self.plugins.get(plugin_id)
        if record is None:
            raise ValueError("插件未安装")
        changed = self.workspace.load_plugin(plugin_id)
        if start:
            try:
                self.plugin_processes.start(record, hidden=True)
                self._set_plugin_active(plugin_id, True)
            except Exception as exc:  # noqa: BLE001 - 启动失败不阻止选项卡建立
                self.notify(f"插件「{record.name}」启动失败：{exc}", "warning")
        self.save_workspace()
        return changed

    def unload_plugin_workspace(self, plugin_id: str) -> bool:
        """从工作空间移除：停止宿主、删除选项卡，插件包与数据保留。"""
        changed = self.workspace.unload_plugin(plugin_id)
        self.plugin_processes.stop(plugin_id)
        self._set_plugin_active(plugin_id, False)
        self.save_workspace()
        return changed

    def uninstall_plugin(self, plugin_id: str) -> None:
        """彻底卸载：停止进程、删除插件包与数据、清理所有配置引用。"""
        from .services.plugin_market import uninstall_plugin as _uninstall

        record = self.plugins.get(plugin_id)
        name = record.name if record else plugin_id
        self.plugin_processes.stop(plugin_id)
        _uninstall(plugin_id)
        self.workspace.unload_plugin(plugin_id)
        self.config.disabled_plugins = [
            item for item in self.config.disabled_plugins if item != plugin_id
        ]
        self.config.workspace_tab_order = [
            item for item in self.config.workspace_tab_order if item != plugin_id
        ]
        self.plugins.reload_custom()
        self.save_workspace()
        self.notify(f"插件「{name}」已卸载", "success")

    def _set_plugin_active(self, plugin_id: str, active: bool) -> None:
        ids = set(self.config.active_plugins)
        if active:
            ids.add(plugin_id)
        else:
            ids.discard(plugin_id)
        self.config.active_plugins = sorted(ids)
        self.config.save()

    def set_plugin_active(self, plugin_id: str, active: bool) -> None:
        """公开接口：记录插件宿主是否应在下次启动时自动恢复。"""
        self._set_plugin_active(plugin_id, active)

    def open_workspace_manager(self) -> None:
        if self.main_window:
            self.main_window.open_workspace_manager()

    def login(self, identifier: str, password: str, remember: bool = False) -> dict:
        """使用用户名或昵称登录；不把 token / 密码明文写盘。"""
        identifier = identifier.strip()
        if not identifier or not password:
            raise ValueError("请填写账号/昵称与密码")
        result = self.api.login(identifier, password)
        user = result.get("user", {}) if isinstance(result, dict) else {}
        actual_username = str(user.get("username") or identifier)
        return self._start_session(result, actual_username, password, remember)

    def register(self, username: str, password: str, display_name: str = "") -> dict:
        """提交注册；服务端自动审核通过时直接建立登录会话。"""
        username = username.strip()
        display_name = display_name.strip()
        if not username or not password:
            raise ValueError("请填写用户名与密码")
        result = self.api.register(username, password, display_name)
        if result.get("token"):
            self._start_session(result, username, password, remember=False)
        return result

    def update_nickname(self, display_name: str) -> dict:
        """修改昵称；登录用户名保持不变，仅更新展示名称。"""
        if not self.online:
            raise ValueError("离线体验模式不支持修改昵称")
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("昵称不能为空")
        if len(display_name) > 32:
            raise ValueError("昵称不能超过 32 个字符")
        if any(ord(char) < 32 or ord(char) == 127 for char in display_name):
            raise ValueError("昵称不能包含换行或控制字符")
        result = self.api.update_profile(display_name)
        user = result.get("user", {}) if isinstance(result, dict) else {}
        updated = str(user.get("display_name") or display_name)
        self.config.display_name = updated
        self.config.save()
        self.profile_changed.emit(user)
        return result

    def _start_session(
        self, result: dict, username: str, password: str, remember: bool
    ) -> dict:
        user = result.get("user", {})
        token = str(result.get("token", ""))
        if not token:
            raise ValueError("服务器未返回登录凭证")
        compat_payload = result.get("server")
        if not isinstance(compat_payload, dict):
            try:
                compat_payload = self.api.health()
            except Exception:
                compat_payload = {}
        compatibility = self.apply_server_compat(compat_payload)
        if compatibility.client_too_old:
            raise ValueError(compatibility.error)
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
        # 登录后角色可能从普通用户变为管理员：仪表盘选项卡随之出现
        self.workspace.set_admin(self.config.role == "admin")
        self.workspace.normalize(self.plugins.installed_ids())
        self.config.save()
        self.devices.start()
        self.connection_changed.emit(True)
        return user

    def apply_server_compat(self, payload: dict | None) -> ServerCompatibility:
        compatibility = ServerCompatibility.from_payload(payload)
        self.server_compat = compatibility
        self.server_compat_changed.emit(compatibility)
        return compatibility

    def _sync_plugin_theme(self, _theme: str) -> None:
        self.plugin_processes.set_theme(self.theme.effective_theme)

    def logout(self) -> None:
        """停止心跳并立即回到演示模式。"""
        self.devices.stop()
        self.config.enter_demo()
        self.apply_server_compat({})
        # 退出登录即失去管理员身份，仪表盘选项卡需要立即移除
        self.workspace.set_admin(False)
        self.workspace.normalize(self.plugins.installed_ids())
        self.workspace_changed.emit()
        self.connection_changed.emit(False)
        self.logout_requested.emit()

    def shutdown(self) -> None:
        if self._shutdown_done:
            return
        self._shutdown_done = True
        try:
            self.plugin_processes.stop_all()
        finally:
            try:
                self.devices.stop()
            finally:
                try:
                    self.embedded.stop()
                finally:
                    self.api.close()
