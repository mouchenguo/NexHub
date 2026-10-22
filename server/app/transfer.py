"""共享文件流式传输（OSS / 临时穿透）。"""
from __future__ import annotations

from urllib.parse import quote

import requests
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .common import normalize_rel_path, share_manifest
from .oss_service import oss_service


def _content_disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode() or "download.bin"
    ascii_name = ascii_name.replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def find_file(share: dict, relative_path: str) -> dict:
    try:
        relative = normalize_rel_path(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for item in share_manifest(share):
        if item.get("path") == relative:
            return item
    raise HTTPException(status_code=404, detail="文件不在共享范围内")


def stream_share_file(share: dict, relative_path: str):
    item = find_file(share, relative_path)
    filename = item["path"].rsplit("/", 1)[-1]
    headers = {
        "Content-Disposition": _content_disposition(filename),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=0",
    }

    if share["storage_type"] == "oss":
        key = oss_service.object_key(share["token"], item["path"])
        if not oss_service.exists(key):
            raise HTTPException(status_code=404, detail="OSS 缓存缺失，可能已过期或被清理")
        headers["Content-Length"] = str(item.get("size") or 0)
        return StreamingResponse(
            oss_service.open_iter(key),
            media_type="application/octet-stream",
            headers=headers,
        )

    local_url = (share.get("local_url") or "").rstrip("/")
    if not local_url:
        raise HTTPException(status_code=503, detail="临时共享未配置穿透地址或发布者已离线")
    url = f"{local_url}/file/{share['token']}/{quote(item['path'], safe='/')}"
    try:
        upstream = requests.get(url, stream=True, timeout=(10, 300))
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"穿透通道不可达：{exc}") from exc
    if upstream.status_code >= 400:
        upstream.close()
        raise HTTPException(status_code=502, detail="临时共享主机返回错误，发布者可能已离线")

    def relay():
        try:
            for chunk in upstream.iter_content(512 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    content_length = upstream.headers.get("Content-Length")
    if content_length:
        headers["Content-Length"] = content_length
    return StreamingResponse(
        relay(),
        media_type=upstream.headers.get("Content-Type", "application/octet-stream"),
        headers=headers,
    )
