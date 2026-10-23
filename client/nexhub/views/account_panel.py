"""内嵌账户面板：登录 / 注册 / 已登录资料，不创建独立窗口。"""
from __future__ import annotations

import re

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
    nickname_requested = pyqtSignal()
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

        self.auth_title = StrongBodyLabel("登录 NexHub")
        layout.addWidget(self.auth_title)
        self.auth_segment = SegmentedWidget()
        self.auth_segment.addItem("login", "登录")
        self.auth_segment.addItem("register", "注册")
        self.auth_segment.currentItemChanged.connect(self._on_auth_mode_changed)
        layout.addWidget(self.auth_segment)
        layout.addSpacing(4)

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

        self.auth_stack = QStackedWidget()
        self.login_form = self._build_login_form()
        self.register_form = self._build_register_form()
        self.auth_stack.addWidget(self.login_form)
        self.auth_stack.addWidget(self.register_form)
        layout.addWidget(self.auth_stack)

        footer = CaptionLabel("服务器地址会同步保存到「设置 → ECS 服务器」。")
        footer.setWordWrap(True)
        layout.addWidget(footer)
        layout.addStretch(1)
        return page

    def _build_login_form(self) -> QWidget:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(CaptionLabel("账号或昵称"))
        self.username_edit = LineEdit()
        self.username_edit.setPlaceholderText("请输入用户名或昵称")
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
        hint = CaptionLabel("不勾选“记住我”时，下次启动默认进入演示预览模式。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return form

    def _build_register_form(self) -> QWidget:
        form = QWidget()
        layout = QVBoxLayout(form)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        layout.addWidget(CaptionLabel("用户名（3-32 位字母、数字、_ . -）"))
        self.register_username_edit = LineEdit()
        self.register_username_edit.setPlaceholderText("例如 alice")
        layout.addWidget(self.register_username_edit)
        layout.addWidget(CaptionLabel("显示名称（可选）"))
        self.register_display_edit = LineEdit()
        self.register_display_edit.setPlaceholderText("例如 张三")
        layout.addWidget(self.register_display_edit)
        layout.addWidget(CaptionLabel("密码（至少 6 位）"))
        self.register_password_edit = PasswordLineEdit()
        self.register_password_edit.setPlaceholderText("请输入密码")
        layout.addWidget(self.register_password_edit)
        layout.addWidget(CaptionLabel("确认密码"))
        self.register_confirm_edit = PasswordLineEdit()
        self.register_confirm_edit.setPlaceholderText("请再次输入密码")
        self.register_confirm_edit.returnPressed.connect(self._register)
        layout.addWidget(self.register_confirm_edit)

        self.register_status = CaptionLabel("")
        self.register_status.setWordWrap(True)
        layout.addWidget(self.register_status)
        self.register_button = make_button(
            "提交注册", primary=True, icon=FIF.ADD, on_click=self._register
        )
        layout.addWidget(self.register_button)
        hint = CaptionLabel("默认自动审核，通过后立即登录；若服务端开启人工审核，需等待管理员通过。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return form

    def _on_auth_mode_changed(self, route_key: str) -> None:
        registering = route_key == "register"
        self.auth_stack.setCurrentWidget(
            self.register_form if registering else self.login_form
        )
        self.auth_title.setText("注册 NexHub" if registering else "登录 NexHub")
        if registering:
            self.register_username_edit.setFocus()
        else:
            self.username_edit.setFocus()

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
        self.nickname_button = make_button(
            "修改昵称", icon=FIF.EDIT, on_click=lambda: self.nickname_requested.emit()
        )
        layout.addWidget(self.nickname_button)
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
            self.nickname_button.setVisible(self.ctx.can_update_nickname)
        else:
            self.nickname_button.setVisible(False)
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
            if self.auth_segment.currentRouteKey() == "register":
                self.register_username_edit.setFocus()
            else:
                self.username_edit.setFocus()

    def close_panel(self) -> None:
        self.hide()

    # ------------------------------------------------------------------
    def _server_url_or_error(self) -> tuple[str, str]:
        host = self.server_host_edit.text().strip()
        if not host:
            return "", "请填写服务器 IP 或域名"
        port = self._server_port()
        if port is None:
            return "", "请填写有效的端口号（1-65535）"
        scheme = self.protocol_segment.currentRouteKey() or "http"
        server_url = build_server_url(scheme, host, port)
        if not server_url:
            return "", "服务器地址无效"
        return server_url, ""

    def _remember_server_url(self, server_url: str) -> None:
        self.ctx.config.server_url = server_url
        self.ctx.config.save()

    def _login(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            self.login_status.setText("请填写账号/昵称与密码")
            return
        server_url, error = self._server_url_or_error()
        if error:
            self.login_status.setText(error)
            return
        self._remember_server_url(server_url)
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
        self.username_edit.setText(self.ctx.config.username)
        self.login_status.setText("登录成功")
        self._sync_state()
        self.close_panel()
        self.ctx.navigate("dashboard")
        compatibility = self.ctx.server_compat
        if compatibility.error:
            self.ctx.notify(compatibility.error, "error")
        elif compatibility.warning:
            self.ctx.notify(compatibility.warning, "warning")
        else:
            self.ctx.notify("登录成功", "success")

    def _on_login_failed(self, message: str) -> None:
        self.login_button.setEnabled(True)
        self.login_button.setText("登录")
        self.login_status.setText(f"登录失败：{message}")

    def _register(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        username = self.register_username_edit.text().strip()
        display_name = self.register_display_edit.text().strip()
        password = self.register_password_edit.text()
        confirm = self.register_confirm_edit.text()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
            self.register_status.setText("用户名需为 3-32 位字母、数字或 _ . -")
            return
        if len(display_name) > 32:
            self.register_status.setText("显示名称不能超过 32 个字符")
            return
        if len(password) < 6:
            self.register_status.setText("密码至少需要 6 位")
            return
        if password != confirm:
            self.register_status.setText("两次输入的密码不一致")
            return
        server_url, error = self._server_url_or_error()
        if error:
            self.register_status.setText(error)
            return
        self._remember_server_url(server_url)
        self.register_button.setEnabled(False)
        self.register_button.setText("正在提交...")
        self.register_status.setText("正在提交注册信息...")
        from ..workers import FunctionWorker

        self._worker = FunctionWorker(
            self.ctx.register, username, password, display_name, parent=self
        )
        self._worker.succeeded.connect(self._on_register_success)
        self._worker.failed.connect(self._on_register_failed)
        self._worker.start()

    def _on_register_success(self, result: dict) -> None:
        self.register_button.setEnabled(True)
        self.register_button.setText("提交注册")
        result = result if isinstance(result, dict) else {}
        message = str(result.get("message") or "注册成功")
        self.register_password_edit.clear()
        self.register_confirm_edit.clear()
        if result.get("token"):
            self.register_status.setText(message)
            self._sync_state()
            self.close_panel()
            self.ctx.navigate("dashboard")
            compatibility = self.ctx.server_compat
            if compatibility.warning:
                self.ctx.notify(compatibility.warning, "warning")
            else:
                self.ctx.notify(message, "success")
            return
        user = result.get("user", {})
        username = str(user.get("username") or self.register_username_edit.text()).strip()
        self.register_status.setText(message)
        self.username_edit.setText(username)
        self.auth_segment.setCurrentItem("login")
        if self.auth_stack.currentWidget() is not self.login_form:
            self._on_auth_mode_changed("login")
        self.login_status.setText(message)
        self.password_edit.setFocus()

    def _on_register_failed(self, message: str) -> None:
        self.register_button.setEnabled(True)
        self.register_button.setText("提交注册")
        self.register_status.setText(f"注册失败：{message}")

    def _logout(self) -> None:
        from ..widgets.common import ask_confirm

        if not ask_confirm(self, "退出登录", "退出后将立即回到演示预览模式，确定退出吗？"):
            return
        self.ctx.logout()
        self.username_edit.clear()
        self.password_edit.clear()
        self.register_password_edit.clear()
        self.register_confirm_edit.clear()
        self.register_status.clear()
        self.auth_segment.setCurrentItem("login")
        self.login_status.setText("已退出登录，当前为演示预览模式")
        self._sync_state()

