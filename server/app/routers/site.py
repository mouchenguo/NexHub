"""NexHub 官网主界面与下载页。

站点入口统一挂在 ``/nexhub`` 下；旧的 ``/downloads`` 文件地址继续保留，
方便已经安装的客户端检查更新。
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..common import human_size
from ..config import settings
from ..templating import render

router = APIRouter(tags=["site"])

DOWNLOADS_DIR = settings.base_dir / "downloads"
GITHUB_URL = "https://github.com/mouchenguo/NexHub"


def _read_latest() -> dict:
    """读取下载清单，缺少文件时给出可用的降级内容。"""
    data: dict = {
        "version": settings.version,
        "download_url": f"NexHub-v{settings.version}.exe",
        "server_download_url": "NexHub-Server-Deploy.zip",
        "server_bundle_url": "NexHub-Server-Bundle.zip",
        "published_at": "",
        "notes": [],
        "sha256": "",
        "server_sha256": "",
    }
    manifest = DOWNLOADS_DIR / "latest.json"
    try:
        loaded = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update({key: value for key, value in loaded.items() if value is not None})
    except (OSError, ValueError, TypeError):
        pass
    if not isinstance(data.get("notes"), list):
        data["notes"] = [str(data.get("notes"))]
    data["version"] = str(data.get("version") or settings.version).lstrip("v")
    return data


def _download_href(raw: str) -> tuple[str, bool]:
    """返回页面下载地址和文件是否存在于本机下载目录。"""
    value = str(raw or "").strip()
    if not value:
        return "", False
    if value.startswith(("http://", "https://", "/")):
        return value, True
    name = Path(value).name
    return f"/nexhub/downloads/{quote(name)}", (DOWNLOADS_DIR / name).is_file()


def _file_size(raw: str) -> str:
    value = str(raw or "").strip()
    if not value or value.startswith(("http://", "https://", "/")):
        return ""
    path = DOWNLOADS_DIR / Path(value).name
    try:
        return human_size(path.stat().st_size)
    except OSError:
        return ""


def _download_items(latest: dict) -> list[dict]:
    client_href, client_ready = _download_href(latest.get("download_url", ""))
    server_href, server_ready = _download_href(latest.get("server_download_url", ""))
    bundle_href, bundle_ready = _download_href(latest.get("server_bundle_url", ""))
    return [
        {
            "tag": "WINDOWS CLIENT",
            "title": f"NexHub-v{latest['version']}.exe",
            "description": "Windows 10 / 11 x64 单文件客户端，下载后直接运行。",
            "href": client_href or f"/nexhub/downloads/NexHub-v{latest['version']}.exe",
            "available": client_ready,
            "primary": True,
            "button": "下载 Windows 客户端",
            "size": _file_size(latest.get("download_url", "")),
        },
        {
            "tag": "SERVER DEPLOY",
            "title": "NexHub-Server-Deploy.zip",
            "description": "服务端一键部署包，包含安装脚本和程序包。",
            "href": server_href or "/nexhub/downloads/NexHub-Server-Deploy.zip",
            "available": server_ready,
            "primary": False,
            "button": "下载部署包",
            "size": _file_size(latest.get("server_download_url", "")),
        },
        {
            "tag": "SERVER BUNDLE",
            "title": "NexHub-Server-Bundle.zip",
            "description": "仅服务端程序包，适合已有服务的手动升级。",
            "href": bundle_href or "/nexhub/downloads/NexHub-Server-Bundle.zip",
            "available": bundle_ready,
            "primary": False,
            "button": "下载程序包",
            "size": _file_size(latest.get("server_bundle_url", "")),
        },
    ]


def _serve_download(filename: str, media_type: str | None = None) -> FileResponse:
    name = Path(filename).name
    if name != filename or name in (".", "..") or name.startswith("."):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = DOWNLOADS_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(path, filename=name, media_type=media_type)


@router.get("/", include_in_schema=False)
def site_root():
    return RedirectResponse("/nexhub", status_code=307)


@router.get("/nexhub")
def site_home(request: Request):
    latest = _read_latest()
    return render(
        request,
        "nexhub_home.html",
        {
            "latest": latest,
            "plugin_market_url": settings.plugin_market_url,
            "github_url": GITHUB_URL,
            "github_release_url": f"{GITHUB_URL}/releases/tag/v{latest['version']}",
        },
    )


@router.get("/nexhub/", include_in_schema=False)
def site_home_slash():
    return RedirectResponse("/nexhub", status_code=307)


@router.get("/nexhub/downloads")
def downloads_page(request: Request):
    latest = _read_latest()
    return render(
        request,
        "nexhub_downloads.html",
        {
            "latest": latest,
            "download_items": _download_items(latest),
            "plugin_market_url": settings.plugin_market_url,
            "github_url": GITHUB_URL,
            "github_release_url": f"{GITHUB_URL}/releases/tag/v{latest['version']}",
        },
    )


@router.get("/nexhub/downloads/", include_in_schema=False)
def downloads_page_slash():
    return RedirectResponse("/nexhub/downloads", status_code=307)


@router.get("/nexhub/download", include_in_schema=False)
def downloads_page_alias():
    return RedirectResponse("/nexhub/downloads", status_code=307)


@router.get("/nexhub/downloads/latest.json")
def downloads_manifest():
    return _serve_download("latest.json", "application/json")


@router.get("/nexhub/downloads/{filename}")
def downloads_file(filename: str):
    return _serve_download(filename)


@router.get("/download", include_in_schema=False)
def legacy_download():
    return RedirectResponse("/nexhub/downloads", status_code=307)


@router.get("/downloads", include_in_schema=False)
def legacy_downloads():
    return RedirectResponse("/nexhub/downloads", status_code=307)


@router.get("/downloads/", include_in_schema=False)
def legacy_downloads_slash():
    return RedirectResponse("/nexhub/downloads", status_code=307)


@router.get("/downloads/latest.json")
def legacy_manifest():
    return _serve_download("latest.json", "application/json")


@router.get("/downloads/{filename}")
def legacy_file(filename: str):
    return _serve_download(filename)

