"""客户端业务层 ↔ ECS 后端联调测试。

会自动启动临时服务端（随机端口）并完整验证：
登录 → OSS 上传/下载 → 临时穿透下载 → 远程主机代理浏览/下载。
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
SERVER_ROOT = PROJECT_ROOT / "server"
TMP_ROOT = PROJECT_ROOT / ".tmp"
CLIENT_DATA = TMP_ROOT / "live_client_data"
SERVER_DATA = TMP_ROOT / "live_server_data"
for directory in (CLIENT_DATA, SERVER_DATA):
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True, exist_ok=True)
os.environ["NEXHUB_DATA_DIR"] = str(CLIENT_DATA)
sys.path.insert(0, str(CLIENT_ROOT))

import requests  # noqa: E402

from nexhub.api_client import ApiClient  # noqa: E402
from nexhub.config import AppConfig  # noqa: E402
from nexhub.embedded_server import EmbeddedServer  # noqa: E402
from nexhub.services.demo_store import DemoStore  # noqa: E402
from nexhub.services.device_service import DeviceService  # noqa: E402
from nexhub.services.download_service import DownloadService  # noqa: E402
from nexhub.services.rdp_service import RdpService  # noqa: E402
from nexhub.services.share_service import ShareService  # noqa: E402


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_url(url: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=1).status_code < 500:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"服务未就绪：{url}")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def main() -> int:
    server_port = free_port()
    local_port = free_port()
    env = os.environ.copy()
    env["NEXHUB_DATA_DIR"] = str(SERVER_DATA)
    env["NEXHUB_DB_PATH"] = str(SERVER_DATA / "live.db")
    env["NEXHUB_ADMIN_PASSWORD"] = "Admin@12345"
    env["NEXHUB_DEMO_PASSWORD"] = "demo1234"
    # 本测试没有真实 NPS，使用客户端提供的本地地址，避免调用 NPS API。
    env["NPS_AUTO_TUNNEL"] = "0"
    server = subprocess.Popen(
        [
            sys.executable,
            str(SERVER_ROOT / "run.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(server_port),
        ],
        cwd=str(SERVER_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    embedded = EmbeddedServer(local_port)
    try:
        base = f"http://127.0.0.1:{server_port}"
        wait_url(f"{base}/api/health")
        check(True, "临时 ECS 服务启动")

        config = AppConfig()
        config.server_url = base
        config.demo_mode = False
        api = ApiClient(config)
        health = api.health()
        check(
            health.get("api_version") == 1
            and "auto_tunnel" in health.get("features", [])
            and "profile_update" in health.get("features", []),
            "服务端健康检查声明兼容信息",
        )
        login = api.login("demo", "demo1234")
        check(
            login.get("server", {}).get("api_version") == 1,
            "登录响应包含服务端兼容信息",
        )
        config.token = login["token"]
        config.username = "demo"
        config.display_name = "演示用户"
        config.save()
        check(bool(config.token), "客户端 API 登录")

        registered = api.register("live_register", "live1234", "联调注册用户")
        check(
            registered.get("status") == "approved"
            and registered.get("auto_approved") is True
            and bool(registered.get("token")),
            "客户端 API 注册（自动审核通过）",
        )
        demo_token = config.token
        config.token = registered["token"]
        profile = api.me().get("user", {})
        check(
            profile.get("username") == "live_register"
            and profile.get("status") == "approved",
            "注册账号凭证可访问个人资料",
        )
        renamed = api.update_profile("联调昵称")
        check(
            renamed.get("user", {}).get("display_name") == "联调昵称"
            and api.me().get("user", {}).get("display_name") == "联调昵称",
            "客户端修改昵称",
        )
        api.update_profile("联调注册用户")
        config.token = demo_token

        devices = DeviceService(config, api)
        devices.start()
        time.sleep(1.2)
        online_devices = devices.list_online(include_self=True)
        check(
            any(item.get("device_id") == config.device_id for item in online_devices),
            "同账号在线设备心跳",
        )
        check(
            not any(
                item.get("device_id") == config.device_id
                for item in devices.list_online()
            ),
            "在线设备列表排除当前设备",
        )
        devices.stop()
        check(
            not any(
                item.get("device_id") == config.device_id
                for item in devices.list_online(include_self=True)
            ),
            "设备离线后从在线列表移除",
        )

        avatar_file = CLIENT_DATA / "avatar.png"
        avatar_file.write_bytes(
            base64.b64decode(
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
            )
        )
        uploaded_avatar = api.upload_avatar(str(avatar_file))
        check(uploaded_avatar.get("user", {}).get("has_avatar") is True, "客户端上传头像")
        check(api.fetch_avatar().startswith(b"\x89PNG"), "客户端读取头像")

        demo = DemoStore(config)
        downloads = DownloadService(config, api, demo)
        shares = ShareService(config, api, demo, embedded, downloads)
        rdp = RdpService(config)

        source = CLIENT_DATA / "共享素材"
        source.mkdir(parents=True, exist_ok=True)
        (source / "hello.txt").write_text("hello from client", encoding="utf-8")
        (source / "nested").mkdir(exist_ok=True)
        (source / "nested" / "data.csv").write_text("a,b\n1,2\n", encoding="utf-8")

        progress = []
        oss_share = shares.create_share(
            str(source), "oss", "integration", progress=lambda p, m: progress.append((p, m))
        )
        check(oss_share["storage_type"] == "oss", "客户端发布 OSS 共享")
        check(progress[-1][0] == 100, "OSS 上传进度到达 100%")
        manifest = shares.manifest(oss_share)
        check(len(manifest["files"]) == 2, "客户端获取 manifest")

        url = api.share_download_url(oss_share["id"], "nested/data.csv")
        response = requests.get(
            url, headers={"Authorization": f"Bearer {config.token}"}, timeout=20
        )
        check(response.status_code == 200 and b"a,b" in response.content, "客户端下载 OSS 文件")

        temp_share = shares.create_share(str(source), "temp", "temp integration")
        check(temp_share["storage_type"] == "temp" and embedded.running, "客户端发布临时穿透共享")
        url = api.share_download_url(temp_share["id"], "hello.txt")
        response = requests.get(
            url, headers={"Authorization": f"Bearer {config.token}"}, timeout=20
        )
        check(
            response.status_code == 200 and response.content == b"hello from client",
            "经 ECS 代理临时穿透下载",
        )

        single_share = shares.create_share(
            str(source / "hello.txt"), "temp", "single file integration"
        )
        url = api.share_download_url(single_share["id"], "hello.txt")
        response = requests.get(
            url, headers={"Authorization": f"Bearer {config.token}"}, timeout=20
        )
        check(
            response.status_code == 200 and response.content == b"hello from client",
            "单文件临时穿透下载",
        )
        shares.cancel_share(single_share)

        rdp_host = rdp.save_host(
            host_id=None,
            name="联调 RDP",
            address="127.0.0.1",
            port=3389,
            username="administrator",
            password="rdp-secret",
            notes="integration",
            use_nps=False,
        )
        check("rdp-secret" not in json.dumps(config.to_dict(), ensure_ascii=False), "RDP 密码不落明文")
        rdp.delete_host(rdp_host.id)

        shares.delete_share(oss_share)
        shares.cancel_share(temp_share)
        api.close()
        print("\nALL CLIENT <-> SERVER CHECKS PASSED")
        return 0
    finally:
        embedded.stop()
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
