"""后台安装插件：下载 + 校验 + 解压。"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from .plugin_market import MarketPlugin, download_package, install_zip

logger = logging.getLogger("nexhub.plugins")


class PluginInstallWorker(QThread):
    progress = pyqtSignal(int, int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        item: MarketPlugin | None = None,
        zip_path: Path | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.item = item
        self.zip_path = Path(zip_path) if zip_path else None

    def _report(self, done: int, total: int, label: str) -> None:
        self.progress.emit(done, total, label)

    def run(self) -> None:
        temp: Path | None = None
        try:
            package = self.zip_path
            if self.item is not None:
                temp = Path(tempfile.mkdtemp(prefix="nexhub-plugin-download-"))
                package = temp / f"{self.item.plugin_id}.zip"
                download_package(
                    self.item.download_url,
                    package,
                    self.item.sha256,
                    self._report,
                )
            if package is None:
                raise ValueError("没有指定插件包")
            record = install_zip(
                package,
                expected_id=self.item.plugin_id if self.item else "",
                progress=self._report,
            )
            self.succeeded.emit(record)
        except Exception as exc:  # noqa: BLE001 - 后台任务把错误交给 UI
            logger.exception("plugin installation failed")
            self.failed.emit(str(exc) or exc.__class__.__name__)
        finally:
            if temp is not None:
                shutil.rmtree(temp, ignore_errors=True)
