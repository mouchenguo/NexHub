"""服务端更新检查。

管理后台只做版本比较和下载入口，不会自动替换运行中的服务端文件。
默认读取 GitHub Releases；也可通过 ``NEXHUB_UPDATE_FEED`` 换成镜像或
兼容 ``latest.json`` 的更新源。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests

DEFAULT_UPDATE_FEED = "https://api.github.com/repos/mouchenguo/NexHub/releases"
SERVER_PACKAGE_NAME = "NexHub-Server-Deploy.zip"
RELEASES_PER_PAGE = 20


@dataclass
class ServerUpdateInfo:
    current_version: str
    latest_version: str
    update_available: bool
    release_url: str
    server_download_url: str
    published_at: str
    notes: list[str] = field(default_factory=list)
    source: str = "GitHub Releases"
    checked_at: str = ""
    latest_tag: str = ""
    server_sha256: str = ""


def version_key(value: str) -> tuple:
    """比较 v0.1.0-beta / 0.1.1 这类版本号（规则与客户端一致）。"""
    text = (value or "").strip().lower().lstrip("v")
    if "+" in text:
        text = text.split("+", 1)[0]
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


def _clean_version(value: str) -> str:
    return str(value or "").strip().lstrip("v")


def _format_moment(value: str) -> str:
    if not value:
        return "未知"
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _plain_notes(body: str) -> list[str]:
    notes = []
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("<!--"):
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"^[-*+]\s+", "", line)
        line = re.sub(r"^\d+\.\s+", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        line = line.replace("`", "").replace("**", "")
        if not line or set(line) <= {"-", " "}:
            continue
        notes.append(line[:180])
        if len(notes) >= 6:
            break
    return notes


def _release_asset_url(release: dict, name: str) -> tuple[str, str]:
    for asset in release.get("assets") or []:
        if asset.get("name") == name:
            digest = str(asset.get("digest") or "")
            sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
            return str(asset.get("browser_download_url") or ""), sha256
    tag = str(release.get("tag_name") or "")
    release_url = str(release.get("html_url") or "")
    if tag and "/releases/tag/" in release_url:
        base = release_url.split("/releases/tag/", 1)[0]
        return f"{base}/releases/download/{tag}/{name}", ""
    return "", ""


def _from_github_release(
    release: dict, current_version: str, feed_url: str = ""
) -> ServerUpdateInfo:
    latest_version = _clean_version(str(release.get("tag_name") or ""))
    if not latest_version:
        raise ValueError("更新源中的 Release 缺少 tag_name")
    server_url, server_sha256 = _release_asset_url(release, SERVER_PACKAGE_NAME)
    release_url = str(release.get("html_url") or "") or feed_url
    return ServerUpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        update_available=version_key(latest_version) > version_key(current_version),
        release_url=release_url,
        server_download_url=server_url or release_url,
        published_at=_format_moment(
            str(release.get("published_at") or release.get("created_at") or "")
        ),
        notes=_plain_notes(str(release.get("body") or "")),
        source="GitHub Releases",
        checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        latest_tag=str(release.get("tag_name") or ""),
        server_sha256=server_sha256,
    )


def _from_manifest(data: dict, feed_url: str, current_version: str) -> ServerUpdateInfo:
    latest_version = _clean_version(str(data.get("version") or ""))
    if not latest_version:
        raise ValueError("更新清单缺少 version 字段")
    page_url = str(data.get("page_url") or "./")
    raw_server = str(data.get("server_download_url") or SERVER_PACKAGE_NAME)
    notes = data.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    return ServerUpdateInfo(
        current_version=current_version,
        latest_version=latest_version,
        update_available=version_key(latest_version) > version_key(current_version),
        release_url=urljoin(feed_url, page_url),
        server_download_url=urljoin(feed_url, raw_server),
        published_at=_format_moment(str(data.get("published_at") or "")),
        notes=[str(item)[:180] for item in notes[:6]],
        source="更新清单",
        checked_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        latest_tag=str(data.get("version") or ""),
        server_sha256=str(data.get("server_sha256") or ""),
    )


def check_for_server_update(
    current_version: str,
    feed_url: str = DEFAULT_UPDATE_FEED,
    timeout: int = 8,
) -> ServerUpdateInfo:
    """读取更新源并返回服务端版本比较结果。"""
    try:
        response = requests.get(
            feed_url,
            timeout=timeout,
            headers={
                "Accept": "application/vnd.github+json, application/json",
                "User-Agent": "NexHub-Server-Update-Check",
            },
            params={"per_page": RELEASES_PER_PAGE},
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"无法连接更新源：{exc}") from exc
    except ValueError as exc:
        raise ValueError("更新源返回的不是有效 JSON") from exc

    if isinstance(data, list):
        releases = [item for item in data if isinstance(item, dict) and item.get("tag_name")]
        if not releases:
            raise ValueError("更新源没有可用的 Release")
        latest = max(
            releases,
            key=lambda item: version_key(str(item.get("tag_name") or "")),
        )
        return _from_github_release(latest, current_version, feed_url)
    if isinstance(data, dict) and "releases" in data:
        releases = [
            item
            for item in (data.get("releases") or [])
            if isinstance(item, dict) and item.get("tag_name")
        ]
        if not releases:
            raise ValueError("更新源没有可用的 Release")
        latest = max(
            releases,
            key=lambda item: version_key(str(item.get("tag_name") or "")),
        )
        return _from_github_release(latest, current_version, feed_url)
    if isinstance(data, dict):
        return _from_manifest(data, feed_url, current_version)
    raise ValueError("更新源格式无法识别")
