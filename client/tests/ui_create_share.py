"""离屏测试发布共享对话框（选择目录 → 扫描 → 发布）。"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
DATA = PROJECT_ROOT / ".tmp" / "ui_create_data"
SOURCE = PROJECT_ROOT / ".tmp" / "ui_create_source"
for directory in (DATA, SOURCE):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
(SOURCE / "docs").mkdir(exist_ok=True)
(SOURCE / "README.md").write_text("# UI create test\n", encoding="utf-8")
(SOURCE / "docs" / "notes.txt").write_text("NexHub create share UI", encoding="utf-8")
os.environ["NEXHUB_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(CLIENT_ROOT))

from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from nexhub.config import AppConfig  # noqa: E402
from nexhub.context import AppContext  # noqa: E402
from nexhub.theme import ThemeManager  # noqa: E402
from nexhub.views.my_shares import CreateShareDialog  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    app = QApplication([])
    for font_file in ("msyh.ttc", "segoeui.ttf"):
        path = Path("C:/Windows/Fonts") / font_file
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    app.setFont(QFont("Microsoft YaHei UI", 9))

    config = AppConfig()
    config.demo_mode = True
    config.username = "demo"
    config.display_name = "本地体验用户"
    config.local_api_port = 18769
    config.save()
    theme = ThemeManager(app, config)
    ctx = AppContext(config, theme)

    dialog = CreateShareDialog(ctx)
    dialog.path_edit.setText(str(SOURCE))
    dialog.description_edit.setText("UI 自动化测试共享")
    dialog._scan_path()  # noqa: SLF001
    deadline = time.time() + 8
    while dialog._scan_worker and dialog._scan_worker.isRunning() and time.time() < deadline:  # noqa: SLF001
        pump(app, 0.1)
    pump(app, 0.3)
    dialog.show()
    pump(app, 0.3)
    print("scan summary:", dialog.scan_summary.text())
    if not dialog._manifest:  # noqa: SLF001
        raise AssertionError("scan failed")
    dialog.grab().save(str(PROJECT_ROOT / ".tmp" / "create_share_before.png"))
    dialog._publish()  # noqa: SLF001
    deadline = time.time() + 12
    while (
        dialog._create_worker
        and dialog._create_worker.isRunning()
        and time.time() < deadline
    ):  # noqa: SLF001
        pump(app, 0.1)
    pump(app, 0.6)
    dialog.grab().save(str(PROJECT_ROOT / ".tmp" / "create_share_after.png"))
    print("result:", dialog.result_label.text())
    shares = ctx.demo.list_shares("mine")
    if not any(share["name"] == SOURCE.name for share in shares):
        raise AssertionError("share not created")
    print("CREATE SHARE UI OK")
    dialog.close()
    ctx.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
