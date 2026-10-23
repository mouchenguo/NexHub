"""NexHub 插件宿主进程：每个插件在独立进程中运行 UI。"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
import threading
import uuid
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
    show_received = pyqtSignal()
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
    return factory, module


def run_plugin_host(plugin_dir: Path, theme: str = "dark", hidden: bool = False) -> int:
    """运行单个插件；hidden=True 时只在后台运行，等待 show 指令。"""
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
    # 关闭配置窗口只隐藏插件 UI，不结束后台插件进程。
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    setTheme(Theme.DARK if theme != "light" else Theme.LIGHT, save=False)
    setThemeColor(ACCENT_COLOR, save=False)

    controller = None
    try:
        factory, module = _load_plugin_module(plugin_dir)
        content = factory(context)
        if isinstance(content, QMainWindow):
            window = content
        elif isinstance(content, QWidget):
            window = QMainWindow()
            window.setCentralWidget(content)
            window.setWindowTitle(f"{name} · NexHub 插件")
        else:
            raise TypeError("create_window(context) 必须返回 QWidget 或 QMainWindow")
        controller_factory = getattr(module, "create_controller", None)
        if callable(controller_factory):
            # 传入插件实际 QWidget，而不是外层 QMainWindow 包装。
            controller = controller_factory(context, content)
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

    def show_window() -> None:
        if window.isMinimized():
            window.showNormal()
        window.show()
        window.raise_()
        window.activateWindow()

    def request_shutdown() -> None:
        context.cleanup()
        app.quit()

    bridge.theme_received.connect(apply_theme)
    bridge.show_received.connect(show_window)
    bridge.shutdown_received.connect(request_shutdown)
    app.aboutToQuit.connect(context.cleanup)

    # 主程序通过共享数据目录发送快捷操作请求；控制请求在主线程执行，
    # 避免从工作线程直接操作插件 QWidget。
    control_root = context.data_dir / "control"
    request_dir = control_root / "requests"
    response_dir = control_root / "responses"
    request_dir.mkdir(parents=True, exist_ok=True)
    response_dir.mkdir(parents=True, exist_ok=True)
    state_path = control_root / "state.json"

    def write_json(path: Path, data: object) -> None:
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)

    def publish_state() -> None:
        if controller is None:
            return
        try:
            write_json(state_path, controller.state())
        except Exception:  # noqa: BLE001
            context.logger.exception("插件状态写入失败")

    def process_control_requests() -> None:
        for request_path in sorted(request_dir.glob("*.json")):
            request_id = request_path.stem
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
                action = str(request.get("action") or "")
                payload = request.get("payload") or {}
                if controller is None:
                    raise RuntimeError("插件未提供快捷操作控制器")
                if action == "state":
                    result = controller.state()
                else:
                    result = controller.invoke(action, payload)
                response = {"id": request_id, "ok": True, "data": result}
            except Exception as exc:  # noqa: BLE001
                context.logger.exception("插件快捷操作失败")
                response = {"id": request_id, "ok": False, "error": str(exc)}
            try:
                write_json(response_dir / f"{request_id}.json", response)
            except Exception:  # noqa: BLE001
                context.logger.exception("插件快捷操作响应写入失败")
            finally:
                request_path.unlink(missing_ok=True)
                publish_state()

    publish_state()
    control_timer = QTimer()
    control_timer.timeout.connect(process_control_requests)
    control_timer.start(100)

    def watch_stdin() -> None:
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "shutdown":
                    bridge.shutdown_received.emit()
                    return
                if command == "show":
                    bridge.show_received.emit()
                    continue
                if command.startswith("theme:"):
                    bridge.theme_received.emit(command.split(":", 1)[1].strip())
        except Exception:  # noqa: BLE001
            context.logger.exception("插件宿主 stdin 读取失败")

    threading.Thread(target=watch_stdin, daemon=True).start()
    if hidden:
        context.logger.info("插件已在后台启动，等待 show 指令")
    else:
        window.show()
    QTimer.singleShot(0, lambda: context.logger.info("插件进程已启动"))
    return app.exec()
