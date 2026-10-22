"""RDP 主机页面：本地加密保存并通过系统 mstsc.exe 连接。"""
from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon as FIF,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    SearchLineEdit,
    SpinBox,
    StrongBodyLabel,
    TitleLabel,
)

from ..file_utils import relative_time
from ..services.rdp_service import RdpError, RdpHost
from ..workers import FunctionWorker
from ..widgets.common import (
    EmptyState,
    ask_confirm,
    clear_layout,
    form_field,
    icon_widget,
    make_button,
    make_card,
    status_pill,
)
from .base import BasePage


class RdpHostDialog(QDialog):
    def __init__(
        self,
        ctx,
        parent: QWidget | None = None,
        host: RdpHost | None = None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.host = host
        self.saved_host: RdpHost | None = None
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · RDP 主机")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(560)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(TitleLabel("编辑 RDP 主机" if self.host else "新建 RDP 主机"))
        hint = BodyLabel(
            "使用 Windows 自带 mstsc.exe 连接，密码只在本机通过 AES-GCM 加密保存。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addSpacing(4)

        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("例如：办公室电脑")
        layout.addLayout(form_field("主机名称", self.name_edit))

        address_row = QHBoxLayout()
        address_row.setSpacing(12)
        self.address_edit = LineEdit()
        self.address_edit.setPlaceholderText("192.168.1.10 或 rdp.example.com")
        address_row.addLayout(form_field("地址（IP / 域名）", self.address_edit), 2)
        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(3389)
        address_row.addLayout(form_field("端口", self.port_spin), 1)
        layout.addLayout(address_row)

        user_row = QHBoxLayout()
        user_row.setSpacing(12)
        self.username_edit = LineEdit()
        self.username_edit.setPlaceholderText("Windows 登录用户名")
        user_row.addLayout(form_field("用户名", self.username_edit), 1)
        self.password_edit = LineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText(
            "留空保持原密码" if self.host else "可选；连接时由系统 RDP 弹窗输入"
        )
        user_row.addLayout(form_field("密码（本地加密）", self.password_edit), 1)
        layout.addLayout(user_row)

        self.notes_edit = LineEdit()
        self.notes_edit.setPlaceholderText("可选，例如用途、机房位置")
        layout.addLayout(form_field("备注", self.notes_edit))

        self.nps_check = CheckBox("使用 NPS 隧道连接")
        layout.addWidget(self.nps_check)
        self.nps_target_edit = LineEdit()
        self.nps_target_edit.setReadOnly(True)
        self.nps_target_edit.setPlaceholderText("尚未在 NPC 配置中填写 RDP/NPS 隧道地址")
        layout.addLayout(
            form_field(
                "NPS 隧道地址（自动读取）",
                self.nps_target_edit,
                "在「设置 → NPC 管理」填写隧道映射后的公网地址，例如 sh.example.com:3389",
            )
        )
        self.nps_check.toggled.connect(self._sync_nps)
        self._sync_nps(False)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(
            make_button("保存", primary=True, icon=FIF.SAVE, on_click=self._save)
        )
        layout.addLayout(footer)

        if self.host:
            self.name_edit.setText(self.host.name)
            self.address_edit.setText(self.host.address)
            self.port_spin.setValue(self.host.port)
            self.username_edit.setText(self.host.username)
            self.notes_edit.setText(self.host.notes)
            self.nps_check.setChecked(self.host.use_nps)

    def _sync_nps(self, checked: bool) -> None:
        target = self.ctx.rdp.nps_tunnel_target()
        self.nps_target_edit.setText(target)
        if checked and not target:
            self.nps_target_edit.setPlaceholderText(
                "未配置，请先到「设置 → NPC 管理」填写 RDP/NPS 隧道地址"
            )

    def _save(self) -> None:
        try:
            self.saved_host = self.ctx.rdp.save_host(
                host_id=self.host.id if self.host else None,
                name=self.name_edit.text(),
                address=self.address_edit.text(),
                port=self.port_spin.value(),
                username=self.username_edit.text(),
                password=self.password_edit.text(),
                notes=self.notes_edit.text(),
                use_nps=self.nps_check.isChecked(),
            )
        except RdpError as exc:
            InfoBar.error(
                "保存失败",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
            )
            return
        self.accept()


class RdpPage(BasePage):
    page_id = "rdp"
    title = "RDP 主机"
    subtitle = "保存 Windows 远程桌面连接，并通过系统 mstsc.exe 发起会话"

    def __init__(self, ctx, parent=None):
        self._hosts: list[RdpHost] = []
        self._online_devices: list[dict] = []
        self._online_worker: FunctionWorker | None = None
        super().__init__(ctx, parent)
        self._online_timer = QTimer(self)
        self._online_timer.setInterval(20_000)
        self._online_timer.timeout.connect(self._load_online)

    def build(self) -> None:
        toolbar, toolbar_layout = make_card(14, 8)
        row = QHBoxLayout()
        row.setSpacing(9)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索主机名称、地址或备注...")
        self.search.setFixedWidth(330)
        self.search.textChanged.connect(self._render)
        row.addWidget(self.search)
        row.addStretch(1)
        row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        row.addWidget(
            make_button("新建 RDP 主机", primary=True, icon=FIF.ADD, on_click=self._add)
        )
        row.addWidget(
            make_button("导出", transparent=True, icon=FIF.SAVE_AS, on_click=self._export)
        )
        row.addWidget(
            make_button("导入", transparent=True, icon=FIF.FOLDER_ADD, on_click=self._import)
        )
        toolbar_layout.addLayout(row)
        self.content_layout.addWidget(toolbar)
        self.content_layout.addWidget(StrongBodyLabel("在线设备 · 同账号"))
        self.online_summary = CaptionLabel("正在读取同账号在线设备...")
        self.content_layout.addWidget(self.online_summary)
        self.online_host = QWidget()
        self.online_layout = QVBoxLayout(self.online_host)
        self.online_layout.setContentsMargins(0, 0, 0, 0)
        self.online_layout.setSpacing(10)
        self.content_layout.addWidget(self.online_host)
        self.online_empty = EmptyState(
            "当前账号没有在线设备",
            "A 电脑登录同一账号并保持 NexHub 在线后，会自动出现在这里。",
            FIF.CONNECT,
        )
        self.online_empty.hide()
        self.content_layout.addWidget(self.online_empty)

        self.content_layout.addWidget(StrongBodyLabel("静态主机 · 本地保存"))
        self.summary = CaptionLabel("正在读取静态 RDP 主机...")
        self.content_layout.addWidget(self.summary)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.content_layout.addWidget(self.list_host)
        self.empty = EmptyState(
            "还没有静态 RDP 主机",
            "保存常用 Windows 远程桌面地址，之后一键通过 mstsc 连接。",
            FIF.PROJECTOR,
            make_button("新建 RDP 主机", primary=True, icon=FIF.ADD, on_click=self._add),
        )
        self.empty.hide()
        self.content_layout.addWidget(self.empty)
        self.content_layout.addStretch(1)

    # ------------------------------------------------------------------
    def on_show(self) -> None:
        self._online_timer.start()
        super().on_show()

    def hideEvent(self, event) -> None:
        self._online_timer.stop()
        super().hideEvent(event)

    def refresh(self) -> None:
        self._hosts = self.ctx.rdp.list_hosts()
        self._render()
        self._load_online()

    def _load_online(self) -> None:
        if self._online_worker and self._online_worker.isRunning():
            return
        if not self.ctx.online:
            self._online_devices = []
            self._render_online()
            return
        self._online_worker = FunctionWorker(self.ctx.devices.list_online, parent=self)
        self._online_worker.succeeded.connect(self._on_online_loaded)
        self._online_worker.failed.connect(self._on_online_failed)
        self._online_worker.start()

    def _on_online_loaded(self, devices: list[dict]) -> None:
        self._online_devices = devices
        self._render_online()

    def _on_online_failed(self, message: str) -> None:
        self.online_summary.setText(f"在线设备读取失败：{message}")

    def _render_online(self) -> None:
        clear_layout(self.online_layout)
        if not self.ctx.online:
            self.online_summary.setText("离线体验模式没有同账号在线设备")
            self.online_empty.hide()
            return
        self.online_summary.setText(
            f"共 {len(self._online_devices)} 台在线设备 · 每 20 秒自动刷新"
        )
        if not self._online_devices:
            self.online_empty.show()
            return
        self.online_empty.hide()
        for device in self._online_devices:
            self.online_layout.addWidget(self._online_row(device))

    def _online_row(self, device: dict) -> QWidget:
        card, layout = make_card(18, 8)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon_widget(FIF.PROJECTOR, 28))
        info = QVBoxLayout()
        info.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title_row.addWidget(StrongBodyLabel(str(device.get("name") or "在线设备")))
        title_row.addWidget(status_pill("NexHub 在线设备", FIF.ACCEPT))
        title_row.addStretch(1)
        info.addLayout(title_row)
        target = str(device.get("tunnel_address") or "未配置 NPS 隧道地址")
        target_label = BodyLabel(f"RDP 隧道：{target}")
        target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(target_label)
        info.addWidget(
            CaptionLabel(f"最后心跳 {relative_time(device.get('last_seen'))}")
        )
        row.addLayout(info, 1)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(
            make_button(
                "连接",
                primary=True,
                icon=FIF.CONNECT,
                on_click=lambda: self._connect_device(device),
            )
        )
        row.addLayout(actions)
        layout.addLayout(row)
        return card

    def _connect_device(self, device: dict) -> None:
        try:
            rdp_file = self.ctx.rdp.connect_device(device)
        except RdpError as exc:
            InfoBar.error(
                "无法连接",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4500,
            )
            return
        self.ctx.notify(
            f"已启动 RDP：{device.get('name', '在线设备')}（临时配置 {rdp_file.name}）",
            "success",
        )

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "导出静态 RDP 主机",
            "nexhub-rdp-hosts.json",
            "JSON 文件 (*.json)",
        )
        if not path:
            return
        try:
            count = self.ctx.rdp.export_hosts(path)
        except OSError as exc:
            InfoBar.error(
                "导出失败",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        InfoBar.success(
            "导出完成",
            f"已导出 {count} 条静态主机（密码不导出）",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入静态 RDP 主机",
            "",
            "JSON 文件 (*.json)",
        )
        if not path:
            return
        try:
            count = self.ctx.rdp.import_hosts(path)
        except RdpError as exc:
            InfoBar.error(
                "导入失败",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        self.refresh()
        InfoBar.success(
            "导入完成",
            f"新增 {count} 条静态主机（密码需重新填写）",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _render(self) -> None:
        clear_layout(self.list_layout)
        query = self.search.text().strip().lower()
        filtered = [
            host
            for host in self._hosts
            if not query
            or query in host.name.lower()
            or query in host.address.lower()
            or query in host.notes.lower()
        ]
        self.summary.setText(f"共 {len(self._hosts)} 台 RDP 主机 · 显示 {len(filtered)} 台")
        if not filtered:
            self.empty.setVisible(not self._hosts)
            if self._hosts:
                self.empty.hide()
                self.list_layout.addWidget(CaptionLabel("没有符合条件的主机"))
            return
        self.empty.hide()
        for host in filtered:
            self.list_layout.addWidget(self._row(host))

    def _row(self, host: RdpHost) -> QWidget:
        card, layout = make_card(18, 8)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(icon_widget(FIF.PROJECTOR, 28))
        info = QVBoxLayout()
        info.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title_row.addWidget(StrongBodyLabel(host.name))
        title_row.addWidget(status_pill("静态主机", FIF.PROJECTOR))
        if host.use_nps:
            title_row.addWidget(status_pill("NPS 隧道", FIF.VPN))
        title_row.addStretch(1)
        info.addLayout(title_row)
        target = (
            self.ctx.rdp.nps_tunnel_target()
            if host.use_nps
            else f"{host.address}:{host.port}"
        )
        target_label = BodyLabel(f"连接地址：{target or '未配置'}")
        target_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        info.addWidget(target_label)
        detail = f"用户名：{host.username or 'Windows 登录用户'}"
        if host.notes:
            detail += f"  ·  {host.notes}"
        info.addWidget(CaptionLabel(detail))
        row.addLayout(info, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(
            make_button("连接", primary=True, icon=FIF.CONNECT, on_click=lambda: self._connect(host))
        )
        actions.addWidget(
            make_button("编辑", icon=FIF.EDIT, on_click=lambda: self._edit(host))
        )
        actions.addWidget(
            make_button("删除", icon=FIF.DELETE, on_click=lambda: self._delete(host))
        )
        row.addLayout(actions)
        layout.addLayout(row)
        return card

    # ------------------------------------------------------------------
    def _add(self) -> None:
        dialog = RdpHostDialog(self.ctx, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_host:
            self.refresh()
            self.ctx.notify("RDP 主机已保存", "success")

    def _edit(self, host: RdpHost) -> None:
        dialog = RdpHostDialog(self.ctx, self, host)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved_host:
            self.refresh()
            self.ctx.notify("RDP 主机已更新", "success")

    def _delete(self, host: RdpHost) -> None:
        if not ask_confirm(self, "删除 RDP 主机", f"确定删除「{host.name}」吗？"):
            return
        try:
            self.ctx.rdp.delete_host(host.id)
        except RdpError as exc:
            InfoBar.error(
                "删除失败",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
            return
        self.refresh()
        self.ctx.notify("RDP 主机已删除", "success")

    def _connect(self, host: RdpHost) -> None:
        try:
            rdp_file = self.ctx.rdp.connect(host)
        except RdpError as exc:
            InfoBar.error(
                "无法连接",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4500,
            )
            return
        self.ctx.notify(f"已启动 RDP：{host.name}（临时配置 {rdp_file.name}）", "success")
