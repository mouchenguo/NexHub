"""客户端更新检查：读取下载站点的 latest.json。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from ..config import AppConfig
from ..paths import VERSION


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    update_available: bool
    download_url: str
    page_url: str
    manifest_url: str
    notes: list[str]
    published_at: str
    sha256: str


def update_manifest_url(config: AppConfig) -> str:
    base = config.server_url.rstrip("/")
    return f"{base}/downloads/latest.json"


def version_key(value: str) -> tuple:
    """比较 v0.1.0-beta / 0.1.2 这类版本号。"""
    text = (value or "").strip().lower().lstrip("v")
    if "-" in text:
        numeric, pre = text.split("-", 1)
    else:
        numeric, pre = text, ""
    numbers = []
    for part in re.split(r"[.\s]+", numeric):
        match = re.match(r"^(\d+)", part)
        if match:
            numbers.append(int(match.group(1)))
    while len(numbers) < 3:
        numbers.append(0)
    pre = pre.strip()
    if not pre:
        pre_key = (1, 0)
    elif pre.startswith("rc"):
        pre_key = (0, 3)
    elif pre.startswith("beta"):
        pre_key = (0, 2)
    elif pre.startswith("alpha"):
        pre_key = (0, 1)
    else:
        pre_key = (0, 0)
    return tuple(numbers[:3]) + pre_key


def check_for_updates(config: AppConfig, timeout: int = 8) -> UpdateInfo:
    manifest_url = update_manifest_url(config)
    response = requests.get(manifest_url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    latest_version = str(data.get("version", "")).strip().lstrip("v")
    if not latest_version:
        raise ValueError("更新文件缺少 version 字段")
    raw_download = str(data.get("download_url") or "").strip()
    raw_page = str(data.get("page_url") or "./").strip()
    download_url = urljoin(manifest_url, raw_download) if raw_download else ""
    page_url = urljoin(manifest_url, raw_page)
    notes = data.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    return UpdateInfo(
        current_version=VERSION,
        latest_version=latest_version,
        update_available=version_key(latest_version) > version_key(VERSION),
        download_url=download_url,
        page_url=page_url,
        manifest_url=manifest_url,
        notes=[str(item) for item in notes],
        published_at=str(data.get("published_at") or ""),
        sha256=str(data.get("sha256") or ""),
    )
