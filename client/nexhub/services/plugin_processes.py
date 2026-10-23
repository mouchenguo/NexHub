"""外部插件进程生命周期管理：QProcess 隔离，不影响主程序。"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from pathlib import Path

from PyQt6.QtCore import QIODevice, QObject, QProcess, pyqtSignal

from ..paths import app_data_dir, logs_dir

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
        self._reset_control(plugin_id)
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

    def _control_dir(self, plugin_id: str) -> Path:
        root = app_data_dir() / "plugin_data" / plugin_id / "control"
        (root / "requests").mkdir(parents=True, exist_ok=True)
        (root / "responses").mkdir(parents=True, exist_ok=True)
        return root

    def _reset_control(self, plugin_id: str) -> None:
        """启动新宿主前清理上一次运行遗留的请求/响应。"""
        root = self._control_dir(plugin_id)
        for folder in (root / "requests", root / "responses"):
            for path in folder.glob("*.json"):
                path.unlink(missing_ok=True)
        (root / "state.json").unlink(missing_ok=True)

    def state(self, plugin_id: str) -> dict:
        path = self._control_dir(plugin_id) / "state.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def request(
        self,
        plugin_id: str,
        action: str,
        payload: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        """向插件宿主发送快捷操作；由后台线程调用，避免阻塞 GUI。"""
        if not self.is_running(plugin_id):
            raise RuntimeError("插件尚未启动")
        root = self._control_dir(plugin_id)
        request_id = uuid.uuid4().hex
        request_path = root / "requests" / f"{request_id}.json"
        response_path = root / "responses" / f"{request_id}.json"
        temp = request_path.with_name(f".{request_id}.tmp")
        temp.write_text(
            json.dumps(
                {"id": request_id, "action": action, "payload": payload or {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        temp.replace(request_path)
        deadline = time.monotonic() + timeout
        response = None
        while time.monotonic() < deadline:
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                break
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
        response_path.unlink(missing_ok=True)
        if response is None:
            raise TimeoutError("插件操作超时，请检查插件日志")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "插件操作失败"))
        data = response.get("data")
        return data if isinstance(data, dict) else {"value": data}

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
