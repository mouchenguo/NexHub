"""NexHub Fluent 主窗口。"""
from __future__ import annotations

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import (
    QAction,
    QColor,
    QCloseEvent,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMenu,
    QSystemTrayIcon,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    FluentWindow,
    InfoBar,
    InfoBarPosition,
    NavigationItemPosition,
)

from .context import AppContext
from .paths import VERSION, resource_path
from .views.account import ChangeNicknameDialog, ChangePasswordDialog
from .views.account_panel import AccountPanel
from .views.dashboard import DashboardPage
from .views.my_shares import MySharesPage
from .views.rdp import RdpPage
from .views.settings import SettingsPage
from .views.square import SquarePage
from .views.toolbox import ToolboxPage
from .widgets.common import ask_confirm
from .workers import FunctionWorker

PAGE_DEFS = [
    ("dashboard", DashboardPage, FIF.HOME, "仪表盘"),
    ("my_shares", MySharesPage, FIF.SHARE, "我的共享"),
    ("square", SquarePage, FIF.GLOBE, "共享广场"),
    ("rdp", RdpPage, FIF.PROJECTOR, "RDP 主机"),
    ("toolbox", ToolboxPage, FIF.DEVELOPER_TOOLS, "工具箱"),
    ("settings", SettingsPage, FIF.SETTING, "设置"),
]

INFO_METHODS = {
    "success": InfoBar.success,
    "error": InfoBar.error,
    "warning": InfoBar.warning,
    "info": InfoBar.info,
}

INFO_TITLES = {
    "success": "操作成功",
    "error": "操作失败",
    "warning": "提示",
    "info": "信息",
}


def build_app_icon() -> QIcon:
    """优先使用打包的 ICO，缺失时再用画布生成品牌图标。"""
    icon_file = resource_path("nexhub.ico")
    if icon_file.exists():
        return QIcon(str(icon_file))
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0, QColor("#4f8cff"))
    gradient.setColorAt(1, QColor("#9b7bff"))
    painter.setBrush(gradient)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 60, 60, 16, 16)
    painter.setPen(QPen(QColor("#ffffff")))
    font = painter.font()
    font.setPointSize(28)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "N")
    painter.end()
    return QIcon(pixmap)


class MainWindow(FluentWindow):
    """使用 FluentWindow 自带可折叠侧边导航。"""

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.ctx.attach_window(self)
        self._pages: dict[str, QWidget] = {}
        self._sidebar_expanded = False
        self._avatar_worker = None
        self._avatar_upload_worker = None
        self._avatar_reload_pending = False
        self._allow_close = False
        self._tray_notified = False
        self._startup_update_checked = False
        self._tray: QSystemTrayIcon | None = None
        self._build_window()
        self._add_pages()
        self.account_panel = AccountPanel(self.ctx, self)
        self.account_panel.avatar_requested.connect(self._choose_avatar)
        self.account_panel.nickname_requested.connect(self._change_nickname)
        self.account_panel.password_requested.connect(self._change_password)
        self.ctx.connection_changed.connect(self._on_connection_changed)
        self.ctx.profile_changed.connect(self._on_profile_changed)
        self._setup_tray()
        QTimer.singleShot(1600, self._check_updates_on_startup)
        if self.ctx.server_compat.warning:
            QTimer.singleShot(
                1200,
                lambda: self.show_toast(self.ctx.server_compat.warning, "warning"),
            )
        # qfluentwidgets 在首次 addSubInterface 时会把尚未显示窗口重置为
        # 500x500，因此初始尺寸必须在页面全部添加完成后再应用。
        self._apply_initial_geometry()
        self.stackedWidget.currentChanged.connect(self._on_page_changed)
        QTimer.singleShot(0, self._initial_page)
        QTimer.singleShot(80, self._expand_sidebar)
        QTimer.singleShot(700, self._check_npc_startup)

    # ------------------------------------------------------------------
    def _build_window(self) -> None:
        self.setWindowTitle("NexHub · 文件共享与远程主机")
        self.setWindowIcon(build_app_icon())

        # 纯色 Fluent 背景，保证 Win10/Win11 与截图效果一致；
        # 如需 Windows 11 Mica，可改为 setMicaEffectEnabled(True)。
        self.setMicaEffectEnabled(False)
        self.setCustomBackgroundColor(QColor("#f3f6fb"), QColor("#1f1f1f"))

        nav = self.navigationInterface
        nav.setExpandWidth(232)
        nav.setMinimumExpandWidth(1040)
        nav.setCollapsible(True)
        nav.setReturnButtonVisible(False)
        nav.setMenuButtonVisible(True)
        nav.addItemHeader("工作空间")

    def _apply_initial_geometry(self) -> None:
        """按屏幕可用区域决定初始尺寸；必须在页面添加完成后调用。"""
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        screen_width = max(640, available.width() if available else 1360)
        screen_height = max(480, available.height() if available else 880)
        width = min(1360, max(720, int(screen_width * 0.92)), screen_width)
        height = min(880, max(640, int(screen_height * 0.90)), screen_height)
        min_width = min(1120, max(640, int(screen_width * 0.80)))
        min_height = min(720, max(480, int(screen_height * 0.75)))
        self.setMinimumSize(min_width, min_height)
        self.resize(width, height)
        if available is not None:
            self.move(
                available.x() + max(0, (available.width() - width) // 2),
                available.y() + max(0, (available.height() - height) // 2),
            )

    def _add_pages(self) -> None:
        nav = self.navigationInterface
        for page_id, page_class, icon, text in PAGE_DEFS:
            if page_id == "toolbox":
                nav.addItemHeader("工具箱")
            page = page_class(self.ctx, self)
            self._pages[page_id] = page
            self.addSubInterface(page, icon, text, NavigationItemPosition.TOP)

        display = self.ctx.config.display_name or self.ctx.config.username or "本地用户"
        role = "管理员" if self.ctx.config.role == "admin" else "普通用户"
        if self.ctx.config.demo_mode:
            role = "点击登录"
        elif self.ctx.config.username:
            role = f"@{self.ctx.config.username} · {role}"
        self.setWindowTitle(f"NexHub v{VERSION} · {display}")
        self.user_card = nav.addUserCard(
            "user-card",
            FIF.PEOPLE,
            display,
            role,
            onClick=self._show_account_panel,
            position=NavigationItemPosition.BOTTOM,
            aboveMenuButton=True,
        )
        self.user_card.setToolTip("点击打开账户面板")
        QTimer.singleShot(250, self._load_user_avatar)

    def _initial_page(self) -> None:
        current = self.stackedWidget.currentWidget()
        if current:
            self._on_page_changed(self.stackedWidget.currentIndex())
        self.navigate("dashboard")

    def _expand_sidebar(self) -> None:
        if not self._sidebar_expanded and self.width() >= 1040:
            self._sidebar_expanded = True
            self.navigationInterface.expand(useAni=False)

    def _check_npc_startup(self) -> None:
        settings_page = self._pages.get("settings")
        panel = getattr(settings_page, "npc_panel", None)
        check = getattr(panel, "startup_check", None)
        if callable(check):
            check()

    def _check_updates_on_startup(self) -> None:
        """登录状态下每次启动最多自动检查一次；失败静默处理。"""
        if self._startup_update_checked or not self.ctx.online:
            return
        self._startup_update_checked = True
        settings_page = self._pages.get("settings")
        check = getattr(settings_page, "check_updates", None)
        if callable(check):
            check(auto=True)

    # ------------------------------------------------------------------ 用户卡片
    def _show_account_panel(self) -> None:
        if self.account_panel.isVisible():
            self.account_panel.close_panel()
        else:
            self.account_panel.open_panel()

    def _choose_avatar(self) -> None:
        if not self.ctx.online:
            self.show_toast("离线体验模式不支持上传头像", "warning")
            return
        if self._avatar_upload_worker and self._avatar_upload_worker.isRunning():
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择头像",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        self.show_toast("正在上传头像...", "info")
        self._avatar_upload_worker = FunctionWorker(
            self.ctx.api.upload_avatar, path, parent=self
        )
        self._avatar_upload_worker.succeeded.connect(self._on_avatar_uploaded)
        self._avatar_upload_worker.failed.connect(
            lambda message: self.show_toast(f"头像上传失败：{message}", "error")
        )
        self._avatar_upload_worker.start()

    def _on_avatar_uploaded(self, result: dict) -> None:
        user = result.get("user", {}) if isinstance(result, dict) else {}
        if user.get("display_name"):
            self.ctx.config.display_name = user["display_name"]
            self.ctx.config.save()
        self._load_user_avatar()
        self.refresh_shell()
        self.show_toast("头像已更新", "success")

    def _load_user_avatar(self) -> None:
        if not self.ctx.online:
            self._set_default_avatar()
            return
        if self._avatar_worker and self._avatar_worker.isRunning():
            self._avatar_reload_pending = True
            return
        self._avatar_worker = FunctionWorker(self.ctx.api.fetch_avatar, parent=self)
        self._avatar_worker.succeeded.connect(self._apply_avatar)
        self._avatar_worker.failed.connect(lambda _message: self._set_default_avatar())
        self._avatar_worker.finished.connect(self._on_avatar_worker_finished)
        self._avatar_worker.start()

    def _on_avatar_worker_finished(self) -> None:
        if self._avatar_reload_pending:
            self._avatar_reload_pending = False
            QTimer.singleShot(0, self._load_user_avatar)

    def _apply_avatar(self, data: bytes) -> None:
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(data):
                self.user_card.setAvatar(pixmap)
                panel = getattr(self, "account_panel", None)
                if panel is not None:
                    panel.profile_avatar.setImage(pixmap)
                self._restore_avatar_radius()
                return
        self._set_default_avatar()

    def _set_default_avatar(self) -> None:
        self.user_card.setAvatarIcon(FIF.PEOPLE)
        panel = getattr(self, "account_panel", None)
        if panel is not None:
            display = self.ctx.config.display_name or self.ctx.config.username or "用户"
            panel.profile_avatar.setText(display[:2])
        self._restore_avatar_radius()

    def _restore_avatar_radius(self) -> None:
        compacted = bool(getattr(self.user_card, "isCompacted", False))
        self.user_card.avatar.setRadius(12 if compacted else 32)
        self.user_card.update()

    def _change_nickname(self) -> None:
        if not self.ctx.online:
            self.show_toast("离线体验模式不支持修改昵称", "warning")
            return
        if not self.ctx.can_update_nickname:
            self.show_toast("当前服务端不支持修改昵称，请升级服务端", "warning")
            return
        ChangeNicknameDialog(self.ctx, self).exec()

    def _change_password(self) -> None:
        if not self.ctx.online:
            self.show_toast("离线体验模式不支持修改密码", "warning")
            return
        ChangePasswordDialog(self.ctx, self).exec()

    def _logout(self) -> None:
        if ask_confirm(self, "退出登录", "确定退出当前账号？本地设置与主题会被保留。"):
            self.ctx.logout()

    # ------------------------------------------------------------------
    def navigate(self, page_id: str) -> None:
        page = self._pages.get(page_id)
        if page is None:
            return
        if self.stackedWidget.currentWidget() is page:
            page.on_show()
            return
        self.switchTo(page)

    def _on_page_changed(self, index: int) -> None:
        page = self.stackedWidget.widget(index)
        if page is not None and hasattr(page, "on_show"):
            page.on_show()

    def refresh_shell(self) -> None:
        display = self.ctx.config.display_name or self.ctx.config.username or "本地用户"
        self.setWindowTitle(f"NexHub v{VERSION} · {display}")
        if hasattr(self, "user_card"):
            if self.ctx.online:
                role = "管理员" if self.ctx.config.role == "admin" else "普通用户"
                if self.ctx.config.username:
                    role = f"@{self.ctx.config.username} · {role}"
            else:
                role = "点击登录"
            set_title = getattr(self.user_card, "setTitle", None)
            set_subtitle = getattr(self.user_card, "setSubtitle", None)
            title_getter = getattr(self.user_card, "title", None)
            subtitle_getter = getattr(self.user_card, "subtitle", None)
            current_title = title_getter() if callable(title_getter) else None
            current_subtitle = subtitle_getter() if callable(subtitle_getter) else None
            if callable(set_title) and current_title != display:
                set_title(display)
            elif not callable(set_title):
                self.user_card.setName(display)
            if callable(set_subtitle) and current_subtitle != role:
                set_subtitle(role)
            self.user_card.setToolTip(f"{display} · {role} · 点击打开账户面板")
        for page in self._pages.values():
            sync = getattr(page, "_sync_header", None)
            if callable(sync):
                sync()
        panel = getattr(self, "account_panel", None)
        if panel is not None:
            panel._sync_state()

    def _on_connection_changed(self, online: bool) -> None:
        self.refresh_shell()
        if online:
            self._load_user_avatar()
            if not self._startup_update_checked:
                QTimer.singleShot(900, self._check_updates_on_startup)
        else:
            self._set_default_avatar()
        current = self.stackedWidget.currentWidget()
        if current is not None and hasattr(current, "on_show"):
            current.on_show()
        # 登录/退出后即使当前不在 RDP 页，也刷新同账号在线列表缓存。
        rdp_page = self._pages.get("rdp")
        if rdp_page is not None and current is not rdp_page:
            refresh = getattr(rdp_page, "refresh", None)
            if callable(refresh):
                refresh()

    def _on_profile_changed(self, _user: dict) -> None:
        """昵称更新后同步窗口标题、用户卡片和当前页面。"""
        self.refresh_shell()
        current = self.stackedWidget.currentWidget()
        if current is not None and hasattr(current, "on_show"):
            current.on_show()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        panel = getattr(self, "account_panel", None)
        if panel is not None:
            panel.sync_geometry()

    # ------------------------------------------------------------------
    def show_toast(self, message: str, tone: str = "success") -> None:
        method = INFO_METHODS.get(tone, InfoBar.info)
        method(
            INFO_TITLES.get(tone, "信息"),
            message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP_RIGHT,
            duration=2600,
            parent=self,
        )

    # ------------------------------------------------------------------ 托盘
    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self.windowIcon(), self)
        menu = QMenu()
        show_action = QAction("显示 NexHub", menu)
        show_action.triggered.connect(self.restore_from_tray)
        quit_action = QAction("退出 NexHub", menu)
        quit_action.triggered.connect(self.request_quit)
        menu.addAction(show_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip("NexHub · 文件共享与 RDP")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()
        # 隐藏窗口后应用不能自动退出，设备心跳仍需继续运行。
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(False)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.restore_from_tray()

    def hide_to_tray(self) -> None:
        if self._allow_close:
            return
        if self._tray is None:
            self.request_quit()
            return
        self.hide()
        if not self._tray_notified:
            self._tray_notified = True
            self._tray.showMessage(
                "NexHub 仍在运行",
                "已最小化到系统托盘，双击托盘图标可重新打开。",
                QSystemTrayIcon.MessageIcon.Information,
                2600,
            )

    def restore_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def prepare_close(self) -> None:
        """供退出登录时绕过托盘拦截。"""
        self._allow_close = True
        if self._tray:
            self._tray.hide()

    def request_quit(self) -> None:
        self._allow_close = True
        if self._tray:
            self._tray.hide()
        try:
            self.ctx.shutdown()
        finally:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._allow_close:
            event.ignore()
            self.hide_to_tray()
            return
        self.ctx.shutdown()
        super().closeEvent(event)
