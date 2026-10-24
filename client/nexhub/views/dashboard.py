"""仪表盘：Fluent 卡片、统计、存储进度与最近动态。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, FluentIcon as FIF, ProgressBar, StrongBodyLabel

from ..file_utils import human_size, relative_time
from ..paths import VERSION
from ..widgets.common import (
    Banner,
    StatCard,
    clear_layout,
    icon_widget,
    make_button,
    make_card,
    section_header,
)
from ..workers import FunctionWorker
from .base import BasePage


class DashboardPage(BasePage):
    page_id = "dashboard"
    title = "仪表盘"
    subtitle = "共享、RDP 主机与存储空间的全局概览"

    def __init__(self, ctx, parent=None):
        self._worker: FunctionWorker | None = None
        self._activity_layout = None
        super().__init__(ctx, parent)

    # ------------------------------------------------------------------
    def build(self) -> None:
        self._build_offline_banner()
        self._build_stats()

        middle = QHBoxLayout()
        middle.setSpacing(18)
        middle.addWidget(self._build_storage_card(), 3)
        middle.addWidget(self._build_status_card(), 2)
        self.content_layout.addLayout(middle)

        self._build_activity()
        self.content_layout.addStretch(1)

    def _build_offline_banner(self) -> None:
        self.offline_banner = Banner(
            "当前未登录，处于预览模式。配置 ECS 地址并登录后即可同步真实共享、在线设备与云端功能。",
            FIF.INFO,
            make_button("前往设置", on_click=lambda: self.ctx.navigate("settings")),
        )
        self.offline_banner.setVisible(not self.ctx.online)
        self.content_layout.addWidget(self.offline_banner)

    def _build_stats(self) -> None:
        grid = QGridLayout()
        grid.setSpacing(16)
        self.active_card = StatCard("活跃共享", "--", "正在加载...", FIF.SHARE)
        self.hosts_card = StatCard("RDP 主机", "--", "正在加载...", FIF.PROJECTOR)
        self.oss_card = StatCard("OSS 已用容量", "--", "正在加载...", FIF.CLOUD)
        grid.addWidget(self.active_card, 0, 0)
        grid.addWidget(self.hosts_card, 0, 1)
        grid.addWidget(self.oss_card, 0, 2)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.content_layout.addLayout(grid)

    def _build_storage_card(self) -> QWidget:
        card, layout = make_card()
        header, actions = section_header("存储用量", "OSS 长期缓存与临时共享规模")
        layout.addWidget(header)

        usage = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(2)
        left.addWidget(CaptionLabel("OSS 已用"))
        self.oss_usage_value = StrongBodyLabel("--")
        left.addWidget(self.oss_usage_value)
        usage.addLayout(left)
        usage.addStretch(1)
        self.oss_percent = CaptionLabel("0%")
        usage.addWidget(self.oss_percent)
        layout.addLayout(usage)

        self.oss_progress = ProgressBar()
        self.oss_progress.setRange(0, 100)
        self.oss_progress.setValue(0)
        layout.addWidget(self.oss_progress)

        self.storage_split = CaptionLabel("OSS 0 B · 临时共享 0 B")
        layout.addWidget(self.storage_split)
        layout.addWidget(
            Banner(
                "从 OSS 下载文件会产生外网流量费用，请及时清理不再需要的长期共享。",
                FIF.INFO,
            )
        )
        return card

    def _build_status_card(self) -> QWidget:
        card, layout = make_card()
        header, actions = section_header("服务状态", "实时连接概览")
        layout.addWidget(header)

        self.status_labels: dict[str, StrongBodyLabel] = {}
        for key, label in (
            ("ecs", "ECS API"),
            ("nps", "nps 穿透"),
            ("version", "客户端版本"),
        ):
            row = QHBoxLayout()
            row.addWidget(CaptionLabel(label))
            row.addStretch(1)
            value = StrongBodyLabel("--")
            row.addWidget(value)
            layout.addLayout(row)
            self.status_labels[key] = value

        layout.addStretch(1)
        self.status_hint = CaptionLabel("")
        self.status_hint.setWordWrap(True)
        layout.addWidget(self.status_hint)

        actions_row = QHBoxLayout()
        actions_row.addWidget(
            make_button(
                "发布共享",
                primary=True,
                icon=FIF.ADD,
                on_click=lambda: self.ctx.navigate("my_shares"),
            )
        )
        actions_row.addWidget(
            make_button(
                "RDP 主机",
                icon=FIF.PROJECTOR,
                on_click=lambda: self.ctx.navigate("rdp"),
            )
        )
        actions_row.addStretch(1)
        layout.addLayout(actions_row)
        return card

    def _build_activity(self) -> None:
        card, layout = make_card()
        header, actions = section_header("最近动态", "共享发布与下载记录")
        layout.addWidget(header)
        self._activity_layout = QVBoxLayout()
        self._activity_layout.setSpacing(0)
        layout.addLayout(self._activity_layout)
        self.content_layout.addWidget(card)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self._fetch, parent=self)
        self._worker.succeeded.connect(self._apply)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _fetch(self) -> dict:
        if self.ctx.online:
            data = self.ctx.api.dashboard()
            shares = self.ctx.api.list_shares("mine")
        else:
            data = self.ctx.demo.dashboard()
            shares = self.ctx.demo.list_shares("mine")
        return {"dashboard": data, "shares": shares, "rdp_count": len(self.ctx.rdp.list_hosts())}

    def _apply(self, payload: dict) -> None:
        data = payload["dashboard"]
        shares = [item for item in payload["shares"] if item.get("status") == "active"]
        rdp_count = int(payload.get("rdp_count") or 0)
        oss_shares = [item for item in shares if item.get("storage_type") == "oss"]
        temp_shares = [item for item in shares if item.get("storage_type") != "oss"]

        self.active_card.set_value(
            str(len(shares)),
            f"OSS {len(oss_shares)} 个 · 临时穿透 {len(temp_shares)} 个",
        )
        self.hosts_card.set_value(
            str(rdp_count),
            "本机保存的 RDP 连接" if rdp_count else "尚未添加 RDP 主机",
        )
        oss_used = int(data.get("oss_used") or 0)
        quota = int(data.get("oss_quota") or 0)
        percent = int(oss_used / quota * 100) if quota else 0
        self.oss_card.set_value(
            human_size(oss_used), f"配额 {human_size(quota)} · 已用 {percent}%"
        )
        self.oss_usage_value.setText(human_size(oss_used))
        self.oss_percent.setText(f"{percent}%")
        self.oss_progress.setValue(min(100, percent))
        temp_bytes = sum(int(item.get("total_size") or 0) for item in temp_shares)
        self.storage_split.setText(
            f"OSS 长期共享 {human_size(oss_used)}  ·  临时共享 {human_size(temp_bytes)}"
        )
        self.offline_banner.setVisible(not self.ctx.online)
        self._apply_status(data, rdp_count)
        self._render_activity(data.get("recent_activity", []))

    def _apply_status(self, data: dict, rdp_count: int) -> None:
        self.status_labels["ecs"].setText("已连接" if self.ctx.online else "离线体验")
        self.status_labels["nps"].setText(
            "已配置" if self.ctx.config.nps.server_addr else "未配置"
        )
        self.status_labels["version"].setText(f"v{VERSION}")
        if self.ctx.online:
            self.status_hint.setText(
                f"ECS {data.get('server', {}).get('version', '')} · "
                f"RDP 主机 {rdp_count} 台 · 累计下载 {data.get('total_downloads', 0)} 次"
            )
        else:
            self.status_hint.setText("未登录预览：登录后可同步共享、在线设备与云端功能。")

    def _render_activity(self, items: list[dict]) -> None:
        clear_layout(self._activity_layout)
        if not items:
            empty = CaptionLabel("暂无动态，发布一个共享试试。")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._activity_layout.addWidget(empty)
            return
        for item in items:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(4, 10, 4, 10)
            row_layout.setSpacing(10)
            row_layout.addWidget(icon_widget(FIF.HISTORY, 18))

            text_box = QVBoxLayout()
            text_box.setSpacing(1)
            text_box.addWidget(BodyLabel(item.get("title", "")))
            text_box.addWidget(CaptionLabel(item.get("detail", "")))
            row_layout.addLayout(text_box, 1)
            row_layout.addWidget(CaptionLabel(relative_time(item.get("time"))))
            self._activity_layout.addWidget(row)

    def _on_error(self, message: str) -> None:
        self.status_hint.setText(f"数据刷新失败：{message}")
        self.ctx.notify(f"仪表盘刷新失败：{message}", "error")
