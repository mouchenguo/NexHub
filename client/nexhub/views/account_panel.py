"""内嵌账户面板：演示模式登录 / 已登录资料，不创建独立窗口。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator
from PyQt6.QtWidgets import QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    AvatarWidget,
    CaptionLabel,
    CardWidget,
    CheckBox,
    FluentIcon as FIF,
    LineEdit,
    PasswordLineEdit,
    SegmentedWidget,
    StrongBodyLabel,
    TitleLabel,
)

from ..config import build_server_url, split_server_url
from ..widgets.common import icon_button, make_button


class AccountPanel(QWidget):
    avatar_requested = pyqtSignal()
    password_requested = pyqtSignal()

    CARD_WIDTH = 430

    def __init__(self, ctx, parent: QWidget):
        super().__init__(parent)
        self.ctx = ctx
        self._worker = None
        self.setObjectName("AccountPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._build()
        self.ctx.theme.theme_changed.connect(self._sync_background)
        self._sync_background()
        self._sync_state()
        self.hide()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addStretch(1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.card = CardWidget(self)
        self.card.setObjectName("AccountCard")
        self.card.setBorderRadius(14)
        self.card.setFixedWidth(self.CARD_WIDTH)
        vertical = QVBoxLayout(self.card)
        vertical.setContentsMargins(26, 22, 26, 24)
        vertical.setSpacing(10)

        header = QHBoxLayout()
        header.addWidget(TitleLabel("账户"))
        header.addStretch(1)
        header.addWidget(icon_button(FIF.CLOSE, tooltip="关闭", on_click=self.close_panel))
        vertical.addLayout(header)

        self.stack = QStackedWidget()
        self.login_page = self._build_login_page()
        self.profile_page = self._build_profile_page()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.profile_page)
        vertical.addWidget(self.stack, 1)
        row.addWidget(self.card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(StrongBodyLabel("登录 NexHub"))
        layout.addSpacing(8)

        layout.addWidget(CaptionLabel("服务器 IP / 域名"))
        server_row = QHBoxLayout()
        server_row.setSpacing(8)
        self.server_host_edit = LineEdit()
        self.server_host_edit.setPlaceholderText("例如 47.108.130.177")
        server_row.addWidget(self.server_host_edit, 1)
        server_row.addWidget(CaptionLabel("端口"))
        self.server_port_edit = LineEdit()
        self.server_port_edit.setValidator(QIntValidator(1, 65535, self))
        self.server_port_edit.setPlaceholderText("8000")
        self.server_port_edit.setFixedWidth(90)
        server_row.addWidget(self.server_port_edit)
        layout.addLayout(server_row)
        layout.addWidget(CaptionLabel("协议"))
        self.protocol_segment = SegmentedWidget()
        self.protocol_segment.addItem("http", "HTTP")
        self.protocol_segment.addItem("https", "HTTPS")
        layout.addWidget(self.protocol_segment)
        layout.addSpacing(6)

        layout.addWidget(CaptionLabel("账号"))
        self.username_edit = LineEdit()
        self.username_edit.setPlaceholderText("请输入用户名")
        layout.addWidget(self.username_edit)
        layout.addWidget(CaptionLabel("密码"))
        self.password_edit = PasswordLineEdit()
        self.password_edit.setPlaceholderText("请输入密码")
        self.password_edit.returnPressed.connect(self._login)
        layout.addWidget(self.password_edit)
        self.remember_check = CheckBox("记住我（本机加密保存）")
        layout.addWidget(self.remember_check)

        self.login_status = CaptionLabel("")
        self.login_status.setWordWrap(True)
        layout.addWidget(self.login_status)
        self.login_button = make_button(
            "登录", primary=True, icon=FIF.FINGERPRINT, on_click=self._login
        )
        layout.addWidget(self.login_button)
        layout.addSpacing(4)
        footer = CaptionLabel("不勾选“记住我”时，下次启动默认进入演示预览模式。")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        server_hint = CaptionLabel("登录后服务器地址会同步保存到「设置 → ECS 服务器」。")
        server_hint.setWordWrap(True)
        layout.addWidget(server_hint)
        layout.addStretch(1)
        return page

    def _build_profile_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        layout.setSpacing(9)
        row = QHBoxLayout()
        self.profile_avatar = AvatarWidget()
        self.profile_avatar.setRadius(26)
        row.addWidget(self.profile_avatar)
        info = QVBoxLayout()
        info.setSpacing(2)
        self.profile_name = StrongBodyLabel("--")
        self.profile_user = CaptionLabel("@--")
        info.addWidget(self.profile_name)
        info.addWidget(self.profile_user)
        row.addLayout(info, 1)
        layout.addLayout(row)
        layout.addWidget(CaptionLabel("已登录，在线设备心跳与云端功能已启用。"))
        layout.addSpacing(6)
        layout.addWidget(
            make_button("更换头像", icon=FIF.CAMERA, on_click=lambda: self.avatar_requested.emit())
        )
        layout.addWidget(
            make_button(
                "修改密码", icon=FIF.FINGERPRINT, on_click=lambda: self.password_requested.emit()
            )
        )
        layout.addWidget(make_button("退出登录", icon=FIF.CANCEL, on_click=self._logout))
        layout.addStretch(1)
        return page
    # ------------------------------------------------------------------
    def _sync_server_fields(self) -> None:
        scheme, host, port = split_server_url(self.ctx.config.server_url)
        self.server_host_edit.setText(host)
        self.server_port_edit.setText(str(port))
        self.protocol_segment.setCurrentItem(scheme)

    def _server_port(self) -> int | None:
        text = self.server_port_edit.text().strip()
        if not text.isdigit():
            return None
        port = int(text)
        return port if 1 <= port <= 65535 else None

    def _sync_state(self) -> None:
        online = self.ctx.online
        self.stack.setCurrentWidget(self.profile_page if online else self.login_page)
        if online:
            display = self.ctx.config.display_name or self.ctx.config.username or "已登录"
            self.profile_name.setText(display)
            self.profile_user.setText(f"@{self.ctx.config.username}")
            self.profile_avatar.setText(display[:2])
        else:
            self.remember_check.setChecked(bool(self.ctx.config.remember_me))

    def _sync_background(self, *_args) -> None:
        dark = self.ctx.theme.effective_theme == "dark"
        color = "#1f1f1f" if dark else "#f3f6fb"
        self.setStyleSheet(
            f"QWidget#AccountPanel {{ background-color: {color}; }}"
        )

    def sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        card_width = min(self.CARD_WIDTH, max(320, parent.width() - 40))
        self.card.setFixedWidth(card_width)
        self.setGeometry(parent.rect())

    def open_panel(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.sync_geometry()
        self._sync_background()
        self._sync_server_fields()
        self._sync_state()
        self.show()
        self.raise_()
        if not self.ctx.online:
            self.username_edit.setFocus()

    def close_panel(self) -> None:
        self.hide()

    # ------------------------------------------------------------------
    def _login(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        host = self.server_host_edit.text().strip()
        if not username or not password:
            self.login_status.setText("请填写账号与密码")
            return
        if not host:
            self.login_status.setText("请填写服务器 IP 或域名")
            return
        port = self._server_port()
        if port is None:
            self.login_status.setText("请填写有效的端口号（1-65535）")
            return
        scheme = self.protocol_segment.currentRouteKey() or "http"
        server_url = build_server_url(scheme, host, port)
        if not server_url:
            self.login_status.setText("服务器地址无效")
            return
        self.ctx.config.server_url = server_url
        self.ctx.config.save()
        self.login_button.setEnabled(False)
        self.login_button.setText("正在登录...")
        self.login_status.setText("正在连接服务器...")
        from ..workers import FunctionWorker

        self._worker = FunctionWorker(
            self.ctx.login, username, password, self.remember_check.isChecked(), parent=self
        )
        self._worker.succeeded.connect(self._on_login_success)
        self._worker.failed.connect(self._on_login_failed)
        self._worker.start()

    def _on_login_success(self, _user: dict) -> None:
        self.login_button.setEnabled(True)
        self.login_button.setText("登录")
        self.password_edit.clear()
        self.login_status.setText("登录成功")
        self._sync_state()
        self.close_panel()
        self.ctx.navigate("dashboard")
        self.ctx.notify("登录成功", "success")

    def _on_login_failed(self, message: str) -> None:
        self.login_button.setEnabled(True)
        self.login_button.setText("登录")
        self.login_status.setText(f"登录失败：{message}")

    def _logout(self) -> None:
        from ..widgets.common import ask_confirm

        if not ask_confirm(self, "退出登录", "退出后将立即回到演示预览模式，确定退出吗？"):
            return
        self.ctx.logout()
        self.username_edit.clear()
        self.password_edit.clear()
        self.login_status.setText("已退出登录，当前为演示预览模式")
        self._sync_state()

