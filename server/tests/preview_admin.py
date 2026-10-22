"""生成管理后台/公共分享页的本地预览 HTML（内联 CSS，便于无头浏览器截图）。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parents[1]
TMP = SERVER_ROOT.parent / ".tmp"
DATA = TMP / "preview_data"
OUT = TMP / "previews"
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True, exist_ok=True)
OUT.mkdir(parents=True, exist_ok=True)
os.environ["NEXHUB_DATA_DIR"] = str(DATA)
os.environ["NEXHUB_DB_PATH"] = str(DATA / "preview.db")
os.environ["NEXHUB_ADMIN_PASSWORD"] = "Admin@12345"
os.environ["NEXHUB_DEMO_PASSWORD"] = "demo1234"
sys.path.insert(0, str(SERVER_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ADMIN_CSS = (SERVER_ROOT / "app" / "static" / "admin.css").read_text(encoding="utf-8")
PUBLIC_CSS = (SERVER_ROOT / "app" / "static" / "public.css").read_text(encoding="utf-8")


def inline(html: str, css: str) -> str:
    return html.replace(
        '<link rel="stylesheet" href="/static/admin.css">',
        f"<style>{css}</style>",
    ).replace(
        '<link rel="stylesheet" href="/static/public.css">',
        f"<style>{css}</style>",
    ).replace('<script defer src="/static/admin.js"></script>', "")


def save(name: str, html: str) -> None:
    (OUT / f"{name}.html").write_text(html, encoding="utf-8")
    print("saved", name)


def main() -> int:
    with TestClient(app, follow_redirects=False) as client:
        client.post(
            "/api/auth/register",
            json={"username": "linyu", "password": "linyu123", "display_name": "林屿"},
        )
        demo = client.post("/api/auth/login", json={"username": "demo", "password": "demo1234"})
        headers = {"Authorization": f"Bearer {demo.json()['token']}"}
        share = client.post(
            "/api/shares",
            json={
                "name": "Q3 项目交付资料",
                "description": "包含接口说明、演示数据与版本记录，供团队内部下载。",
                "storage_type": "oss",
                "files": [
                    {"path": "README.md", "size": 1240},
                    {"path": "docs/接口说明.md", "size": 76000},
                    {"path": "assets/演示数据.json", "size": 2400000},
                ],
            },
            headers=headers,
        ).json()
        for path, content in (
            ("README.md", b"# Q3 delivery"),
            ("docs/接口说明.md", b"# API docs"),
            ("assets/演示数据.json", b"{}"),
        ):
            client.post(
                f"/api/shares/{share['id']}/files",
                params={"path": path},
                content=content,
                headers={**headers, "Content-Type": "application/octet-stream"},
            )
        login_page = client.get("/admin/login")
        save("admin_login", inline(login_page.text, ADMIN_CSS))
        admin_login = client.post(
            "/admin/login", data={"username": "admin", "password": "Admin@12345"}
        )
        cookies = admin_login.cookies
        for name, path in (
            ("admin_dashboard", "/admin"),
            ("admin_users", "/admin/users"),
            ("admin_shares", "/admin/shares"),
            ("admin_oss", "/admin/oss"),
            ("admin_nps", "/admin/nps"),
            ("admin_logs", "/admin/logs"),
            ("admin_settings", "/admin/settings"),
        ):
            response = client.get(path, cookies=cookies)
            save(name, inline(response.text, ADMIN_CSS))
        public_page = client.get(f"/s/{share['token']}")
        save("public_share", inline(public_page.text, PUBLIC_CSS))
        print("preview ready at", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
