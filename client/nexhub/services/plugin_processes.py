"""外部插件进程生命周期管理：QProcess 隔离，不影响主程序。"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PyQt6.QtCore import QIODevice, QObject, QProcess, pyqtSignal

from ..paths import logs_dir

logger = logging.getLogger("nexhub.plugins")


class PluginProcessManager(QObject):
    changed = pyqtSignal()

    def __init__(self, ctx) -> None:
        super().__init__(ctx)
        self.ctx = ctx
        self._processes: dict[str, QProcess] = {}
        self._intentional: set[str] = set()

    def is_running(self, plugin_id: str) -> bool:
        process = self._processes.get(plugin_id)
        return bool(process and process.state() != QProcess.ProcessState.NotRunning)

    def running_ids(self) -> set[str]:
        return {
            plugin_id
            for plugin_id in self._processes
            if self.is_running(plugin_id)
        }

    def start(self, record, hidden: bool = True) -> None:
        """启动插件宿主；默认后台运行，不自动显示插件窗口。"""
        plugin_id = record.plugin_id
        if self.is_running(plugin_id):
            return
        if not record.folder.is_dir():
            raise FileNotFoundError(f"插件目录不存在：{record.folder}")
        log_path = logs_dir() / f"plugin-{plugin_id}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        process = QProcess(self)
        process.setProgram(sys.executable)
        args: list[str] = []
        if not getattr(sys, "frozen", False):
            args.append(str(Path(__file__).resolve().parents[2] / "main.py"))
        args.extend(
            [
                "--plugin-host",
                str(record.folder),
                "--theme",
                self.ctx.theme.effective_theme,
            ]
        )
        if hidden:
            args.append("--hidden")
        process.setArguments(args)
        process.setWorkingDirectory(str(record.folder))
        process.setStandardOutputFile(
            str(log_path), QIODevice.OpenModeFlag.Append
        )
        process.setStandardErrorFile(
            str(log_path), QIODevice.OpenModeFlag.Append
        )
        self._intentional.discard(plugin_id)
        process.finished.connect(
            lambda code, _status, pid=plugin_id: self._on_finished(pid, code)
        )
        process.stateChanged.connect(lambda _state: self.changed.emit())
        self._processes[plugin_id] = process
        process.start()
        if not process.waitForStarted(5000):
            self._processes.pop(plugin_id, None)
            process.deleteLater()
            raise RuntimeError(f"插件「{record.name}」启动失败，详见 {log_path}")
        logger.info("started plugin %s pid=%s hidden=%s", plugin_id, process.processId(), hidden)
        self.changed.emit()

    def show(self, plugin_id: str) -> None:
        """请求已在后台运行的插件显示自己的配置窗口。"""
        process = self._processes.get(plugin_id)
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            raise RuntimeError("插件尚未启动")
        process.write(b"show\n")
        process.waitForBytesWritten(500)

    def _on_finished(self, plugin_id: str, exit_code: int) -> None:
        process = self._processes.pop(plugin_id, None)
        intentional = plugin_id in self._intentional
        self._intentional.discard(plugin_id)
        if process is not None:
            process.deleteLater()
        logger.info(
            "plugin %s finished code=%s intentional=%s",
            plugin_id,
            exit_code,
            intentional,
        )
        if not intentional and exit_code != 0:
            self.ctx.notify(
                f"插件「{plugin_id}」异常退出（代码 {exit_code}），主程序继续运行",
                "error",
            )
        self.changed.emit()

    def stop(self, plugin_id: str) -> None:
        process = self._processes.get(plugin_id)
        if process is None or process.state() == QProcess.ProcessState.NotRunning:
            return
        self._intentional.add(plugin_id)
        process.write(b"shutdown\n")
        process.waitForBytesWritten(500)
        if not process.waitForFinished(2500):
            process.terminate()
            if not process.waitForFinished(1500):
                process.kill()
                process.waitForFinished(1000)
        self.changed.emit()

    def set_theme(self, theme: str) -> None:
        payload = f"theme:{theme}\n".encode("utf-8")
        for plugin_id, process in list(self._processes.items()):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.write(payload)
                process.waitForBytesWritten(300)

    def stop_all(self) -> None:
        for plugin_id in list(self._processes):
            self.stop(plugin_id)
