"""设置中的 NPC / nps 子模块。"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PasswordLineEdit,
)

from ..config import normalize_server_url
from ..services.npc_service import NpcManager
from ..services.nps_service import check_nps
from ..widgets.common import (
    Banner,
    ask_confirm,
    form_field,
    icon_button,
    make_button,
    status_pill,
)
from ..workers import FunctionWorker


class NpcSettingsPanel(QWidget):
    """NPC 安装、npc.conf 两字段、nps 域名与重启服务。"""

    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.manager = NpcManager()
        self._install_worker: FunctionWorker | None = None
        self._save_worker: FunctionWorker | None = None
        self._restart_worker: FunctionWorker | None = None
        self._nps_worker: FunctionWorker | None = None
        self._auto_tunnel_worker: FunctionWorker | None = None
        self._startup_checked = False
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        status_row = QHBoxLayout()
        status_row.setSpacing(7)
        status_row.addWidget(CaptionLabel("NPC 状态"))
        self.install_badge = status_pill("检测中...", FIF.INFO)
        self.service_badge = status_pill("服务检测中", FIF.INFO)
        self.run_badge = status_pill("状态未知", FIF.INFO)
        self.bundle_badge = status_pill("内置资源", FIF.INFO)
        self.admin_badge = status_pill("权限检测中", FIF.INFO)
        status_row.addWidget(self.install_badge)
        status_row.addWidget(self.service_badge)
        status_row.addWidget(self.run_badge)
        status_row.addWidget(self.bundle_badge)
        status_row.addWidget(self.admin_badge)
        status_row.addStretch(1)
        status_row.addWidget(
            icon_button(FIF.SYNC, tooltip="重新检测 NPC 状态", on_click=self.refresh)
        )
        layout.addLayout(status_row)

        self.install_path = CaptionLabel("--")
        self.install_path.setWordWrap(True)
        self.install_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.install_path)

        self.config_path_label = CaptionLabel("--")
        self.config_path_label.setWordWrap(True)
        self.config_path_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.config_path_label)

        self.install_button = make_button(
            "安装 NPC",
            primary=True,
            icon=FIF.DOWNLOAD,
            on_click=self._install_clicked,
        )
        self.lock_banner = Banner("", FIF.INFO, self.install_button)
        layout.addWidget(self.lock_banner)

        self.config_box = QWidget()
        config_layout = QVBoxLayout(self.config_box)
        config_layout.setContentsMargins(0, 0, 0, 0)
        config_layout.setSpacing(8)

        self.server_edit = LineEdit()
        self.server_edit.setPlaceholderText("x.x.x.x:8024")
        config_layout.addLayout(
            form_field("主机 IP（含端口）", self.server_edit, "例如 1.2.3.4:8024")
        )

        self.vkey_edit = PasswordLineEdit()
        self.vkey_edit.setPlaceholderText("请输入 nps 客户端 vkey")
        config_layout.addLayout(
            form_field("vkey", self.vkey_edit, "仅写入本机 npc.conf，不会上传服务器")
        )

        domains = QHBoxLayout()
        domains.setSpacing(12)
        self.web_domain_edit = LineEdit()
        self.web_domain_edit.setPlaceholderText("https://share.example.com")
        domains.addLayout(
            form_field(
                "临时共享域名",
                self.web_domain_edit,
                "旧版兼容；默认由 ECS 自动分配公网端口",
            ),
            1,
        )
        self.host_domain_edit = LineEdit()
        self.host_domain_edit.setPlaceholderText("sh.example.com:3389")
        domains.addLayout(
            form_field(
                "RDP / NPS 隧道地址",
                self.host_domain_edit,
                "自动读取；启用 NPS 隧道的 RDP 主机会使用此地址",
            ),
            1,
        )
        config_layout.addLayout(domains)

        buttons = QHBoxLayout()
        buttons.setSpacing(7)
        self.save_button = make_button(
            "保存配置", primary=True, icon=FIF.SAVE, on_click=self._save_clicked
        )
        self.restart_button = make_button(
            "重启 NPC", icon=FIF.SYNC, on_click=self._restart_clicked
        )
        self.test_nps_button = make_button(
            "测试 nps 端口", icon=FIF.CONNECT, on_click=self._test_nps_clicked
        )
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.restart_button)
        buttons.addWidget(self.test_nps_button)
        buttons.addStretch(1)
        config_layout.addLayout(buttons)

        self.nps_status = CaptionLabel("")
        self.nps_status.setWordWrap(True)
        config_layout.addWidget(self.nps_status)
        config_layout.addWidget(
            CaptionLabel("安装、保存和重启需要管理员权限，Windows 会弹出 UAC。")
        )
        layout.addWidget(self.config_box)

    # ------------------------------------------------------------------
    def startup_check(self) -> None:
        if self._startup_checked:
            return
        self._startup_checked = True
        self.refresh()
        status = self.manager.status()
        if status.get("installed"):
            if not status.get("service_installed") and status.get("bundle_available"):
                self._install(repair=False)
            self._ensure_nps_host()
            return
        if os.environ.get("NEXHUB_NPC_AUTO_INSTALL", "1") == "0":
            return
        if not status.get("bundle_available"):
            return
        if status.get("install_dir_exists"):
            if ask_confirm(
                self.window(),
                "修复 NPC 安装",
                "检测到 C:\\Program Files\\npc 目录存在，但核心文件 npc.exe 缺失。\n"
                "是否重新解压内置 NPC 资源覆盖修复？",
            ):
                self._install(repair=True)
        else:
            self._install(repair=False)

    def refresh(self) -> None:
        try:
            status = self.manager.status()
        except Exception as exc:  # noqa: BLE001
            self.lock_banner.label.setText(f"检测 NPC 状态失败：{exc}")
            self.lock_banner.show()
            return

        installed = bool(status.get("installed"))
        service_installed = bool(status.get("service_installed"))
        running = bool(status.get("running"))
        bundle = bool(status.get("bundle_available"))
        admin = bool(status.get("is_admin"))

        self.install_badge.setText("已安装" if installed else "未安装")
        self.install_badge.setIcon(FIF.ACCEPT if installed else FIF.CANCEL)
        self.service_badge.setText("开机自启" if service_installed else "服务未注册")
        self.service_badge.setIcon(FIF.ACCEPT if service_installed else FIF.INFO)
        self.run_badge.setText("运行中" if running else "未运行")
        self.run_badge.setIcon(FIF.CONNECT if running else FIF.INFO)
        self.bundle_badge.setText("内置资源已就绪" if bundle else "缺少内置资源")
        self.bundle_badge.setIcon(FIF.ACCEPT if bundle else FIF.CANCEL)
        self.admin_badge.setText("管理员权限" if admin else "普通权限")
        self.admin_badge.setIcon(FIF.ACCEPT if admin else FIF.INFO)

        install_dir = status.get("install_dir", "--")
        exe_path = status.get("exe_path", "--")
        self.install_path.setText(f"安装目录：{install_dir}\n核心文件：{exe_path}")
        self.config_path_label.setText(f"配置文件：{status.get('config_path', '--')}")

        if installed:
            if service_installed:
                self.lock_banner.label.setText("NPC 已安装，配置已解锁。")
                self.install_button.hide()
            else:
                self.lock_banner.label.setText(
                    "NPC 文件已安装，但 Npc 服务未注册。请重新注册开机自启服务。"
                )
                self.install_button.show()
                self.install_button.setEnabled(True)
                self.install_button.setText("注册开机自启服务")
            self.config_box.setEnabled(True)
            try:
                config = self.manager.read_config()
            except Exception as exc:  # noqa: BLE001
                self.nps_status.setText(f"读取 NPC 配置失败：{exc}")
                return
            server = config.get("server_addr", "")
            vkey = config.get("vkey", "")
            if not server and self.ctx.config.nps.server_addr:
                server = (
                    f"{self.ctx.config.nps.server_addr}:"
                    f"{self.ctx.config.nps.server_port}"
                )
            if not vkey:
                vkey = self.ctx.config.nps.vkey
            if not self.server_edit.hasFocus():
                self.server_edit.setText(server)
            if not self.vkey_edit.hasFocus():
                self.vkey_edit.setText(vkey)
            if not self.web_domain_edit.hasFocus():
                self.web_domain_edit.setText(self.ctx.config.nps.web_domain)
            if not self.host_domain_edit.hasFocus():
                self.host_domain_edit.setText(self.ctx.config.nps.host_domain)
        else:
            self.config_box.setEnabled(False)
            self.install_button.show()
            if not bundle:
                self.lock_banner.label.setText(
                    "未检测到内置 NPC 资源。请将 npc.exe 整个文件夹放入 "
                    "client/nexhub/resources/npc 后重新打包客户端。"
                )
                self.install_button.setEnabled(False)
            elif status.get("install_dir_exists"):
                self.lock_banner.label.setText(
                    "NPC 安装目录存在，但核心文件 npc.exe 缺失。请重新解压修复。"
                )
                self.install_button.setEnabled(True)
                self.install_button.setText("重新解压修复")
            else:
                self.lock_banner.label.setText(
                    "尚未安装 NPC。点击安装会释放内置资源并请求管理员权限。"
                )
                self.install_button.setEnabled(True)
                self.install_button.setText("安装 NPC")

    # ------------------------------------------------------------------
    def _install_clicked(self) -> None:
        if self._install_worker and self._install_worker.isRunning():
            return
        status = self.manager.status()
        if not status.get("bundle_available"):
            self._error("客户端未内置 NPC 资源，无法安装")
            return
        self._install(bool(status.get("install_dir_exists") and not status.get("installed")))

    def _install(self, repair: bool) -> None:
        if self._install_worker and self._install_worker.isRunning():
            return
        self._set_busy(True)
        self._install_worker = FunctionWorker(self.manager.install, repair, parent=self)
        self._install_worker.succeeded.connect(self._on_installed)
        self._install_worker.failed.connect(self._on_install_failed)
        self._install_worker.finished.connect(self._set_idle)
        self._install_worker.start()

    def _on_installed(self, status: dict) -> None:
        self.refresh()
        if status.get("service_installed"):
            self._info("NPC 安装完成，Npc 服务已设置为开机自启")
        else:
            self._info("NPC 安装完成")
        self._ensure_nps_host()

    def _ensure_nps_host(self) -> None:
        """安装/启动后自动在 NPS 创建当前设备主机。"""
        if not self.ctx.online or self._auto_tunnel_worker:
            return
        if self._auto_tunnel_worker and self._auto_tunnel_worker.isRunning():
            return
        self._auto_tunnel_worker = FunctionWorker(
            self.ctx.tunnels.ensure_local_tunnel, 3389, parent=self
        )
        self._auto_tunnel_worker.succeeded.connect(self._on_auto_tunnel_ready)
        self._auto_tunnel_worker.failed.connect(
            lambda message: self._error(f"自动注册 NPS 主机失败：{message}", "NPS 注册失败")
        )
        self._auto_tunnel_worker.start()

    def _on_auto_tunnel_ready(self, tunnel: dict | None) -> None:
        if tunnel:
            self.nps_status.setText(f"本机 NPS 主机已注册：{tunnel.get('name', '')}")

    def _on_install_failed(self, message: str) -> None:
        self._error(message, "安装失败")
        self.refresh()

    def _save_clicked(self) -> None:
        if self._save_worker and self._save_worker.isRunning():
            return
        server = self.server_edit.text().strip()
        vkey = self.vkey_edit.text().strip()
        if not server:
            self._error("请填写主机 IP（含端口）", "无法保存")
            return
        if not vkey:
            self._error("请填写 vkey", "无法保存")
            return
        self._set_busy(True)
        self.nps_status.setText("正在保存 npc.conf ...")
        self._save_worker = FunctionWorker(
            self.manager.save_config, server, vkey, parent=self
        )
        self._save_worker.succeeded.connect(self._on_saved)
        self._save_worker.failed.connect(self._on_save_failed)
        self._save_worker.finished.connect(self._set_idle)
        self._save_worker.start()

    def _on_saved(self, result: dict) -> None:
        self._apply_npc_fields_to_config(result.get("server_addr", ""), result.get("vkey", ""))
        self.ctx.config.save()
        self.refresh()
        self.nps_status.setText("配置已保存；重启 NPC 后生效。")
        self._info("NPC 配置已保存，请重启 NPC 使其生效")

    def _on_save_failed(self, message: str) -> None:
        self.nps_status.setText(message)
        self._error(message, "保存失败")

    def _restart_clicked(self) -> None:
        if self._restart_worker and self._restart_worker.isRunning():
            return
        if not self.manager.status().get("installed"):
            self._error("NPC 尚未安装，无法重启", "无法重启")
            return
        self._set_busy(True)
        self.nps_status.setText("正在重启 NPC ...")
        self._restart_worker = FunctionWorker(self.manager.restart, parent=self)
        self._restart_worker.succeeded.connect(self._on_restarted)
        self._restart_worker.failed.connect(self._on_restart_failed)
        self._restart_worker.finished.connect(self._set_idle)
        self._restart_worker.start()

    def _on_restarted(self, result: dict) -> None:
        method = "服务" if result.get("method") == "service" else "进程"
        self.refresh()
        self.nps_status.setText(f"NPC {method}已重启。")
        self._info(f"NPC {method}已重启")

    def _on_restart_failed(self, message: str) -> None:
        self.nps_status.setText(message)
        self._error(message, "重启失败")

    def _test_nps_clicked(self) -> None:
        if self._nps_worker and self._nps_worker.isRunning():
            return
        server = self.server_edit.text().strip()
        if ":" not in server:
            self._error("请先填写带端口的主机地址，例如 1.2.3.4:8024", "无法测试")
            return
        host, port_text = server.rsplit(":", 1)
        if not port_text.isdigit():
            self._error("端口无效", "无法测试")
            return
        self.test_nps_button.setEnabled(False)
        self.test_nps_button.setText("测试中...")
        self.nps_status.setText("正在探测 nps Bridge 端口...")
        self._nps_worker = FunctionWorker(check_nps, host, int(port_text), parent=self)
        self._nps_worker.succeeded.connect(self._on_nps_checked)
        self._nps_worker.failed.connect(
            lambda message: self._on_nps_checked(
                {"online": False, "message": message}
            )
        )
        self._nps_worker.start()

    def _on_nps_checked(self, result: dict) -> None:
        self.test_nps_button.setEnabled(True)
        self.test_nps_button.setText("测试 nps 端口")
        self.nps_status.setText(result.get("message", ""))
        if result.get("online"):
            self._info(result.get("message", "nps 端口可达"), "测试成功")
        else:
            self._error(result.get("message", "nps 端口不可达"), "测试失败")

    # ------------------------------------------------------------------
    def persist_local_config(self) -> None:
        """仅保存本地设置，不写 Program Files 的 npc.conf。"""
        self._apply_npc_fields_to_config(
            self.server_edit.text().strip(), self.vkey_edit.text().strip()
        )
        self.ctx.config.nps.local_port = self.ctx.config.local_api_port
        self.ctx.config.save()

    def _apply_npc_fields_to_config(self, server_addr: str, vkey: str) -> None:
        host, _, port = server_addr.rpartition(":")
        if host and port.isdigit():
            self.ctx.config.nps.server_addr = host
            self.ctx.config.nps.server_port = int(port)
        self.ctx.config.nps.vkey = vkey
        self.ctx.config.nps.web_domain = normalize_server_url(
            self.web_domain_edit.text()
        )
        self.ctx.config.nps.host_domain = self.host_domain_edit.text().strip()

    def _set_busy(self, busy: bool) -> None:
        self.install_button.setEnabled(not busy)
        self.config_box.setEnabled(not busy)
        if busy:
            self.nps_status.setText("正在请求管理员权限/执行操作...")

    def _set_idle(self) -> None:
        self.refresh()

    def _info(self, message: str, title: str = "操作成功") -> None:
        InfoBar.success(
            title,
            message,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=2800,
        )

    def _error(self, message: str, title: str = "操作失败") -> None:
        InfoBar.error(
            title,
            message,
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
            duration=4200,
        )
