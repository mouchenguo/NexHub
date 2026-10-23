"""更新提示与自动安装对话框。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentStyleSheet,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    ProgressBar,
    TitleLabel,
)

from ..services.update_service import (
    UpdateInfo,
    auto_update_supported,
    download_update,
    install_update,
)
from ..widgets.common import make_button
from ..workers import FunctionWorker


class UpdateDialog(QDialog):
    """询问用户是否自动下载、替换并重启客户端。"""

    def __init__(self, ctx, info: UpdateInfo, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.info = info
        self._worker: FunctionWorker | None = None
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · 发现新版本")
        self.setFixedWidth(540)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(9)
        layout.addWidget(TitleLabel(f"发现新版本 v{self.info.latest_version}"))
        layout.addWidget(
            CaptionLabel(
                f"当前版本 v{self.info.current_version}。"
                "自动安装会关闭 NexHub、替换当前程序并重新启动；账号和本地配置不会丢失。"
            )
        )
        notes = "\n".join(f"• {item}" for item in self.info.notes[:6])
        if notes:
            body = CaptionLabel(notes)
            body.setWordWrap(True)
            layout.addWidget(body)
        self.status = CaptionLabel("是否下载并自动安装？")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        layout.addWidget(self.progress)
        if not auto_update_supported():
            self.status.setText("源码运行模式不支持自动安装，请打开下载页手动更新。")
        elif not self.info.download_url or len(self.info.sha256.strip()) != 64:
            self.status.setText("更新清单缺少有效下载地址或 SHA-256，已禁用自动安装。")
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = make_button("稍后", on_click=self.reject)
        self.install_button = make_button(
            "自动安装", primary=True, icon=FIF.DOWNLOAD, on_click=self._install
        )
        if not auto_update_supported() or not self.info.download_url or len(
            self.info.sha256.strip()
        ) != 64:
            self.install_button.setEnabled(False)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.install_button)
        layout.addLayout(footer)

    def _install(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.install_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.status.setText("正在下载更新，请稍候...")
        self.progress.setRange(0, 0)
        self.progress.show()
        self._worker = FunctionWorker(download_update, self.info, parent=self)
        self._worker.succeeded.connect(self._on_downloaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_downloaded(self, path: Path) -> None:
        try:
            install_update(path)
        except Exception as exc:  # noqa: BLE001 - 向用户展示安装失败原因
            self._on_failed(str(exc))
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText("下载完成，正在重启并安装新版本...")
        QTimer.singleShot(700, self._restart)

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self.status.setText(f"自动安装失败：{message}")
        self.install_button.setEnabled(True)
        self.cancel_button.setEnabled(True)
        InfoBar.error(
            "自动安装失败",
            message,
            parent=self,
            position=InfoBarPosition.TOP,
        )

    def _restart(self) -> None:
        window = self.ctx.main_window
        if window is not None:
            window.request_quit()
        else:
            QApplication.quit()
