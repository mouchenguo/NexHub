"""NexHub 客户端入口。

用法：
    python main.py            # 默认演示预览；记住我时自动登录
    python main.py --demo     # 强制进入演示预览模式（开发调试）
    python main.py --reset-config
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parent
if str(CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(CLIENT_ROOT))

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from nexhub.api_client import ApiClient
from nexhub.app import MainWindow, build_app_icon
from nexhub.config import AppConfig
from nexhub.context import AppContext
from nexhub.paths import APP_NAME, VERSION, logs_dir
from nexhub.services.credential_store import CredentialStore
from nexhub.single_instance import SingleInstance
from nexhub.theme import ThemeManager


def _try_auto_login(config: AppConfig) -> dict | None:
    """只有勾选“记住我”且凭证可解密时才自动登录。"""
    if not config.remember_me or not config.credential_enc:
        config.enter_demo()
        return None
    credentials = CredentialStore.load(config.credential_enc)
    if credentials is None:
        config.enter_demo()
        return None
    client = ApiClient(config)
    try:
        result = client.login(*credentials)
        if not isinstance(result.get("server"), dict):
            try:
                result["server"] = client.health()
            except Exception:
                pass
    except Exception:
        # 网络或服务暂时不可用时保留加密凭证，下次启动仍可自动重试。
        config.demo_mode = True
        config.token = ""
        config.username = "demo"
        config.display_name = "请登录"
        config.role = "user"
        config.save()
        return None
    finally:
        client.close()
    user = result.get("user", {})
    config.token = str(result.get("token", ""))
    if not config.token:
        config.enter_demo()
        return None
    config.username = str(user.get("username", credentials[0]))
    config.display_name = str(user.get("display_name") or config.username)
    config.role = str(user.get("role", "user"))
    config.demo_mode = False
    config.remember_me = True
    config.save()
    return result


def _run_plugin_host_helper() -> int | None:
    if "--plugin-host" not in sys.argv:
        return None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--plugin-host", required=True)
    parser.add_argument("--theme", default="dark")
    parser.add_argument("--hidden", action="store_true")
    args, _ = parser.parse_known_args()
    from nexhub.plugin_host import run_plugin_host

    return run_plugin_host(Path(args.plugin_host), args.theme, hidden=args.hidden)


def _run_npc_admin_helper() -> int | None:
    """UAC 提权后以隐藏模式执行 NPC 安装/写配置/重启。"""
    if "--npc-admin" not in sys.argv:
        return None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--npc-admin", action="store_true")
    parser.add_argument("--request", required=True)
    args, _ = parser.parse_known_args()
    from nexhub.npc_admin import run_request

    return run_request(args.request)


def _setup_logging() -> None:
    log_file = logs_dir() / "client.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger(__name__).info("NexHub client %s starting", VERSION)


def main(argv: list[str] | None = None) -> int:
    plugin_exit = _run_plugin_host_helper()
    if plugin_exit is not None:
        return plugin_exit
    helper_exit = _run_npc_admin_helper()
    if helper_exit is not None:
        return helper_exit

    parser = argparse.ArgumentParser(description="NexHub Windows 客户端")
    parser.add_argument("--demo", action="store_true", help="强制进入演示预览模式")
    parser.add_argument("--reset-config", action="store_true", help="重置本地配置")
    args = parser.parse_args(argv)

    _setup_logging()
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("NexHub")
    app.setApplicationVersion(VERSION)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setWindowIcon(build_app_icon())

    instance = SingleInstance()
    if instance.already_running:
        instance.notify_existing()
        instance.close()
        return 0

    config = AppConfig.load()
    if args.reset_config:
        config = AppConfig()
        config.save()
    auto_login_result = None
    if args.demo:
        config.enter_demo()
    else:
        auto_login_result = _try_auto_login(config)

    theme = ThemeManager(app, config)
    ctx = AppContext(
        config,
        theme,
        server_payload=(auto_login_result or {}).get("server"),
    )
    window = MainWindow(ctx)
    instance.show_requested.connect(window.restore_from_tray)
    window.show()
    if instance.consume_pending_show():
        window.restore_from_tray()

    app.aboutToQuit.connect(ctx.shutdown)
    app.aboutToQuit.connect(instance.close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
