"""离屏 GUI 冒烟测试（开发用）。

运行：
    python tests/smoke_gui.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
DATA_DIR = PROJECT_ROOT / ".tmp" / "gui_data"
SHOT_DIR = PROJECT_ROOT / ".tmp" / "screenshots"
os.environ.setdefault("NEXHUB_DATA_DIR", str(DATA_DIR))
os.environ.setdefault("NEXHUB_NPC_AUTO_INSTALL", "0")
sys.path.insert(0, str(CLIENT_ROOT))

from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from nexhub.app import MainWindow  # noqa: E402
from nexhub.config import AppConfig  # noqa: E402
from nexhub.context import AppContext  # noqa: E402
from nexhub.theme import ThemeManager  # noqa: E402


def pump(app: QApplication, seconds: float = 0.8) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    for font_file in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "seguisb.ttf"):
        path = Path("C:/Windows/Fonts") / font_file
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 9))

    config = AppConfig.load()
    config.demo_mode = True
    config.username = "demo"
    config.display_name = "本地体验用户"
    config.theme = "dark"
    config.save()

    theme = ThemeManager(app, config)
    ctx = AppContext(config, theme)
    window = MainWindow(ctx)
    window.resize(1360, 880)
    window.show()
    window.navigationInterface.expand(useAni=False)
    pump(app, 2.0)

    pages = [
        "dashboard",
        "my_shares",
        "square",
        "rdp",
        "toolbox",
        "settings",
    ]
    for page in pages:
        window.navigate(page)
        pump(app, 0.9)
        window.grab().save(str(SHOT_DIR / f"{page}_dark.png"))
        print("captured", page, "dark")

    theme.set_theme("light")
    pump(app, 1.0)
    for page in ("dashboard", "settings"):
        window.navigate(page)
        pump(app, 0.6)
        window.grab().save(str(SHOT_DIR / f"{page}_light.png"))
        print("captured", page, "light")

    window._show_account_panel()
    pump(app, 0.5)
    print(
        "embedded account panel:",
        window.account_panel.isVisible()
        and window.account_panel.stack.currentWidget() is window.account_panel.login_page,
    )
    window.account_panel.auth_segment.setCurrentItem("register")
    pump(app, 0.3)
    print(
        "register form:",
        window.account_panel.auth_stack.currentWidget()
        is window.account_panel.register_form
        and bool(window.account_panel.register_username_edit.placeholderText()),
    )
    window.grab().save(str(SHOT_DIR / "account_register_light.png"))
    window.account_panel.auth_segment.setCurrentItem("login")
    window.account_panel.close_panel()
    pump(app, 0.4)
    print("pages:", sorted(window._pages))
    print("account nav removed:", "account" not in window._pages)
    print("user card:", bool(getattr(window, "user_card", None)))
    print("nps tunnel panel:", bool(getattr(window._pages.get("settings"), "nps_tunnel_panel", None)))
    settings_page = window._pages.get("settings")
    print("settings folder rows:", len(settings_page._folders) if settings_page else 0)
    print("embedded:", ctx.embedded.running)

    from nexhub.views.my_shares import CreateShareDialog
    from nexhub.views.rdp import RdpHostDialog

    rdp_dialog = RdpHostDialog(ctx, window)
    print("rdp dialog:", bool(rdp_dialog.name_edit.placeholderText()))
    share_dialog = CreateShareDialog(ctx, window)
    print("demo OSS locked:", not share_dialog.oss_radio.isEnabled())
    # 演示模式只锁定 OSS；这里仅验证两个单选框的互斥关系。
    share_dialog.oss_radio.setEnabled(True)
    share_dialog.oss_card.setEnabled(True)
    share_dialog.oss_radio.setChecked(True)
    exclusive = (
        share_dialog.oss_radio.isChecked()
        and not share_dialog.temp_radio.isChecked()
    )
    share_dialog.temp_radio.setChecked(True)
    exclusive = (
        exclusive
        and share_dialog.temp_radio.isChecked()
        and not share_dialog.oss_radio.isChecked()
    )
    print("storage modes exclusive:", exclusive)
    rdp_dialog.deleteLater()
    share_dialog.deleteLater()
    ctx.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
