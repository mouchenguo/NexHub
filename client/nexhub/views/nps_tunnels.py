"""工具箱中的 NPS 隧道管理面板。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    FluentStyleSheet,
    LineEdit,
    SpinBox,
    StrongBodyLabel,
    TitleLabel,
)

from ..widgets.common import (
    Banner,
    EmptyState,
    ask_confirm,
    clear_layout,
    form_field,
    icon_button,
    icon_widget,
    make_button,
    make_card,
    section_header,
    status_pill,
)
from ..workers import FunctionWorker


class TunnelEditDialog(QDialog):
    """编辑隧道的名称 / 本地服务地址。"""

    def __init__(self, ctx, tunnel: dict, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.tunnel = tunnel
        self._worker: FunctionWorker | None = None
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · 编辑 NPS 隧道")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(500)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(TitleLabel("编辑 NPS 隧道"))
        layout.addWidget(BodyLabel("修改隧道名称与本地服务地址；vkey 保持不变。"))
        layout.addSpacing(4)

        self.name_edit = LineEdit()
        self.name_edit.setText(str(self.tunnel.get("name", "")))
        layout.addLayout(form_field("隧道名称", self.name_edit))

        self.host_edit = LineEdit()
        self.host_edit.setText(str(self.tunnel.get("local_host", "127.0.0.1")))
        layout.addLayout(
            form_field(
                "本地服务 IP / 主机名",
                self.host_edit,
                "例如 127.0.0.1 或 192.168.1.20",
            )
        )

        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(int(self.tunnel.get("local_port") or 8765))
        layout.addLayout(form_field("本地服务端口", self.port_spin))

        self.note = CaptionLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        self.save_button = make_button(
            "保存", primary=True, icon=FIF.SAVE, on_click=self._save
        )
        footer.addWidget(self.save_button)
        layout.addLayout(footer)

    def _save(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        name = self.name_edit.text().strip()
        host = self.host_edit.text().strip()
        if not name or not host:
            self.note.setText("名称和本地服务地址不能为空")
            return
        self.save_button.setEnabled(False)
        self.save_button.setText("保存中...")
        self._worker = FunctionWorker(
            self.ctx.tunnels.update_tunnel,
            self.tunnel,
            name,
            host,
            self.port_spin.value(),
            parent=self,
        )
        self._worker.succeeded.connect(self._on_saved)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_saved(self, _tunnel: dict) -> None:
        self.accept()

    def _on_failed(self, message: str) -> None:
        self.save_button.setEnabled(True)
        self.save_button.setText("保存")
        self.note.setText(f"保存失败：{message}")


class NpsTunnelPanel(QWidget):
    """工具箱中的 NPS 客户端/隧道管理卡片。"""

    def __init__(self, ctx, parent: QWidget | None = None, embedded: bool = False):
        super().__init__(parent)
        self.ctx = ctx
        self._embedded = embedded
        self._list_worker: FunctionWorker | None = None
        self._action_worker: FunctionWorker | None = None
        self._status_worker: FunctionWorker | None = None
        self._tunnels: list[dict] = []
        self._build()
        self.refresh_npc_status()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        if self._embedded:
            layout = QVBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(12)
            actions = QHBoxLayout()
            actions.setSpacing(8)
            actions.addStretch(1)
        else:
            card, layout = make_card(20, 12)
            header, actions = section_header(
                "NPS 隧道管理",
                "每台内网主机自动创建独立 NPS 客户端与 vkey",
            )
            layout.addWidget(header)
        self.count_pill = status_pill("0 条", FIF.CONNECT)
        actions.addWidget(self.count_pill)
        actions.addWidget(
            make_button(
                "重启 NPC", transparent=True, icon=FIF.SYNC, on_click=self._restart_npc
            )
        )
        actions.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        if self._embedded:
            layout.addLayout(actions)

        self.npc_caption = CaptionLabel("NPC 状态检测中...")
        self.npc_caption.setWordWrap(True)
        layout.addWidget(self.npc_caption)

        self.demo_note = Banner(
            "未登录预览：登录后可管理云端 NPS 隧道；本地 NPC 配置仍可在设置中查看。",
            FIF.INFO,
        )
        self.demo_note.setVisible(not self.ctx.online)
        layout.addWidget(self.demo_note)

        form_row = QHBoxLayout()
        form_row.setSpacing(12)
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("例如 书房工作台")
        form_row.addLayout(form_field("隧道名称", self.name_edit), 2)

        self.host_edit = LineEdit()
        self.host_edit.setText("127.0.0.1")
        self.host_edit.setPlaceholderText("127.0.0.1")
        form_row.addLayout(form_field("本地服务 IP", self.host_edit), 2)

        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(8765)
        form_row.addLayout(form_field("端口", self.port_spin), 1)

        self.create_button = make_button(
            "创建隧道", primary=True, icon=FIF.ADD, on_click=self._create
        )
        form_row.addWidget(self.create_button, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(form_row)

        hint = CaptionLabel(
            "提交后由 ECS 调用 NPS API 创建客户端并生成唯一 vkey；"
            "本机 NPC 需要管理员权限，首次下发会弹出 UAC。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.summary = CaptionLabel("正在加载 NPS 隧道...")
        layout.addWidget(self.summary)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        layout.addWidget(self.list_host)

        self.empty = EmptyState(
            "还没有 NPS 隧道",
            "填写本机服务的 IP 与端口，创建第一条独立 vkey 的隧道。",
            FIF.CONNECT,
        )
        self.empty.hide()
        layout.addWidget(self.empty)
        if not self._embedded:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(card)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._list_worker and self._list_worker.isRunning():
            return
        self._list_worker = FunctionWorker(self.ctx.tunnels.list_tunnels, parent=self)
        self._list_worker.succeeded.connect(self._on_loaded)
        self._list_worker.failed.connect(self._on_load_failed)
        self._list_worker.start()

    def refresh_npc_status(self) -> None:
        if self._status_worker and self._status_worker.isRunning():
            return
        self._status_worker = FunctionWorker(self.ctx.tunnels.npc_status, parent=self)
        self._status_worker.succeeded.connect(self._on_npc_status)
        self._status_worker.failed.connect(
            lambda message: self.npc_caption.setText(f"NPC 状态读取失败：{message}")
        )
        self._status_worker.start()

    def on_show(self) -> None:
        self.refresh()
        self.refresh_npc_status()

    def _on_npc_status(self, status: dict) -> None:
        if status.get("running"):
            state = "运行中"
        elif status.get("installed"):
            state = "已安装，未运行"
        else:
            state = "未安装"
        path = status.get("config_path") or status.get("install_dir") or "--"
        self.npc_caption.setText(f"NPC：{state} · 配置 {path}")

    def _on_load_failed(self, message: str) -> None:
        self.summary.setText(f"NPS 隧道加载失败：{message}")
        self.ctx.notify(f"隧道列表加载失败：{message}", "error")

    def _on_loaded(self, tunnels: list[dict]) -> None:
        self._tunnels = tunnels
        self._render()

    # ------------------------------------------------------------------
    def _create(self) -> None:
        if self._action_worker and self._action_worker.isRunning():
            return
        name = self.name_edit.text().strip()
        host = self.host_edit.text().strip()
        if not name or not host:
            self.ctx.notify("请填写隧道名称和本地服务 IP", "warning")
            return
        self.create_button.setEnabled(False)
        self.create_button.setText("创建中...")
        self._action_worker = FunctionWorker(
            self.ctx.tunnels.create_tunnel,
            name,
            host,
            self.port_spin.value(),
            parent=self,
        )
        self._action_worker.succeeded.connect(self._on_created)
        self._action_worker.failed.connect(self._on_create_failed)
        self._action_worker.finished.connect(self._reset_create_button)
        self._action_worker.start()

    def _on_created(self, tunnel: dict) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验：模拟隧道已创建，不会调用 NPS 或写入 NPC", "info")
        elif tunnel.get("local_error"):
            local_error = tunnel.get("local_error")
            self.ctx.notify(
                f"NPS 客户端已创建，但本机 NPC 配置下发失败：{local_error}",
                "warning",
            )
        else:
            self.ctx.notify("NPS 隧道已创建，vkey 已下发到本机 NPC", "success")
        self.name_edit.clear()
        self.refresh()
        self.refresh_npc_status()

    def _on_create_failed(self, message: str) -> None:
        self.ctx.notify(f"创建隧道失败：{message}", "error")

    def _reset_create_button(self) -> None:
        self.create_button.setEnabled(True)
        self.create_button.setText("创建隧道")

    # ------------------------------------------------------------------
    def _copy_vkey(self, tunnel: dict) -> None:
        vkey = str(tunnel.get("vkey") or "")
        if not vkey:
            self.ctx.notify("该隧道没有可复制的 vkey", "warning")
            return
        QApplication.clipboard().setText(vkey)
        self.ctx.notify("完整 vkey 已复制", "success")

    def _apply(self, tunnel: dict) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验模式不会写入本机 npc.conf", "info")
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(
            self.ctx.tunnels.apply_tunnel, tunnel, parent=self
        )
        self._action_worker.succeeded.connect(
            lambda _result: (
                self.ctx.notify("已下发配置并重启 NPC", "success"),
                self.refresh_npc_status(),
            )
        )
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"下发本机失败：{message}", "error")
        )
        self._action_worker.start()

    def _toggle(self, tunnel: dict) -> None:
        if self._action_worker and self._action_worker.isRunning():
            return
        target = not bool(tunnel.get("enabled"))
        self._action_worker = FunctionWorker(
            self.ctx.tunnels.set_enabled, tunnel, target, parent=self
        )
        self._action_worker.succeeded.connect(
            lambda _result: (
                self.ctx.notify("NPS 隧道状态已更新", "success"),
                self.refresh(),
            )
        )
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"更新隧道状态失败：{message}", "error")
        )
        self._action_worker.start()

    def _edit(self, tunnel: dict) -> None:
        dialog = TunnelEditDialog(self.ctx, tunnel, self.window())
        if dialog.exec():
            self.refresh()

    def _delete(self, tunnel: dict) -> None:
        if not ask_confirm(
            self,
            "删除 NPS 隧道",
            f"确定删除隧道「{tunnel.get('name')}」？\n"
            "这会同时删除 NPS 服务端对应客户端，当前本机 npc.conf 不会自动清空。",
        ):
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(
            self.ctx.tunnels.delete_tunnel, tunnel, parent=self
        )
        self._action_worker.succeeded.connect(
            lambda _result: (
                self.ctx.notify("隧道已删除", "success"),
                self.refresh(),
            )
        )
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"删除隧道失败：{message}", "error")
        )
        self._action_worker.start()

    def _restart_npc(self) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验模式不会操作本机 NPC", "info")
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(self.ctx.tunnels.restart_npc, parent=self)
        self._action_worker.succeeded.connect(
            lambda _result: (
                self.ctx.notify("NPC 已重启", "success"),
                self.refresh_npc_status(),
            )
        )
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"重启 NPC 失败：{message}", "error")
        )
        self._action_worker.start()

    # ------------------------------------------------------------------
    def _render(self) -> None:
        clear_layout(self.list_layout)
        count = len(self._tunnels)
        enabled_count = sum(1 for item in self._tunnels if item.get("enabled"))
        self.count_pill.setText(f"{count} 条")
        self.summary.setText(f"共 {count} 条隧道 · {enabled_count} 条已启用")
        self.empty.setVisible(count == 0)
        for tunnel in self._tunnels:
            self.list_layout.addWidget(self._tunnel_card(tunnel))

    def _tunnel_card(self, tunnel: dict) -> QWidget:
        card, layout = make_card(16, 8)
        enabled = bool(tunnel.get("enabled"))
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(icon_widget(FIF.GLOBE, 20))
        top.addWidget(StrongBodyLabel(str(tunnel.get("name") or "未命名隧道")))
        top.addWidget(
            status_pill("已启用" if enabled else "已停用", FIF.ACCEPT if enabled else FIF.PAUSE)
        )
        top.addStretch(1)
        top.addWidget(
            icon_button(
                FIF.COPY,
                tooltip="复制完整 vkey",
                on_click=lambda _checked=False, item=tunnel: self._copy_vkey(item),
            )
        )
        top.addWidget(
            icon_button(
                FIF.SEND,
                tooltip="下发 vkey 到本机 npc.conf 并重启 NPC",
                on_click=lambda _checked=False, item=tunnel: self._apply(item),
            )
        )
        top.addWidget(
            icon_button(
                FIF.PAUSE if enabled else FIF.PLAY,
                tooltip="停用" if enabled else "启用",
                on_click=lambda _checked=False, item=tunnel: self._toggle(item),
            )
        )
        top.addWidget(
            icon_button(
                FIF.EDIT,
                tooltip="编辑隧道",
                on_click=lambda _checked=False, item=tunnel: self._edit(item),
            )
        )
        top.addWidget(
            icon_button(
                FIF.DELETE,
                tooltip="删除隧道和 NPS 客户端",
                on_click=lambda _checked=False, item=tunnel: self._delete(item),
            )
        )
        layout.addLayout(top)

        local = f"{tunnel.get('local_host', '--')}:{tunnel.get('local_port', '--')}"
        server = (
            f"{tunnel.get('server_addr') or '未配置'}:"
            f"{tunnel.get('server_port') or 8024}"
        )
        vkey = str(tunnel.get("vkey") or "")
        masked_vkey = f"{vkey[:8]}…{vkey[-4:]}" if len(vkey) > 14 else vkey or "--"
        detail = CaptionLabel(
            f"本地服务 {local} · NPS {server} · 客户端 #{tunnel.get('nps_client_id') or '--'} · "
            f"vkey {masked_vkey}"
        )
        detail.setWordWrap(True)
        detail.setToolTip(f"vkey: {vkey}" if vkey else "")
        layout.addWidget(detail)
        return card



