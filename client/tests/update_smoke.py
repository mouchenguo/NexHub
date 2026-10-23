"""客户端更新检查、下载和更新脚本冒烟测试。"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
DATA = PROJECT_ROOT / ".tmp" / "update_smoke"
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True)
sys.path.insert(0, str(CLIENT_ROOT))

from nexhub.config import AppConfig  # noqa: E402
from nexhub.services.update_service import (  # noqa: E402
    UpdateInfo,
    build_updater_script,
    check_for_updates,
    download_update,
    version_key,
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def main() -> int:
    check(
        version_key("0.1.2-beta") < version_key("0.1.2") < version_key("1.0.0"),
        "beta 版本比较",
    )
    payload = b"NexHub fake update payload\n"
    digest = hashlib.sha256(payload).hexdigest().upper()
    downloads = DATA / "downloads"
    downloads.mkdir()
    (downloads / "NexHub-test.exe").write_bytes(payload)
    (downloads / "latest.json").write_text(
        json.dumps(
            {
                "version": "9.9.9-beta",
                "download_url": "NexHub-test.exe",
                "page_url": "./",
                "notes": ["自动更新测试"],
                "sha256": digest,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), lambda *args, **kwargs: QuietHandler(*args, directory=str(DATA), **kwargs)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = AppConfig()
        config.server_url = f"http://127.0.0.1:{server.server_address[1]}"
        info = check_for_updates(config, timeout=5)
        check(
            info.update_available
            and info.latest_version == "9.9.9-beta"
            and info.download_url.endswith("/NexHub-test.exe"),
            "读取更新清单",
        )
        downloaded = download_update(info, target_dir=DATA / "downloaded")
        check(
            downloaded.read_bytes() == payload
            and hashlib.sha256(downloaded.read_bytes()).hexdigest().upper() == digest,
            "下载并校验 SHA-256",
        )
        bad_info = UpdateInfo(
            current_version=info.current_version,
            latest_version=info.latest_version,
            update_available=True,
            download_url=info.download_url,
            page_url=info.page_url,
            manifest_url=info.manifest_url,
            notes=[],
            published_at="",
            sha256="0" * 64,
        )
        try:
            download_update(bad_info, target_dir=DATA / "bad")
        except ValueError:
            check(True, "拒绝 SHA-256 不匹配的更新")
        else:
            check(False, "拒绝 SHA-256 不匹配的更新")

        script = build_updater_script(
            downloads / "NexHub-test.exe", DATA / "target.exe", DATA / "install.cmd"
        )
        content = script.read_text(encoding="ascii")
        check(
            "%NEXHUB_UPDATE_SOURCE%" in content
            and "%NEXHUB_UPDATE_TARGET%" in content
            and "copy /Y" in content,
            "生成更新替换脚本",
        )
    finally:
        server.shutdown()
        server.server_close()
    print("ALL UPDATE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
