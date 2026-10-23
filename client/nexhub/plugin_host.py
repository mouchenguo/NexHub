"""NexHub 插件宿主进程：每个插件在独立进程中运行 UI。"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import threading
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox, QWidget
from qfluentwidgets import Theme, setTheme, setThemeColor

from .plugin_api import PluginContext
from .paths import APP_NAME

ACCENT_COLOR = "#4f8cff"


class _HostBridge(QObject):
    theme_received = pyqtSignal(str)
    shutdown_received = pyqtSignal()


def _load_plugin_module(plugin_dir: Path):
    entry = plugin_dir / "plugin.py"
    if not entry.is_file():
        raise FileNotFoundError(f"插件缺少 plugin.py：{entry}")
    module_name = f"nexhub_external_plugin_{plugin_dir.name}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件入口：{entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(plugin_dir))
    try:
        spec.loader.exec_module(module)
    finally:
        try:
            sys.path.remove(str(plugin_dir))
        except ValueError:
            pass
    factory = getattr(module, "create_window", None)
    if not callable(factory):
        raise AttributeError("plugin.py 必须提供 create_window(context)")
    return factory


def run_plugin_host(plugin_dir: Path, theme: str = "dark") -> int:
    """运行单个插件；退出码由插件窗口生命周期决定。"""
    plugin_dir = Path(plugin_dir).resolve()
    manifest_path = plugin_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"插件 manifest 读取失败：{exc}", file=sys.stderr)
        return 2
    plugin_id = str(manifest.get("id") or plugin_dir.name)
    name = str(manifest.get("name") or plugin_id)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    context = PluginContext(plugin_id, plugin_dir, theme)

    app = QApplication([])
    app.setApplicationName(f"{APP_NAME} · {name}")
    app.setOrganizationName("NexHub")
    app.setFont(QFont("Microsoft YaHei UI", 9))
    setTheme(Theme.DARK if theme != "light" else Theme.LIGHT, save=False)
    setThemeColor(ACCENT_COLOR, save=False)

    try:
        factory = _load_plugin_module(plugin_dir)
        content = factory(context)
        if isinstance(content, QMainWindow):
            window = content
        elif isinstance(content, QWidget):
            window = QMainWindow()
            window.setCentralWidget(content)
            window.setWindowTitle(f"{name} · NexHub 插件")
        else:
            raise TypeError("create_window(context) 必须返回 QWidget 或 QMainWindow")
    except Exception as exc:  # noqa: BLE001
        context.logger.exception("插件加载失败")
        box = QMessageBox()
        box.setWindowTitle("插件加载失败")
        box.setText(f"{name} 加载失败：{exc}")
        box.exec()
        return 3

    bridge = _HostBridge()

    def apply_theme(value: str) -> None:
        setTheme(Theme.DARK if value != "light" else Theme.LIGHT, save=False)
        setThemeColor(ACCENT_COLOR, save=False)
        context.set_theme(value)
        hook = getattr(window, "apply_theme", None)
        if callable(hook):
            try:
                hook(value)
            except Exception:  # noqa: BLE001
                context.logger.exception("插件主题切换失败")

    def request_shutdown() -> None:
        context.cleanup()
        app.quit()

    bridge.theme_received.connect(apply_theme)
    bridge.shutdown_received.connect(request_shutdown)
    app.aboutToQuit.connect(context.cleanup)

    def watch_stdin() -> None:
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "shutdown":
                    bridge.shutdown_received.emit()
                    return
                if command.startswith("theme:"):
                    bridge.theme_received.emit(command.split(":", 1)[1].strip())
        except Exception:  # noqa: BLE001
            context.logger.exception("插件宿主 stdin 读取失败")

    threading.Thread(target=watch_stdin, daemon=True).start()
    window.show()
    QTimer.singleShot(0, lambda: context.logger.info("插件进程已启动"))
    return app.exec()
