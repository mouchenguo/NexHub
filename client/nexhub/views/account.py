"""账号页：Fluent 个人资料、容量统计与下载记录。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    AvatarWidget,
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
    ProgressBar,
    StrongBodyLabel,
    TableWidget,
    TitleLabel,
)

from ..file_utils import format_time, human_size
from ..widgets.common import (
    StatCard,
    ask_confirm,
    make_button,
    make_card,
    section_header,
    status_pill,
)
from ..workers import FunctionWorker
from .base import BasePage


class ChangeNicknameDialog(QDialog):
    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker: FunctionWorker | None = None
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · 修改昵称")
        self.setFixedWidth(430)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("修改昵称"))
        layout.addWidget(CaptionLabel("昵称仅用于界面展示，登录用户名不会改变。"))
        layout.addSpacing(6)

        layout.addWidget(BodyLabel("新昵称（1-32 个字符）"))
        self.nickname = LineEdit()
        self.nickname.setPlaceholderText("请输入昵称")
        self.nickname.setMaxLength(32)
        current = self.ctx.config.display_name or self.ctx.config.username
        self.nickname.setText(current)
        self.nickname.selectAll()
        self.nickname.returnPressed.connect(self._submit)
        layout.addWidget(self.nickname)
        layout.addSpacing(4)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = make_button("取消", on_click=self.reject)
        self.submit = make_button("保存修改", primary=True, on_click=self._submit)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.submit)
        layout.addLayout(footer)
        if not self.ctx.online:
            self.submit.setEnabled(False)
            layout.addWidget(CaptionLabel("离线体验模式不支持修改昵称"))
        elif not self.ctx.can_update_nickname:
            self.submit.setEnabled(False)
            layout.addWidget(CaptionLabel("当前服务端不支持修改昵称，请升级服务端"))
        self.nickname.setFocus()

    def _submit(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        nickname = self.nickname.text().strip()
        current = (self.ctx.config.display_name or self.ctx.config.username).strip()
        if not nickname:
            self._error("昵称不能为空")
            return
        if len(nickname) > 32:
            self._error("昵称不能超过 32 个字符")
            return
        if any(ord(char) < 32 or ord(char) == 127 for char in nickname):
            self._error("昵称不能包含换行或控制字符")
            return
        if nickname == current:
            self.ctx.notify("昵称未变化", "info")
            self.accept()
            return
        self.submit.setEnabled(False)
        self.submit.setText("正在保存...")
        self._worker = FunctionWorker(self.ctx.update_nickname, nickname, parent=self)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _error(self, message: str) -> None:
        InfoBar.error("无法修改", message, parent=self, position=InfoBarPosition.TOP)

    def _on_success(self, _result) -> None:
        self.ctx.notify("昵称已更新", "success")
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.submit.setEnabled(True)
        self.submit.setText("保存修改")
        self._error(message)


class ChangePasswordDialog(QDialog):
    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._worker: FunctionWorker | None = None
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · 修改密码")
        self.setFixedWidth(430)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("修改密码"))
        layout.addWidget(CaptionLabel("修改后其他设备上的登录状态不会自动失效。"))
        layout.addSpacing(6)

        layout.addWidget(BodyLabel("当前密码"))
        self.old_password = PasswordLineEdit()
        layout.addWidget(self.old_password)
        layout.addWidget(BodyLabel("新密码（至少 6 位）"))
        self.new_password = PasswordLineEdit()
        layout.addWidget(self.new_password)
        layout.addWidget(BodyLabel("确认新密码"))
        self.confirm_password = PasswordLineEdit()
        layout.addWidget(self.confirm_password)
        layout.addSpacing(4)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = make_button("取消", on_click=self.reject)
        self.submit = make_button("保存修改", primary=True, on_click=self._submit)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.submit)
        layout.addLayout(footer)
        if not self.ctx.online:
            self.submit.setEnabled(False)
            layout.addWidget(CaptionLabel("离线体验模式不支持修改密码"))

    def _submit(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        old = self.old_password.text()
        new = self.new_password.text()
        confirm = self.confirm_password.text()
        if not old or not new:
            self._error("请填写当前密码与新密码")
            return
        if len(new) < 6:
            self._error("新密码至少需要 6 位")
            return
        if new != confirm:
            self._error("两次输入的新密码不一致")
            return
        self.submit.setEnabled(False)
        self.submit.setText("正在保存...")
        self._worker = FunctionWorker(self.ctx.api.change_password, old, new, parent=self)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _error(self, message: str) -> None:
        InfoBar.error("无法修改", message, parent=self, position=InfoBarPosition.TOP)

    def _on_success(self, result) -> None:
        self.ctx.notify("密码已更新", "success")
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.submit.setEnabled(True)
        self.submit.setText("保存修改")
        self._error(message)


class AccountPage(BasePage):
    page_id = "account"
    title = "账号"
    subtitle = "个人信息、容量统计与下载记录"

    def __init__(self, ctx, parent=None):
        self._worker: FunctionWorker | None = None
        super().__init__(ctx, parent)

    def build(self) -> None:
        self._build_profile()
        self._build_stats()

        middle = QHBoxLayout()
        middle.setSpacing(18)
        middle.addWidget(self._build_quota_card(), 3)
        middle.addWidget(self._build_security_card(), 2)
        self.content_layout.addLayout(middle)
        self._build_records()
        self.content_layout.addStretch(1)

    def _build_profile(self) -> None:
        card, layout = make_card(20, 10)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.avatar = AvatarWidget()
        self.avatar.setRadius(28)
        row.addWidget(self.avatar)

        info = QVBoxLayout()
        info.setSpacing(3)
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        self.name_label = TitleLabel("--")
        name_row.addWidget(self.name_label)
        self.role_badge = status_pill("普通用户", FIF.PEOPLE)
        name_row.addWidget(self.role_badge)
        self.mode_badge = status_pill("离线体验", FIF.INFO)
        name_row.addWidget(self.mode_badge)
        name_row.addStretch(1)
        info.addLayout(name_row)
        self.username_label = CaptionLabel("@ --")
        info.addWidget(self.username_label)
        self.created_label = CaptionLabel("加入时间 --")
        info.addWidget(self.created_label)
        row.addLayout(info, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.nickname_button = make_button(
            "修改昵称", icon=FIF.EDIT, on_click=self._change_nickname
        )
        actions.addWidget(self.nickname_button)
        actions.addWidget(
            make_button("修改密码", icon=FIF.FINGERPRINT, on_click=self._change_password)
        )
        actions.addWidget(
            make_button("退出登录", icon=FIF.CANCEL, on_click=self._logout)
        )
        row.addLayout(actions)
        layout.addLayout(row)
        self.content_layout.addWidget(card)

        self.offline_hint = CaptionLabel(
            "当前离线体验：账号与容量信息来自本地演示数据，不会上传服务器。"
        )
        self.offline_hint.setVisible(not self.ctx.online)
        self.content_layout.addWidget(self.offline_hint)

    def _build_stats(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(16)
        self.oss_stat = StatCard("OSS 已用容量", "--", "正在加载...", FIF.CLOUD)
        self.share_stat = StatCard("活跃共享", "--", "正在加载...", FIF.SHARE)
        self.download_stat = StatCard("累计下载", "--", "正在加载...", FIF.DOWN)
        row.addWidget(self.oss_stat, 1)
        row.addWidget(self.share_stat, 1)
        row.addWidget(self.download_stat, 1)
        self.content_layout.addLayout(row)

    def _build_quota_card(self) -> QWidget:
        card, layout = make_card()
        header, actions = section_header("OSS 容量占用", "由 ECS 后端统一统计")
        actions.addWidget(status_pill("密钥不落地客户端", FIF.ACCEPT))
        layout.addWidget(header)

        usage = QHBoxLayout()
        box = QVBoxLayout()
        box.setSpacing(2)
        box.addWidget(CaptionLabel("当前已用"))
        self.quota_value = StrongBodyLabel("--")
        box.addWidget(self.quota_value)
        usage.addLayout(box)
        usage.addStretch(1)
        self.quota_percent = CaptionLabel("--")
        usage.addWidget(self.quota_percent)
        layout.addLayout(usage)

        self.quota_progress = ProgressBar()
        self.quota_progress.setRange(0, 100)
        layout.addWidget(self.quota_progress)
        self.quota_hint = CaptionLabel("正在读取容量...")
        self.quota_hint.setWordWrap(True)
        layout.addWidget(self.quota_hint)
        return card

    def _build_security_card(self) -> QWidget:
        card, layout = make_card()
        header, actions = section_header("登录与安全", "账号状态")
        layout.addWidget(header)
        self.session_label = StrongBodyLabel("会话 --")
        layout.addWidget(self.session_label)
        self.login_method = CaptionLabel("--")
        self.login_method.setWordWrap(True)
        layout.addWidget(self.login_method)
        layout.addStretch(1)
        layout.addWidget(CaptionLabel("管理员账号可在浏览器登录 ECS 管理后台。"))
        return card

    def _build_records(self) -> None:
        card, layout = make_card()
        header, actions = section_header("下载记录", "最近的文件下载与来源共享")
        actions.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        layout.addWidget(header)

        self.records_table = TableWidget()
        self.records_table.setBorderVisible(False)
        self.records_table.setBorderRadius(10)
        self.records_table.setColumnCount(5)
        self.records_table.setHorizontalHeaderLabels(["文件", "来源共享", "大小", "时间", "状态"])
        self.records_table.verticalHeader().setVisible(False)
        self.records_table.setShowGrid(False)
        self.records_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.records_table.setSelectionMode(TableWidget.SelectionMode.NoSelection)
        self.records_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.records_table.setMinimumHeight(240)
        header_view = self.records_table.horizontalHeader()
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for index in range(1, 5):
            header_view.setSectionResizeMode(index, QHeaderView.ResizeMode.ResizeToContents)
        self.records_table.verticalHeader().setDefaultSectionSize(38)
        layout.addWidget(self.records_table)

        self.records_empty = CaptionLabel("暂无下载记录")
        self.records_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.records_empty.hide()
        layout.addWidget(self.records_empty)
        self.content_layout.addWidget(card)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self._fetch, parent=self)
        self._worker.succeeded.connect(self._apply)
        self._worker.failed.connect(
            lambda message: self.ctx.notify(f"账号信息加载失败：{message}", "error")
        )
        self._worker.start()

    def _fetch(self) -> dict:
        if self.ctx.online:
            data = self.ctx.api.me()
            user = data.get("user", data)
            dashboard = self.ctx.api.dashboard()
            records = self.ctx.api.download_records()
            shares = self.ctx.api.list_shares("mine")
        else:
            user = self.ctx.demo.current_user()
            dashboard = self.ctx.demo.dashboard()
            records = self.ctx.demo.download_records()
            shares = self.ctx.demo.list_shares("mine")
        return {
            "user": user,
            "dashboard": dashboard,
            "records": records,
            "shares": shares,
        }

    def _apply(self, payload: dict) -> None:
        user = payload["user"]
        dashboard = payload["dashboard"]
        records = payload["records"]
        shares = payload["shares"]
        active = [item for item in shares if item.get("status") == "active"]

        display_name = user.get("display_name") or user.get("username") or "用户"
        self.name_label.setText(display_name)
        self.username_label.setText(f"@ {user.get('username', '--')}")
        self.avatar.setText(display_name[:1].upper())
        is_admin = user.get("role") == "admin"
        self.role_badge.setText("管理员" if is_admin else "普通用户")
        self.role_badge.setIcon(FIF.PEOPLE)
        self.mode_badge.setText("离线体验" if not self.ctx.online else "ECS 在线")
        self.mode_badge.setIcon(FIF.INFO if not self.ctx.online else FIF.CONNECT)
        self.nickname_button.setVisible(self.ctx.can_update_nickname)
        self.created_label.setText(f"加入时间 {format_time(user.get('created_at'))}")
        self.offline_hint.setVisible(not self.ctx.online)

        oss_used = int(user.get("oss_used") or dashboard.get("oss_used") or 0)
        quota = int(user.get("oss_quota") or dashboard.get("oss_quota") or 0)
        percent = int(oss_used / quota * 100) if quota else 0
        self.oss_stat.set_value(human_size(oss_used), f"配额 {human_size(quota)}")
        self.share_stat.set_value(str(len(active)), f"共发布 {len(shares)} 个共享")
        self.download_stat.set_value(
            str(dashboard.get("total_downloads", len(records))), "含他人下载次数"
        )
        self.quota_value.setText(human_size(oss_used))
        self.quota_percent.setText(f"{percent}% / {human_size(quota)}")
        self.quota_progress.setValue(min(100, percent))
        self.quota_hint.setText(
            "容量接近上限时，后端会自动清理最早创建的 OSS 共享。"
            if percent >= 80
            else "OSS 下载会产生外网流量费用，请按需保留长期共享。"
        )
        self.session_label.setText(
            "离线本地会话" if not self.ctx.online else f"在线会话 · {self.ctx.config.server_url}"
        )
        self.login_method.setText(
            f"账号 {user.get('username', '--')} · "
            f"{'管理员' if is_admin else '普通用户'} · "
            f"{'演示数据' if not self.ctx.online else 'Token 鉴权'}"
        )
        self._fill_records(records)

    def _fill_records(self, records: list[dict]) -> None:
        self.records_table.setRowCount(0)
        if not records:
            self.records_table.hide()
            self.records_empty.show()
            return
        self.records_table.show()
        self.records_empty.hide()
        for record in records[:30]:
            row = self.records_table.rowCount()
            self.records_table.insertRow(row)
            values = [
                record.get("file_name", "--"),
                record.get("share_name", "--"),
                human_size(record.get("size", 0)),
                format_time(record.get("created_at")),
                "成功" if record.get("status", "success") == "success" else record.get("status"),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                item.setToolTip(str(value))
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.records_table.setItem(row, column, item)

    # ------------------------------------------------------------------
    def _change_nickname(self) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验模式不支持修改昵称", "warning")
            return
        if not self.ctx.can_update_nickname:
            self.ctx.notify("当前服务端不支持修改昵称，请升级服务端", "warning")
            return
        ChangeNicknameDialog(self.ctx, self).exec()

    def _change_password(self) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验模式不支持修改密码", "warning")
            return
        ChangePasswordDialog(self.ctx, self).exec()

    def _logout(self) -> None:
        if ask_confirm(self, "退出登录", "确定退出当前账号？本地设置与主题会被保留。"):
            self.ctx.logout()
