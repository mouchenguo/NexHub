"""离屏测试文件浏览/选择性下载弹窗。"""
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
DATA = PROJECT_ROOT / ".tmp" / "ui_download_data"
TARGET = PROJECT_ROOT / ".tmp" / "ui_download_target"
SOURCE = PROJECT_ROOT / ".tmp" / "ui_download_source"
for directory in (DATA, SOURCE):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
if TARGET.exists():
    shutil.rmtree(TARGET)
(SOURCE / "demo.txt").write_text("NexHub UI download test\n", encoding="utf-8")
os.environ["NEXHUB_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(CLIENT_ROOT))

from PyQt6.QtGui import QFont, QFontDatabase  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from nexhub.config import AppConfig  # noqa: E402
from nexhub.context import AppContext  # noqa: E402
from nexhub.theme import ThemeManager  # noqa: E402
from nexhub.widgets.file_browser import FileBrowserDialog  # noqa: E402


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
    config.save()
    theme = ThemeManager(app, config)
    ctx = AppContext(config, theme)
    # 演示数据不再预置，测试时临时创建一个本地演示共享。
    ctx.demo.create_share(
        name="UI 下载测试",
        storage_type="temp",
        manifest=[{"path": "demo.txt", "size": (SOURCE / "demo.txt").stat().st_size}],
        local_root=str(SOURCE),
        description="UI download test",
    )
    share = ctx.demo.list_shares("public")[0]
    manifest = ctx.demo.manifest_for(share["id"])
    dialog = FileBrowserDialog(
        ctx,
        title=share["name"],
        subtitle="UI download test",
        manifest=manifest,
        job_factory=lambda files: ctx.shares.build_download_jobs(share, files),
        storage_label="临时穿透",
    )
    dialog.target_edit.setText(str(TARGET))
    dialog._set_all(True)  # noqa: SLF001
    pump(app, 0.3)
    dialog.show()
    pump(app, 0.4)
    dialog.grab().save(str(PROJECT_ROOT / ".tmp" / "download_dialog.png"))
    dialog._start_download()  # noqa: SLF001
    deadline = time.time() + 10
    while dialog._worker and dialog._worker.isRunning() and time.time() < deadline:  # noqa: SLF001
        pump(app, 0.1)
    pump(app, 0.5)
    files = [path for path in TARGET.rglob("*") if path.is_file()] if TARGET.exists() else []
    print("downloaded files:", [str(path.relative_to(TARGET)) for path in files])
    if not files:
        raise AssertionError("download produced no files")
    total = sum(path.stat().st_size for path in files)
    print("total bytes:", total)
    dialog.close()
    ctx.shutdown()
    print("UI DOWNLOAD OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
