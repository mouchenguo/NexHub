"""插件安装进度对话框。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    CaptionLabel,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    ProgressBar,
    TitleLabel,
)

from ..services.plugin_install import PluginInstallWorker
from ..services.plugin_market import MarketPlugin
from ..widgets.common import make_button


class PluginInstallDialog(QDialog):
    def __init__(
        self,
        ctx,
        *,
        item: MarketPlugin | None = None,
        zip_path: Path | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.item = item
        self.zip_path = zip_path
        self.record = None
        self.worker: PluginInstallWorker | None = None
        self.setWindowTitle("NexHub · 安装插件")
        self.setFixedWidth(500)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        FluentStyleSheet.DIALOG.apply(self)
        self._build()
        QTimer.singleShot(100, self._start)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(9)
        target = self.item.name if self.item else (self.zip_path.name if self.zip_path else "插件")
        layout.addWidget(TitleLabel(f"正在安装 {target}"))
        self.status = CaptionLabel("准备下载插件包..." if self.item else "正在校验本地插件包...")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        layout.addStretch(1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = make_button("关闭", on_click=self.reject)
        self.close_button.setEnabled(False)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def _start(self) -> None:
        self.worker = PluginInstallWorker(
            item=self.item, zip_path=self.zip_path, parent=self
        )
        self.worker.progress.connect(self._on_progress)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self.worker.start()

    def _on_progress(self, done: int, total: int, label: str) -> None:
        if total > 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, int(done / total * 100)))
            self.status.setText(f"{label}… {self.progress.value()}%")
        else:
            self.progress.setRange(0, 0)
            self.status.setText(f"{label}…")

    def _on_succeeded(self, record) -> None:
        self.record = record
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.status.setText(f"插件「{record.name}」安装完成，已注册到插件目录。")
        self.close_button.setEnabled(True)
        QTimer.singleShot(500, self.accept)

    def _on_failed(self, message: str) -> None:
        self.progress.hide()
        self.status.setText(f"安装失败：{message}")
        self.close_button.setEnabled(True)
        InfoBar.error(
            "安装失败",
            message,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=5000,
        )
