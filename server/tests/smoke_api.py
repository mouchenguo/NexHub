"""服务端 API / 管理后台冒烟测试（开发用）。

运行：
    python tests/smoke_api.py
"""
from __future__ import annotations

import base64
import os
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import unquote

SERVER_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SERVER_ROOT.parent
DATA_DIR = PROJECT_ROOT / ".tmp" / "server_test_data"
if DATA_DIR.exists():
    shutil.rmtree(DATA_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ["NEXHUB_DATA_DIR"] = str(DATA_DIR)
os.environ["NEXHUB_DB_PATH"] = str(DATA_DIR / "test.db")
os.environ["NEXHUB_WEB_BASE"] = ""
os.environ["NEXHUB_ADMIN_PASSWORD"] = "Admin@12345"
os.environ["NEXHUB_DEMO_PASSWORD"] = "demo1234"
sys.path.insert(0, str(SERVER_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, restart_service, update_apply, update_service  # noqa: E402
from app.main import app  # noqa: E402
from app.oss_service import derive_oss_endpoints  # noqa: E402
from app.routers import admin as admin_router  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    with TestClient(app, follow_redirects=False) as client:
        health = client.get("/api/health")
        check(health.status_code == 200 and health.json()["status"] == "ok", "健康检查")
        health_payload = health.json()
        check(
            health_payload.get("api_version") == 1
            and health_payload.get("min_client_version")
            and "auto_tunnel" in health_payload.get("features", [])
            and "profile_update" in health_payload.get("features", []),
            "健康检查声明协议版本与能力",
        )
        check(
            health.headers.get("X-NexHub-Api-Version") == "1"
            and health.headers.get("X-NexHub-Min-Client-Version"),
            "响应头声明协议版本",
        )

        login = client.post("/api/auth/login", json={"username": "demo", "password": "demo1234"})
        check(login.status_code == 200, "演示账号登录")
        check(
            login.json().get("server", {}).get("api_version") == 1,
            "登录响应包含服务端兼容信息",
        )
        demo_headers = auth(login.json()["token"])

        old_client_login = client.post(
            "/api/auth/login",
            json={"username": "demo", "password": "demo1234"},
            headers={"X-NexHub-Client-Version": "0.0.1"},
        )
        check(old_client_login.status_code == 426, "明确上报过低客户端版本时拒绝登录")
        old_client_api = client.get(
            "/api/auth/me",
            headers={**demo_headers, "X-NexHub-Client-Version": "0.0.1"},
        )
        check(old_client_api.status_code == 426, "过低客户端版本不能调用鉴权接口")
        versioned_login = client.post(
            "/api/auth/login",
            json={"username": "demo", "password": "demo1234"},
            headers={"X-NexHub-Client-Version": "0.1.2-beta"},
        )
        check(versioned_login.status_code == 200, "当前版本客户端可正常登录")

        register = client.post(
            "/api/auth/register",
            json={"username": "tester", "password": "test1234", "display_name": "测试用户"},
        )
        check(
            register.status_code == 200
            and register.json()["status"] == "approved"
            and register.json()["auto_approved"]
            and register.json()["token"],
            "注册新用户（自动审核通过）",
        )
        test_headers = auth(register.json()["token"])

        nickname_login = client.post(
            "/api/auth/login", json={"username": "演示用户", "password": "demo1234"}
        )
        check(
            nickname_login.status_code == 200
            and nickname_login.json()["user"]["username"] == "demo",
            "支持使用昵称登录",
        )
        tester_nickname_login = client.post(
            "/api/auth/login", json={"username": "测试用户", "password": "test1234"}
        )
        check(
            tester_nickname_login.status_code == 200
            and tester_nickname_login.json()["user"]["username"] == "tester",
            "中文昵称登录",
        )
        unicode_nickname = "Café"
        unicode_register = client.post(
            "/api/auth/register",
            json={
                "username": "unicode_user",
                "password": "unicode123",
                "display_name": unicode_nickname,
            },
        )
        unicode_login = client.post(
            "/api/auth/login",
            json={
                "username": unicodedata.normalize("NFD", unicode_nickname),
                "password": "unicode123",
            },
        )
        check(
            unicode_register.status_code == 200
            and unicode_login.status_code == 200
            and unicode_login.json()["user"]["username"] == "unicode_user",
            "Unicode NFC/NFD 昵称登录",
        )
        duplicate = client.post(
            "/api/auth/register",
            json={
                "username": "tester_dup",
                "password": "test1234",
                "display_name": "测试用户",
            },
        )
        ambiguous = client.post(
            "/api/auth/login", json={"username": "测试用户", "password": "test1234"}
        )
        check(
            duplicate.status_code == 200 and ambiguous.status_code == 409,
            "重复昵称登录时提示使用用户名",
        )

        db.set_setting("registration_auto_approve", "0")
        pending_register = client.post(
            "/api/auth/register",
            json={"username": "pending_user", "password": "pending123", "display_name": "待审用户"},
        )
        pending_payload = pending_register.json()
        check(
            pending_register.status_code == 200
            and pending_payload["status"] == "pending"
            and pending_payload["requires_review"]
            and not pending_payload["token"],
            "关闭自动审核后注册进入待审核",
        )
        pending_id = pending_payload["user"]["id"]
        pending_login = client.post(
            "/api/auth/login", json={"username": "pending_user", "password": "pending123"}
        )
        check(pending_login.status_code == 403, "待审核账号禁止登录")

        rejected_register = client.post(
            "/api/auth/register",
            json={"username": "rejected_user", "password": "rejected123", "display_name": "拒绝用户"},
        )
        rejected_id = rejected_register.json()["user"]["id"]
        db.set_setting("registration_auto_approve", "1")
        check(
            rejected_register.json()["status"] == "pending",
            "人工审核模式下所有新账号待审",
        )

        avatar_png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        avatar_upload = client.post(
            "/api/account/avatar",
            files={"file": ("avatar.png", avatar_png, "image/png")},
            headers=demo_headers,
        )
        check(
            avatar_upload.status_code == 200
            and avatar_upload.json()["user"]["has_avatar"],
            "上传本地头像",
        )
        avatar_get = client.get("/api/account/avatar", headers=demo_headers)
        check(
            avatar_get.status_code == 200
            and avatar_get.headers.get("content-type", "").startswith("image/png")
            and "inline" in avatar_get.headers.get("content-disposition", ""),
            "头像以内联图片返回",
        )
        check(
            client.get("/api/account/avatar").status_code == 401,
            "头像接口需要鉴权",
        )

        nickname = client.patch(
            "/api/auth/profile",
            json={"display_name": "  自动化昵称  "},
            headers=demo_headers,
        )
        check(
            nickname.status_code == 200
            and nickname.json()["user"]["display_name"] == "自动化昵称",
            "修改昵称并去除首尾空格",
        )
        me_after_nickname = client.get("/api/auth/me", headers=demo_headers)
        check(
            me_after_nickname.json()["user"]["display_name"] == "自动化昵称",
            "昵称修改后个人资料同步",
        )
        empty_nickname = client.patch(
            "/api/auth/profile",
            json={"display_name": "   "},
            headers=demo_headers,
        )
        check(empty_nickname.status_code == 400, "空昵称被拒绝")
        control_nickname = client.patch(
            "/api/auth/profile",
            json={"display_name": "昵称\n注入"},
            headers=demo_headers,
        )
        check(control_nickname.status_code == 400, "昵称中的控制字符被拒绝")
        long_nickname = client.patch(
            "/api/auth/profile",
            json={"display_name": "x" * 33},
            headers=demo_headers,
        )
        check(long_nickname.status_code == 422, "超长昵称被拒绝")
        check(
            client.patch(
                "/api/auth/profile", json={"display_name": "未登录昵称"}
            ).status_code
            == 401,
            "修改昵称需要登录",
        )
        restored_nickname = client.patch(
            "/api/auth/profile",
            json={"display_name": "演示用户"},
            headers=demo_headers,
        )
        check(restored_nickname.status_code == 200, "恢复演示账号昵称")

        payload = {
            "name": "自动化测试共享",
            "description": "smoke test",
            "storage_type": "oss",
            "local_url": "",
            "files": [
                {"path": "docs/hello.txt", "size": 11},
                {"path": "data.csv", "size": 9},
            ],
        }
        created = client.post("/api/shares", json=payload, headers=demo_headers)
        check(created.status_code == 200, "发布 OSS 共享")
        share = created.json()
        share_id = share["id"]
        check(share["public_url"].endswith(share["token"]), "生成网页分享链接")

        upload1 = client.post(
            f"/api/shares/{share_id}/files",
            params={"path": "docs/hello.txt"},
            content=b"hello world",
            headers={**demo_headers, "Content-Type": "application/octet-stream"},
        )
        check(upload1.status_code == 200, "上传 OSS 文件 1")
        upload2 = client.post(
            f"/api/shares/{share_id}/files",
            params={"path": "data.csv"},
            content=b"a,b\n1,2\n",
            headers={**demo_headers, "Content-Type": "application/octet-stream"},
        )
        check(upload2.status_code == 200, "上传 OSS 文件 2")

        manifest = client.get(f"/api/shares/{share_id}/manifest", headers=demo_headers)
        check(manifest.status_code == 200 and len(manifest.json()["files"]) == 2, "获取 manifest")
        public_manifest = client.get(f"/api/public/shares/{share['token']}/manifest")
        check(public_manifest.status_code == 200, "公开 manifest 无需登录")

        download = client.get(
            f"/api/shares/{share_id}/download",
            params={"path": "docs/hello.txt"},
            headers=demo_headers,
        )
        check(download.status_code == 200 and download.content == b"hello world", "鉴权下载")
        public_download = client.get(
            f"/api/public/shares/{share['token']}/download", params={"path": "data.csv"}
        )
        check(public_download.status_code == 200 and b"a,b" in public_download.content, "公开下载")

        page = client.get(f"/s/{share['token']}")
        check(page.status_code == 200 and "自动化测试共享" in page.text, "网页端分享页")

        mine = client.get("/api/shares", params={"scope": "mine"}, headers=demo_headers)
        check(mine.status_code == 200 and len(mine.json()["items"]) == 1, "我的共享列表")
        square = client.get("/api/shares", params={"scope": "public"}, headers=test_headers)
        check(square.status_code == 200 and len(square.json()["items"]) == 1, "共享广场列表")

        dashboard = client.get("/api/dashboard", headers=demo_headers)
        check(dashboard.status_code == 200 and dashboard.json()["active_shares"] == 1, "仪表盘统计")
        records = client.get("/api/account/downloads", headers=demo_headers)
        check(records.status_code == 200 and len(records.json()["items"]) >= 1, "下载记录")

        db.set_setting("nps_server_addr", "nps.test")
        db.set_setting("nps_server_port", "8024")
        with (
            patch(
                "app.nps_hosts.create_client",
                return_value={"id": 501, "vkey": "test", "remark": "NexHub/tester"},
            ) as nps_create,
            patch("app.routers.tunnels.update_client") as nps_update,
            patch("app.routers.tunnels.set_tunnel_enabled") as nps_state,
            patch("app.nps_hosts.delete_client") as nps_delete,
            patch("app.routers.tunnels.delete_tunnel") as nps_task_delete,
            patch(
                "app.nps_hosts.find_client",
                return_value={"Id": 501, "VerifyKey": "test", "Status": True},
            ),
            patch(
                "app.routers.tunnels.create_tcp_tunnel",
                return_value={"id": 7001, "port": 30011, "target": "127.0.0.1:3389"},
            ) as nps_tcp_create,
            patch(
                "app.routers.tunnels.list_nps_tunnels",
                return_value=[
                    {
                        "Id": 7001,
                        "Port": 30011,
                        "TargetStr": "127.0.0.1:3389",
                        "ClientId": 501,
                    }
                ],
            ),
        ):
            tunnel = client.post(
                "/api/tunnels",
                json={"name": "测试隧道", "local_host": "127.0.0.1", "local_port": 8080},
                headers=test_headers,
            )
            check(tunnel.status_code == 200 and tunnel.json().get("vkey"), "创建 NPS 隧道")
            tunnel_id = tunnel.json()["id"]
            check(nps_create.called, "创建隧道调用 NPS API")
            listed = client.get("/api/tunnels", headers=test_headers)
            check(listed.status_code == 200 and len(listed.json()["items"]) == 1, "NPS 隧道列表")
            edited = client.patch(
                f"/api/tunnels/{tunnel_id}",
                json={"name": "重命名隧道", "local_host": "127.0.0.1", "local_port": 8081},
                headers=test_headers,
            )
            check(edited.status_code == 200 and edited.json()["name"] == "重命名隧道", "编辑 NPS 隧道")
            check(nps_update.called, "编辑隧道同步 NPS 备注")
            stopped = client.post(f"/api/tunnels/{tunnel_id}/stop", headers=test_headers)
            check(stopped.status_code == 200 and not stopped.json()["enabled"], "停用 NPS 隧道")
            started = client.post(f"/api/tunnels/{tunnel_id}/start", headers=test_headers)
            check(started.status_code == 200 and started.json()["enabled"], "启用 NPS 隧道")
            check(nps_state.call_count == 2, "隧道启停调用 NPS API")
            removed = client.delete(f"/api/tunnels/{tunnel_id}", headers=test_headers)
            check(
                removed.status_code == 200
                and nps_task_delete.called
                and not nps_delete.called,
                "删除 NPS 隧道但保留共享主机",
            )

            auto_payload = {
                "name": "RDP-测试设备",
                "local_host": "127.0.0.1",
                "local_port": 3389,
                "rdp_task": True,
            }
            auto_first = client.post(
                "/api/tunnels/auto", json=auto_payload, headers=test_headers
            )
            check(
                auto_first.status_code == 200
                and auto_first.json()["created"]
                and auto_first.json()["tunnel"]["public_address"],
                "一键自动配置创建 NPS 主机与隧道",
            )
            auto_second = client.post(
                "/api/tunnels/auto", json=auto_payload, headers=test_headers
            )
            check(
                auto_second.status_code == 200
                and not auto_second.json()["created"]
                and auto_second.json()["tunnel"]["id"]
                == auto_first.json()["tunnel"]["id"],
                "一键自动配置幂等复用现有主机",
            )
            check(
                nps_create.call_count == 1 and nps_tcp_create.call_count == 2,
                "自动配置复用 NPS 主机与 TCP 隧道",
            )

            other = client.get("/api/tunnels", headers=demo_headers)
            check(other.status_code == 200 and other.json()["items"] == [], "隧道按用户隔离")
            check(
                client.get("/api/tunnels").status_code == 401,
                "隧道接口需要鉴权",
            )

        redirect = client.get("/admin")
        check(
            redirect.status_code == 303 and redirect.headers.get("location") == "/admin/login",
            "后台未登录跳转",
        )

        login_page = client.get("/admin/login")
        check(login_page.status_code == 200, "后台登录页")
        admin_login = client.post(
            "/admin/login",
            data={"username": "admin", "password": "Admin@12345"},
        )
        check(admin_login.status_code == 303, "管理员登录")
        admin_dashboard = client.get("/admin", cookies=admin_login.cookies)
        check(admin_dashboard.status_code == 200 and "仪表盘" in admin_dashboard.text, "后台仪表盘")

        # TestClient 会复用之前的 admin Cookie：临时摘除后再做匿名/权限断言
        admin_cookie = client.cookies.get("nexhub_admin")
        client.cookies.delete("nexhub_admin")
        overview_anon = client.get("/api/admin/overview")
        check(overview_anon.status_code == 401, "管理员概览需要登录")
        overview_demo = client.get("/api/admin/overview", headers=demo_headers)
        check(overview_demo.status_code == 403, "普通用户无法读取管理员概览")
        if admin_cookie:
            client.cookies.set("nexhub_admin", admin_cookie)
        overview = client.get(
            "/api/admin/overview",
            params={"limit": 5},
            cookies=admin_login.cookies,
        )
        payload = overview.json() if overview.status_code == 200 else {}
        usage = payload.get("usage") or {}
        check(
            overview.status_code == 200
            and {
                "oss_used",
                "oss_quota",
                "oss_percent",
                "active_shares",
                "downloads",
                "users",
                "pending_users",
            }
            <= set(usage),
            "管理员概览返回完整用量字段",
        )
        check(len(payload.get("logs") or []) <= 5, "管理员概览日志条数受 limit 限制")
        check(
            str((payload.get("server") or {}).get("web_url") or "").endswith("/admin"),
            "管理员概览提供网页后台地址",
        )
        check(
            int(usage.get("active_shares") or 0) >= 1
            and int(usage.get("downloads") or 0) >= 1,
            "管理员概览统计包含已发布共享与下载",
        )
        for path, keyword in (
            ("/admin/users", "用户管理"),
            ("/admin/shares", "共享资源"),
            ("/admin/oss", "OSS 系统配置"),
            ("/admin/nps", "服务器与 NPS 配置"),
            ("/admin/logs", "全局日志"),
            ("/admin/settings", "系统设置"),
        ):
            response = client.get(path, cookies=admin_login.cookies)
            check(response.status_code == 200 and keyword in response.text, f"后台页面 {path}")
        settings_page = client.get("/admin/settings", cookies=admin_login.cookies)
        check("服务端检查更新" in settings_page.text, "后台显示服务端检查更新入口")

        oss_page = client.get("/admin/oss", cookies=admin_login.cookies)
        check(
            "当前网络" in oss_page.text and "Endpoint" in oss_page.text,
            "后台 OSS 页面展示当前网络与 endpoint",
        )
        check("本地回退存储" in oss_page.text, "未配置 OSS 时后台提示本地回退存储")
        check(
            derive_oss_endpoints("https://oss-cn-hangzhou.aliyuncs.com")
            == (
                "https://oss-cn-hangzhou.aliyuncs.com",
                "https://oss-cn-hangzhou-internal.aliyuncs.com",
            ),
            "OSS 标准 endpoint 自动推导内网地址",
        )
        check(derive_oss_endpoints("") == ("", ""), "未配置 OSS endpoint 时推导为空")
        invalid_network = client.post(
            "/admin/oss/network",
            data={"network": "bogus"},
            cookies=admin_login.cookies,
        )
        check(
            invalid_network.status_code == 303
            and "不合法" in unquote(invalid_network.headers.get("location", "")),
            "后台拒绝非法 OSS 网络类型",
        )
        check(
            client.post("/admin/oss/network", data={"network": "internal"}).status_code == 303,
            "OSS 网络切换需要后台登录",
        )
        db.set_setting("oss_network", "")
        unconfigured_switch = client.post(
            "/admin/oss/network",
            data={"network": "internal"},
            cookies=admin_login.cookies,
        )
        check(
            unconfigured_switch.status_code == 303
            and "切换失败" in unquote(unconfigured_switch.headers.get("location", "")),
            "OSS 未配置时切换网络给出错误提示",
        )
        check(db.get_setting("oss_network", "") == "", "切换失败不写入 oss_network")
        with patch.object(
            admin_router.oss_service,
            "switch_network",
            return_value="https://oss-cn-hangzhou-internal.aliyuncs.com",
        ):
            switched = client.post(
                "/admin/oss/network",
                data={"network": "internal"},
                cookies=admin_login.cookies,
            )
        check(
            switched.status_code == 303
            and "已切换到内网" in unquote(switched.headers.get("location", "")),
            "后台切换到内网 endpoint",
        )
        check(db.get_setting("oss_network", "") == "internal", "切换成功写入 oss_network")
        db.set_setting("oss_network", "")

        release_payload = [
            {
                "tag_name": "v0.1.2-beta",
                "html_url": "https://github.com/mouchenguo/NexHub/releases/tag/v0.1.2-beta",
                "published_at": "2026-10-20T00:00:00Z",
                "body": "旧版本",
                "assets": [],
            },
            {
                "tag_name": "v0.2.0-beta",
                "html_url": "https://github.com/mouchenguo/NexHub/releases/tag/v0.2.0-beta",
                "published_at": "2026-10-23T00:00:00Z",
                "body": "## 更新内容\n- 服务端检查更新\n",
                "assets": [
                    {
                        "name": "NexHub-Server-Deploy.zip",
                        "browser_download_url": "https://github.com/mouchenguo/NexHub/releases/download/v0.2.0-beta/NexHub-Server-Deploy.zip",
                        "digest": "sha256:abc123",
                    }
                ],
            },
            {
                "tag_name": "v0.1.0-beta",
                "html_url": "https://github.com/mouchenguo/NexHub/releases/tag/v0.1.0-beta",
                "published_at": "2026-10-01T00:00:00Z",
                "body": "当前版本",
                "assets": [],
            },
        ]
        fake_response = Mock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = release_payload
        with patch("app.update_service.requests.get", return_value=fake_response):
            parsed_update = update_service.check_for_server_update(
                "0.1.0-beta", "https://example.test/releases"
            )
            parsed_current = update_service.check_for_server_update(
                "0.2.0-beta", "https://example.test/releases"
            )
        check(
            parsed_update.latest_version == "0.2.0-beta"
            and parsed_update.update_available
            and parsed_update.server_download_url.endswith("NexHub-Server-Deploy.zip")
            and parsed_update.server_sha256 == "abc123"
            and "服务端检查更新" in parsed_update.notes,
            "服务端更新检查解析 Release 与预发布版本",
        )
        check(
            not parsed_current.update_available,
            "服务端版本相同时不提示更新",
        )
        fake_update = update_service.ServerUpdateInfo(
            current_version="0.1.0-beta",
            latest_version="0.2.0-beta",
            update_available=True,
            release_url="https://github.com/mouchenguo/NexHub/releases/tag/v0.2.0-beta",
            server_download_url="https://github.com/mouchenguo/NexHub/releases/download/v0.2.0-beta/NexHub-Server-Deploy.zip",
            published_at="2026-10-23 00:00 UTC",
            notes=["服务端检查更新"],
            source="GitHub Releases",
            checked_at="2026-10-23 00:00 UTC",
            latest_tag="v0.2.0-beta",
            server_sha256="abc123",
        )
        with patch("app.routers.admin.check_for_server_update", return_value=fake_update):
            update_page = client.get(
                "/admin/settings?check=1", cookies=admin_login.cookies
            )
        check(
            update_page.status_code == 200
            and "发现新版本" in update_page.text
            and "v0.2.0-beta" in update_page.text
            and "一键更新到 v0.2.0-beta" in update_page.text
            and "下载部署包" in update_page.text
            and "abc123" in update_page.text,
            "后台检查服务端更新并显示一键更新入口",
        )

        mirror_payload = {
            "version": "0.3.0-beta",
            "page_url": "./",
            "server_download_url": "NexHub-Server-Deploy.zip",
            "server_sha256": "def456",
            "published_at": "2026-11-01",
            "notes": ["服务端一键更新"],
        }
        mirror_response = Mock()
        mirror_response.raise_for_status.return_value = None
        mirror_response.json.return_value = mirror_payload
        with patch("app.update_service.requests.get", return_value=mirror_response):
            mirror_info = update_service.check_for_server_update(
                "0.1.0-beta",
                "https://example.test/releases",
                mirror_url="http://47.108.130.177:8000/nexhub/downloads",
            )
        check(
            mirror_info.latest_version == "0.3.0-beta"
            and mirror_info.update_available
            and mirror_info.server_download_url
            == "http://47.108.130.177:8000/nexhub/downloads/NexHub-Server-Deploy.zip"
            and mirror_info.server_sha256 == "def456"
            and "47.108.130.177" in mirror_info.source,
            "检查更新优先读取 47 下载镜像清单",
        )

        update_root = PROJECT_ROOT / ".tmp" / "update_apply_test"
        if update_root.exists():
            shutil.rmtree(update_root)
        bundle_path = update_root / "bundle" / "NexHub-Server-Bundle.zip"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(bundle_path, "w") as archive:
            archive.writestr("run.py", "# new run\n")
            archive.writestr("requirements.txt", "fastapi\n")
            archive.writestr("app/main.py", "# new main\n")
            archive.writestr("app/config.py", 'version: str = "0.3.0-beta"\n')
        deploy_path = update_root / "NexHub-Server-Deploy.zip"
        with zipfile.ZipFile(deploy_path, "w") as archive:
            archive.write(bundle_path, "NexHub-Server-Bundle.zip")

        installed = update_root / "installed"
        (installed / "app").mkdir(parents=True, exist_ok=True)
        (installed / "run.py").write_text("# old run\n", encoding="utf-8")
        (installed / "app" / "main.py").write_text("# old main\n", encoding="utf-8")
        (installed / "app" / "config.py").write_text(
            'version: str = "0.1.0-beta"\n', encoding="utf-8"
        )
        (installed / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
        (installed / ".env").write_text("NEXHUB_KEEP=1\n", encoding="utf-8")

        install_info = update_service.ServerUpdateInfo(
            current_version="0.1.0-beta",
            latest_version="0.3.0-beta",
            update_available=True,
            release_url="http://47.108.130.177:8000/nexhub/downloads/",
            server_download_url="http://47.108.130.177:8000/nexhub/downloads/NexHub-Server-Deploy.zip",
            published_at="2026-11-01",
            notes=["服务端一键更新"],
            source="下载镜像 47.108.130.177",
            checked_at="2026-11-01 00:00 UTC",
            latest_tag="v0.3.0-beta",
            server_sha256=update_apply.sha256_file(deploy_path),
        )

        def fake_download(urls, dest, expected_sha256="", log=None):
            shutil.copy2(deploy_path, dest)
            if expected_sha256 and update_apply.sha256_file(dest) != expected_sha256.upper():
                raise AssertionError("SHA-256 校验未通过")
            return dest, urls[0]

        with patch.object(update_apply, "download_package", fake_download), patch.object(
            update_apply.settings, "update_install_requirements", False
        ):
            applied = update_apply.apply_server_update(install_info, base_dir=installed)

        check(
            "0.3.0-beta" in (installed / "app" / "config.py").read_text(encoding="utf-8")
            and (installed / "run.py").read_text(encoding="utf-8").strip() == "# new run"
            and (installed / ".env").read_text(encoding="utf-8").strip() == "NEXHUB_KEEP=1"
            and Path(applied["backup"], "app", "main.py").is_file(),
            "一键更新替换程序文件、保留 .env 并生成备份",
        )
        check(
            update_apply.read_pending().get("version") == "0.3.0-beta",
            "一键更新写入待重启标记",
        )

        pending_page = client.get("/admin/settings", cookies=admin_login.cookies)
        check(
            pending_page.status_code == 200
            and "待重启" in pending_page.text
            and "立即重启服务端" in pending_page.text
            and "0.3.0-beta" in pending_page.text,
            "后台显示待重启提示与重启按钮",
        )

        with patch.object(
            admin_router,
            "schedule_restart",
            return_value={
                "mode": "systemd",
                "detail": "已请求退出，systemd 将在数秒内自动拉起（nexhub.service）",
                "command": ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"],
            },
        ):
            restart_page = client.post("/admin/settings/restart", cookies=admin_login.cookies)
        check(
            restart_page.status_code == 200
            and "正在重启服务端" in restart_page.text
            and "0.3.0-beta" in restart_page.text,
            "管理员触发重启并显示重启进度页",
        )

        with patch.object(admin_router, "check_for_server_update", return_value=fake_update), patch.object(
            admin_router,
            "apply_server_update",
            return_value={
                "version": "0.2.0-beta",
                "previous_version": "0.1.0-beta",
                "applied_at": "2026-11-11 00:00 UTC",
                "backup": str(update_root / "backups" / "server-0.1.0-beta-1"),
                "source": "http://47.108.130.177:8000/nexhub/downloads/NexHub-Server-Deploy.zip",
                "requirements": "unchanged",
            },
        ):
            apply_response = client.post(
                "/admin/settings/apply-update", data={}, cookies=admin_login.cookies
            )
        apply_location = apply_response.headers.get("location", "")
        check(
            apply_response.status_code == 303
            and "restart=1" in apply_location
            and "0.2.0-beta" in unquote(apply_location),
            "一键更新接口安装后跳转并要求重启",
        )

        bad_zip = update_root / "bad.zip"
        with zipfile.ZipFile(bad_zip, "w") as archive:
            archive.writestr("../evil.txt", "x")
        try:
            update_apply._safe_extract(bad_zip, update_root / "bad")
            rejected = False
        except update_apply.UpdateError:
            rejected = True
        check(rejected, "拒绝越界路径的部署包")

        update_apply.clear_pending()
        check(
            restart_service.strategy() in ("self", "systemd", "command", "manual")
            and restart_service.launch_command()[1].endswith("run.py"),
            "重启策略与启动命令可解析",
        )
        users_page = client.get("/admin/users", cookies=admin_login.cookies)
        check(
            "待审核" in users_page.text and "自动审核已开启" in users_page.text,
            "后台显示注册审核状态",
        )
        approve = client.post(
            f"/admin/users/{pending_id}/review",
            data={"action": "approve"},
            cookies=admin_login.cookies,
        )
        check(approve.status_code == 303, "管理员通过注册审核")
        approved_login = client.post(
            "/api/auth/login", json={"username": "pending_user", "password": "pending123"}
        )
        check(approved_login.status_code == 200, "审核通过后允许登录")
        reject = client.post(
            f"/admin/users/{rejected_id}/review",
            data={"action": "reject"},
            cookies=admin_login.cookies,
        )
        check(reject.status_code == 303, "管理员拒绝注册审核")
        rejected_login = client.post(
            "/api/auth/login", json={"username": "rejected_user", "password": "rejected123"}
        )
        check(rejected_login.status_code == 403, "拒绝后禁止登录")
        settings_update = client.post(
            "/admin/settings",
            data={"web_base": "", "registration_auto_approve": "1"},
            cookies=admin_login.cookies,
        )
        check(
            settings_update.status_code == 303
            and db.get_setting("registration_auto_approve") == "1",
            "后台保存自动审核开关",
        )
        share_logs = client.get(f"/admin/shares/{share_id}/logs", cookies=admin_login.cookies)
        check(share_logs.status_code == 200, "共享访问日志")

        reset = client.post(
            "/admin/users/2/reset-password",
            data={"new_password": "newdemo123"},
            cookies=admin_login.cookies,
        )
        check(reset.status_code == 303, "管理员重置用户密码")
        old_login = client.post(
            "/api/auth/login", json={"username": "demo", "password": "demo1234"}
        )
        new_login = client.post(
            "/api/auth/login", json={"username": "demo", "password": "newdemo123"}
        )
        check(old_login.status_code == 401 and new_login.status_code == 200, "重置后密码生效")

        cancel = client.post(f"/api/shares/{share_id}/cancel", headers=demo_headers)
        check(cancel.status_code == 200, "取消共享")
        invalid_manifest = client.get(f"/api/public/shares/{share['token']}/manifest")
        check(invalid_manifest.status_code == 404, "取消后公开 manifest 失效")

        rotate = client.post(
            "/admin/settings/rotate-secret",
            data={"new_secret": "a-new-secret-with-enough-length"},
            cookies=admin_login.cookies,
        )
        check(rotate.status_code == 303, "轮换后端签名密钥")
        expired = client.get("/admin", cookies=admin_login.cookies)
        check(expired.status_code == 303, "轮换后管理会话失效")

        print("\nALL SERVER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
