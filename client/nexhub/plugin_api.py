"""NexHub 外部插件运行时公共接口。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal

from .paths import VERSION, app_data_dir, logs_dir


class PluginContext(QObject):
    """传给插件进程的受限上下文；不暴露主程序业务对象。"""

    theme_changed = pyqtSignal(str)

    def __init__(self, plugin_id: str, plugin_dir: Path, theme: str = "dark"):
        super().__init__()
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir).resolve()
        self.data_dir = app_data_dir() / "plugin_data" / plugin_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.host_version = VERSION
        self.theme = theme
        self._cleanups: list[Callable[[], None]] = []
        self.log_path = logs_dir() / f"plugin-{plugin_id}.log"
        self.logger = logging.getLogger(f"nexhub.plugin.{plugin_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.log_path, encoding="utf-8")
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            self.logger.addHandler(handler)

    def log(self, message: str, level: int = logging.INFO) -> None:
        self.logger.log(level, message)

    def notify(self, message: str, tone: str = "info") -> None:
        """插件独立窗口中无主程序 Toast 时，至少写入插件日志。"""
        self.log(f"[{tone}] {message}")

    def on_cleanup(self, callback: Callable[[], None]) -> None:
        self._cleanups.append(callback)

    def set_theme(self, theme: str) -> None:
        if theme not in {"dark", "light"} or theme == self.theme:
            return
        self.theme = theme
        self.theme_changed.emit(theme)

    def cleanup(self) -> None:
        callbacks, self._cleanups = self._cleanups, []
        for callback in callbacks:
            try:
                callback()
            except Exception:  # noqa: BLE001
                self.logger.exception("插件清理回调失败")
