"""管理员仪表盘：全局用量、系统日志与网页后台入口。

普通用户不可见（工作空间注册表 + navigate 双重拦截）。
数据来自服务端只读接口 ``GET /api/admin/overview``。
"""
from __future__ import annotations

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, FluentIcon as FIF, ProgressBar, StrongBodyLabel

from ..api_client import ApiError
from ..file_utils import human_size, relative_time
from ..paths import VERSION
from ..widgets.common import (
    Banner,
    StatCard,
    clear_layout,
    make_button,
    make_card,
    section_header,
)
from ..workers import FunctionWorker
from .base import BasePage

LOGS_LIMIT = 30
LEVEL_TONES = {"error": "#f85149", "warning": "#d29922", "info": "#58a6ff"}


class DashboardPage(BasePage):
    page_id = "dashboard"
    title = "仪表盘"
    subtitle = "管理员专属：全局用量、系统日志与网页后台入口"

    def __init__(self, ctx, parent=None):
        self._worker: FunctionWorker | None = None
        self._overview: dict = {}
        super().__init__(ctx, parent)

    # ------------------------------------------------------------------ 构建
    def build(self) -> None:
        self.add_header_action(
            make_button("刷新", icon=FIF.SYNC, on_click=self.refresh)
        )

        self.banner = Banner("", FIF.INFO)
        self.banner.setVisible(False)
        self.content_layout.addWidget(self.banner)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.oss_card = StatCard("OSS 已用", "--", "全局 OSS 长期缓存", FIF.CLOUD)
        self.share_card = StatCard("活跃共享", "--", "当前有效的共享", FIF.SHARE)
        self.download_card = StatCard("累计下载", "--", "全站下载次数", FIF.DOWNLOAD)
        self.user_card = StatCard("注册用户", "--", "含待审核账号", FIF.PEOPLE)
        for column, card in enumerate(
            (self.oss_card, self.share_card, self.download_card, self.user_card)
        ):
            grid.addWidget(card, 0, column)
            grid.setColumnStretch(column, 1)
        self.content_layout.addLayout(grid)

        storage_card, storage_layout = make_card()
        header, _actions = section_header("存储用量", "OSS 配额与全局占用")
        storage_layout.addWidget(header)
        usage_row = QHBoxLayout()
        usage_row.addWidget(CaptionLabel("已用"))
        self.usage_label = StrongBodyLabel("--")
        usage_row.addWidget(self.usage_label)
        usage_row.addStretch(1)
        self.percent_label = CaptionLabel("0%")
        usage_row.addWidget(self.percent_label)
        storage_layout.addLayout(usage_row)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        storage_layout.addWidget(self.progress)
        self.quota_label = CaptionLabel("配额 --")
        storage_layout.addWidget(self.quota_label)
        self.content_layout.addWidget(storage_card)

        logs_card, logs_layout = make_card()
        header, _actions = section_header(
            "系统日志", f"最近 {LOGS_LIMIT} 条服务端全局日志"
        )
        logs_layout.addWidget(header)
        self.logs_layout = QVBoxLayout()
        self.logs_layout.setSpacing(0)
        logs_layout.addLayout(self.logs_layout)
        self.content_layout.addWidget(logs_card)

        footer = QHBoxLayout()
        footer.addWidget(
            make_button(
                "打开网页管理后台",
                primary=True,
                icon=FIF.LINK,
                on_click=self._open_web_admin,
            )
        )
        footer.addStretch(1)
        self.server_label = CaptionLabel("")
        footer.addWidget(self.server_label)
        self.content_layout.addLayout(footer)
        self.content_layout.addStretch(1)

    # ------------------------------------------------------------------ 数据
    def refresh(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        if not self.ctx.online:
            self._show_banner(
                "离线体验模式无法读取管理员概览，请用管理员账号登录。", FIF.INFO
            )
            return
        self._worker = FunctionWorker(self._fetch, parent=self)
        self._worker.succeeded.connect(self._apply)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _fetch(self) -> dict:
        try:
            return {"ok": True, "data": self.ctx.api.admin_overview(LOGS_LIMIT)}
        except ApiError as exc:
            return {"ok": False, "status": exc.status_code, "message": exc.message}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "status": 0, "message": str(exc)}

    def _apply(self, result: dict) -> None:
        if not isinstance(result, dict) or not result.get("ok"):
            self._on_failed(result if isinstance(result, dict) else {})
            return
        payload = result.get("data") or {}
        self._overview = payload if isinstance(payload, dict) else {}
        self.banner.setVisible(False)

        usage = self._overview.get("usage") or {}
        server = self._overview.get("server") or {}
        oss_used = int(usage.get("oss_used") or 0)
        quota = int(usage.get("oss_quota") or 0)
        percent = int(usage.get("oss_percent") or 0)
        self.oss_card.set_value(human_size(oss_used), f"配额 {human_size(quota)}")
        self.share_card.set_value(
            str(int(usage.get("active_shares") or 0)), "全站活跃共享"
        )
        self.download_card.set_value(
            str(int(usage.get("downloads") or 0)), "累计下载次数"
        )
        users = int(usage.get("users") or 0)
        pending = int(usage.get("pending_users") or 0)
        self.user_card.set_value(str(users), f"待审核 {pending} 个")
        self.usage_label.setText(human_size(oss_used))
        self.percent_label.setText(f"{percent}%")
        self.progress.setValue(max(0, min(100, percent)))
        self.quota_label.setText(f"配额 {human_size(quota)} · 客户端 v{VERSION}")
        server_version = str(server.get("version") or "")
        uptime = str(server.get("uptime") or "")
        self.server_label.setText(
            f"服务端 v{server_version} · 运行 {uptime}" if server_version else ""
        )
        self._render_logs(self._overview.get("logs") or [])

    def _on_failed(self, result) -> None:
        if isinstance(result, dict):
            status = int(result.get("status") or 0)
            message = str(result.get("message") or "未知错误")
        else:
            status = 0
            message = str(result)
        if status == 403:
            self._show_banner("当前账号不是管理员，无法查看全局概览。", FIF.INFO)
        elif status == 404:
            self._show_banner(
                "当前服务端未提供 /api/admin/overview（需服务端 ≥ 0.1.7）。", FIF.INFO
            )
        else:
            self._show_banner(f"管理员概览读取失败：{message}", FIF.INFO)

    def _show_banner(self, text: str, icon) -> None:
        self.banner.label.setText(text)
        self.banner.setVisible(True)

    def _render_logs(self, logs: list) -> None:
        clear_layout(self.logs_layout)
        items = [item for item in logs if isinstance(item, dict)]
        if not items:
            self.logs_layout.addWidget(CaptionLabel("暂无系统日志。"))
            return
        for item in items[:LOGS_LIMIT]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(4, 6, 4, 6)
            layout.setSpacing(10)
            level = str(item.get("level") or "info").lower()
            level_label = CaptionLabel(level.upper())
            level_label.setStyleSheet(f"color: {LEVEL_TONES.get(level, '#8b949e')};")
            level_label.setMinimumWidth(56)
            layout.addWidget(level_label)
            meta = " · ".join(
                part
                for part in (
                    str(item.get("category") or ""),
                    str(item.get("username") or ""),
                )
                if part
            )
            meta_label = CaptionLabel(meta or "system")
            meta_label.setMinimumWidth(120)
            layout.addWidget(meta_label)
            message = CaptionLabel(str(item.get("message") or ""))
            message.setWordWrap(True)
            layout.addWidget(message, 1)
            layout.addWidget(CaptionLabel(relative_time(item.get("created_at"))))
            self.logs_layout.addWidget(row)

    def _open_web_admin(self) -> None:
        server = self._overview.get("server") or {}
        url = str(server.get("web_url") or "").strip()
        if not url:
            base = (self.ctx.config.server_url or "").rstrip("/")
            url = f"{base}/admin" if base else ""
        if not url:
            self.ctx.notify("请先在设置页填写服务器地址", "warning")
            return
        QDesktopServices.openUrl(QUrl(url))
