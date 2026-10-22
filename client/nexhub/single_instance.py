"""Windows 单实例保护：第二次启动唤醒已有窗口。"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

try:  # pywin32 是客户端 Windows 依赖
    import win32api
    import win32con
    import win32event
except ImportError:  # pragma: no cover - 非 Windows / 未安装 pywin32
    win32api = win32con = win32event = None


MUTEX_NAME = "Local\\NexHub.Client.SingleInstance.v1"
SERVER_NAME = "NexHub.Client.SingleInstance.v1"
ERROR_ALREADY_EXISTS = getattr(win32con, "ERROR_ALREADY_EXISTS", 183) if win32con else 183


class SingleInstance(QObject):
    """持有命名互斥体，并通过本地 IPC 唤醒已有实例。"""

    show_requested = pyqtSignal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._handle = None
        self._pending_show = False
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_connection)
        self.already_running = False
        self._acquire()

    def _acquire(self) -> None:
        if win32event is None or win32api is None:
            # 退化方案：用本地 IPC 判断是否有实例在监听。
            self._start_server()
            self.already_running = not self._server.isListening()
            return
        try:
            self._handle = win32event.CreateMutex(None, False, MUTEX_NAME)
            self.already_running = win32api.GetLastError() == ERROR_ALREADY_EXISTS
        except Exception:
            self._handle = None
            self._start_server()
            self.already_running = not self._server.isListening()
            return
        if not self.already_running:
            self._start_server()

    def _start_server(self) -> None:
        # 上次异常退出可能留下同名本地管道；先清理再监听。
        QLocalServer.removeServer(SERVER_NAME)
        self._server.listen(SERVER_NAME)

    def notify_existing(self, timeout_seconds: float = 8.0) -> bool:
        """通知已有实例显示窗口；成功返回 True。"""
        if not self.already_running:
            return True
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(SERVER_NAME)
            if socket.waitForConnected(250):
                socket.write(b"show")
                socket.waitForBytesWritten(500)
                socket.disconnectFromServer()
                return True
            socket.abort()
            time.sleep(0.08)
        return self._wake_by_window_title()

    def _accept_connection(self) -> None:
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                continue
            connection.readyRead.connect(lambda c=connection: c.readAll())
            connection.disconnected.connect(connection.deleteLater)
        self._pending_show = True
        self.show_requested.emit()

    def consume_pending_show(self) -> bool:
        pending = self._pending_show
        self._pending_show = False
        return pending

    @staticmethod
    def _wake_by_window_title() -> bool:
        """本地 IPC 不可用时的兜底：按标题找主窗口并恢复。"""
        user32 = ctypes.windll.user32
        found: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                if buffer.value.startswith("NexHub v"):
                    found.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        if not found:
            return False
        hwnd = found[0]
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True

    def close(self) -> None:
        if self._server.isListening():
            self._server.close()
        if self._handle is not None:
            try:
                win32api.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
