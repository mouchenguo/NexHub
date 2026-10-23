"""插件广场远程清单、断点续传下载与 zip 安全安装。"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin

import requests

from ..paths import user_plugins_dir
from .plugin_manager import PluginRecord

logger = logging.getLogger("nexhub.plugins")

Progress = Callable[[int, int, str], None]


def normalize_market_url(raw: str) -> str:
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    if not value.lower().startswith(("http://", "https://")):
        value = "http://" + value
    return value


@dataclass(frozen=True)
class MarketPlugin:
    plugin_id: str
    name: str
    version: str
    description: str
    requirements: str
    download_url: str
    sha256: str = ""
    size: int = 0
    category: str = "其他"
    glyph: str = "✦"
    icon_url: str = ""

    @classmethod
    def from_dict(cls, data: dict, base_url: str) -> "MarketPlugin":
        plugin_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        version = str(data.get("version") or "").strip()
        if not plugin_id or not name or not version:
            raise ValueError("清单项缺少 id/name/version")
        raw_download = str(
            data.get("download_url") or data.get("package_url") or ""
        ).strip()
        if raw_download.startswith(("http://", "https://")):
            download_url = raw_download
        elif raw_download.startswith("/"):
            download_url = urljoin(base_url, raw_download)
        else:
            download_url = urljoin(base_url.rstrip("/") + "/", raw_download)
        icon = str(data.get("icon_url") or data.get("icon") or "").strip()
        icon_url = urljoin(base_url.rstrip("/") + "/", icon) if icon else ""
        return cls(
            plugin_id=plugin_id,
            name=name,
            version=version,
            description=str(data.get("description") or "").strip(),
            requirements=str(data.get("requirements") or "").strip(),
            download_url=download_url,
            sha256=str(data.get("sha256") or "").strip().upper(),
            size=int(data.get("size") or 0),
            category=str(data.get("category") or "其他").strip() or "其他",
            glyph=str(data.get("glyph") or "✦").strip() or "✦",
            icon_url=icon_url,
        )


def fetch_market_plugins(url: str, timeout: int = 15) -> list[MarketPlugin]:
    endpoint = normalize_market_url(url)
    if not endpoint:
        raise ValueError("插件广场 API 地址为空")
    response = requests.get(
        endpoint,
        timeout=timeout,
        headers={"User-Agent": "NexHub-Plugin-Market/1.0", "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    raw_items = payload.get("plugins") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ValueError("插件清单格式无效：需要 plugins 数组")
    items: list[MarketPlugin] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            items.append(MarketPlugin.from_dict(raw, endpoint))
    return items


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.infolist():
        name = member.filename.replace("\\", "/")
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"插件包包含非法路径：{member.filename}")
        destination = (root / name).resolve()
        if root not in destination.parents and destination != root:
            raise ValueError(f"插件包路径越界：{member.filename}")
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(member) as source, destination.open("wb") as output:
            shutil.copyfileobj(source, output)


def _find_package_root(temp: Path) -> Path:
    if (temp / "manifest.json").is_file():
        return temp
    children = [p for p in temp.iterdir() if p.is_dir() and not p.name.startswith(".")]
    candidates = [p for p in children if (p / "manifest.json").is_file()]
    if len(candidates) == 1:
        return candidates[0]
    raise ValueError("插件包必须包含唯一的插件文件夹和 manifest.json")


def download_package(
    url: str,
    target: Path,
    expected_sha256: str = "",
    progress: Progress | None = None,
) -> Path:
    """下载插件包，支持 .part 断点续传并校验 SHA-256。"""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    existing = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": "NexHub-Plugin-Market/1.0"}
    if existing:
        headers["Range"] = f"bytes={existing}-"
    with requests.get(url, headers=headers, stream=True, timeout=(15, 300)) as response:
        if response.status_code == 416 and existing:
            part.unlink(missing_ok=True)
            existing = 0
            response.close()
            return download_package(url, target, expected_sha256, progress)
        response.raise_for_status()
        append = existing > 0 and response.status_code == 206
        if not append:
            existing = 0
        total = existing + int(response.headers.get("content-length") or 0)
        if progress:
            progress(existing, total, "下载插件包")
        with part.open("ab" if append else "wb") as handle:
            done = existing
            for chunk in response.iter_content(1024 * 256):
                if not chunk:
                    continue
                handle.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total, "下载插件包")
    digest = hashlib.sha256()
    with part.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest().upper()
    expected = (expected_sha256 or "").strip().upper()
    if expected and actual != expected:
        part.unlink(missing_ok=True)
        raise ValueError(f"插件包 SHA-256 校验失败：{actual[:12]}…")
    os.replace(part, target)
    return target



def install_zip(
    zip_path: Path,
    expected_id: str = "",
    progress: Progress | None = None,
) -> PluginRecord:
    """校验 zip 并安装到 plugins/<id>；调用方应先停止正在运行的插件。"""
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"插件包不存在：{zip_path}")
    plugins_dir = user_plugins_dir()
    plugins_dir.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(0, 100, "校验插件包")
    temp = Path(tempfile.mkdtemp(prefix="nexhub-plugin-", dir=str(plugins_dir.parent)))
    try:
        try:
            with zipfile.ZipFile(zip_path) as archive:
                bad = archive.testzip()
                if bad:
                    raise ValueError(f"插件包损坏：{bad}")
                _safe_extract(archive, temp)
        except zipfile.BadZipFile as exc:
            raise ValueError("插件包不是有效的 ZIP 文件") from exc
        if progress:
            progress(25, 100, "解压插件包")
        root = _find_package_root(temp)
        manifest_payload = json.loads(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest_id = str(manifest_payload.get("id") or "").strip()
        if expected_id and manifest_id != expected_id:
            raise ValueError(
                f"插件包 ID 不匹配：期望 {expected_id}，实际 {manifest_id}"
            )
        if not manifest_id:
            raise ValueError("插件包 manifest 缺少 id")
        target = plugins_dir / manifest_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root, target)
        record = PluginRecord.from_folder(target)
        if progress:
            progress(100, 100, "安装完成")
        logger.info("installed plugin %s from %s", record.plugin_id, zip_path)
        return record
    except Exception:
        logger.exception("plugin install failed: %s", zip_path)
        raise
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def uninstall_plugin(plugin_id: str) -> None:
    if not plugin_id or "/" in plugin_id or "\\" in plugin_id or ".." in plugin_id:
        raise ValueError("插件 ID 非法")
    target = user_plugins_dir() / plugin_id
    if target.exists():
        shutil.rmtree(target)
        logger.info("uninstalled plugin %s", plugin_id)

