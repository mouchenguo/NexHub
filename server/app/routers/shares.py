"""共享 API：发布、列表、manifest、OSS 上传代理、下载与删除。"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from .. import db
from ..common import (
    client_ip,
    log_download,
    normalize_rel_path,
    sanitize_manifest,
    share_manifest,
    share_payload,
    web_base,
)
from ..config import settings
from ..deps import get_current_user
from ..nps_api import NpsApiError, delete_tunnel
from ..oss_service import oss_service
from ..schemas import ShareCreateIn
from ..security import generate_token
from ..share_ops import (
    cancel_share,
    cleanup_oss_capacity,
    create_temp_route,
    fetch_share,
    remove_share,
)
from ..transfer import find_file, stream_share_file

router = APIRouter(prefix="/api/shares", tags=["shares"])


def _require_owner(share: dict, user: dict) -> None:
    if int(share["owner_id"]) != int(user["id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能管理自己发布的共享")


@router.get("")
def list_shares(
    request: Request,
    scope: str = "public",
    user: dict = Depends(get_current_user),
):
    scope = scope if scope in ("mine", "public") else "public"
    base = web_base(request)
    if scope == "mine":
        rows = db.query_all(
            "SELECT s.*, u.username AS owner_username, u.display_name AS owner_display "
            "FROM shares s JOIN users u ON u.id = s.owner_id "
            "WHERE s.owner_id = ? ORDER BY s.created_at DESC",
            (user["id"],),
        )
    else:
        rows = db.query_all(
            "SELECT s.*, u.username AS owner_username, u.display_name AS owner_display "
            "FROM shares s JOIN users u ON u.id = s.owner_id "
            "WHERE s.status = 'active' ORDER BY s.created_at DESC"
        )
    return {"items": [share_payload(row, int(user["id"]), base) for row in rows]}


@router.post("")
def create_share(
    payload: ShareCreateIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    try:
        manifest = sanitize_manifest([item.model_dump() for item in payload.files])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    total_size = sum(item["size"] for item in manifest)
    temp_route = None
    local_url = ""
    if payload.storage_type == "oss":
        if total_size >= settings.max_oss_bytes:
            raise HTTPException(
                status_code=400,
                detail="文件总大小已达到 5GB，仅支持临时穿透共享",
            )
        if not cleanup_oss_capacity(total_size, int(user["id"]), client_ip(request)):
            raise HTTPException(status_code=507, detail="全局 OSS 容量不足，请联系管理员扩容")
    else:
        if settings.nps_auto_tunnel:
            try:
                temp_route = create_temp_route(int(user["id"]), payload.name.strip())
            except NpsApiError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            local_url = temp_route["local_url"]
        else:
            local_url = (payload.local_url or "").strip().rstrip("/")
            if local_url and not local_url.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail="穿透地址必须以 http(s):// 开头")

    try:
        share_id = db.execute(
            "INSERT INTO shares(token, owner_id, name, description, storage_type, total_size, "
            "file_count, status, local_url, nps_task_id, public_port, manifest_json, downloads, created_at) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 0, ?)",
            (
                generate_token(18),
                user["id"],
                payload.name.strip(),
                payload.description.strip(),
                payload.storage_type,
                total_size,
                len(manifest),
                local_url,
                temp_route["task_id"] if temp_route else None,
                temp_route["public_port"] if temp_route else None,
                json.dumps(manifest, ensure_ascii=False),
                db.utcnow(),
            ),
        )
    except Exception as exc:
        if temp_route:
            try:
                delete_tunnel(int(temp_route["task_id"]))
            except NpsApiError:
                pass
        raise HTTPException(status_code=500, detail=f"保存共享失败：{exc}") from exc
    share = fetch_share(share_id)
    db.log_event(
        "info",
        "share",
        f"发布共享：{payload.name}（{'OSS' if payload.storage_type == 'oss' else '临时穿透'}，"
        f"{len(manifest)} 个文件，{total_size} bytes）",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return share_payload(share, int(user["id"]), web_base(request))


@router.post("/{share_id}/files")
async def upload_file(
    share_id: int,
    request: Request,
    relative_path: str = Query(alias="path", min_length=1, max_length=1024),
    user: dict = Depends(get_current_user),
):
    share = fetch_share(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在")
    _require_owner(share, user)
    if share["storage_type"] != "oss":
        raise HTTPException(status_code=400, detail="仅 OSS 共享支持文件上传")
    if share["status"] != "active":
        raise HTTPException(status_code=409, detail="共享已取消")
    try:
        relative = normalize_rel_path(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    expected = next(
        (item for item in share_manifest(share) if item["path"] == relative), None
    )
    if not expected:
        raise HTTPException(status_code=400, detail="文件不在 manifest 范围内")

    settings.upload_temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=settings.upload_temp_dir, prefix="nexhub-upload-")
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_oss_bytes:
                    raise HTTPException(status_code=413, detail="单个文件超过 5GB 限制")
                handle.write(chunk)
        key, size = await run_in_threadpool(
            oss_service.put_file, share["token"], relative, Path(temp_name)
        )
        db.execute(
            "INSERT INTO oss_objects(share_id, object_key, relative_path, size, created_at) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(share_id, relative_path) DO UPDATE SET "
            "object_key = excluded.object_key, size = excluded.size",
            (share_id, key, relative, size, db.utcnow()),
        )
        db.log_event(
            "info",
            "oss",
            f"上传 OSS：{share['name']} / {relative}（{size} bytes）",
            user_id=user["id"],
            ip=client_ip(request),
        )
        return {"ok": True, "path": relative, "size": size}
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass


@router.get("/{share_id}/manifest")
def get_manifest(
    share_id: int,
    user: dict = Depends(get_current_user),
):
    share = fetch_share(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在")
    if share["status"] != "active" and int(share["owner_id"]) != int(user["id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=404, detail="共享已失效")
    files = share_manifest(share)
    return {
        "share_id": share_id,
        "name": share["name"],
        "storage_type": share["storage_type"],
        "token": share["token"],
        "files": files,
        "total_size": share["total_size"],
        "file_count": share["file_count"],
    }


@router.get("/{share_id}/download")
def download_share_file(
    share_id: int,
    request: Request,
    relative_path: str = Query(alias="path", min_length=1, max_length=1024),
    user: dict = Depends(get_current_user),
):
    share = fetch_share(share_id)
    if not share or share["status"] != "active":
        raise HTTPException(status_code=404, detail="共享不存在或已失效")
    item = find_file(share, relative_path)
    response = stream_share_file(share, item["path"])
    log_download(share_id, int(user["id"]), item["path"], int(item.get("size") or 0), client_ip(request))
    db.execute("UPDATE shares SET downloads = downloads + 1 WHERE id = ?", (share_id,))
    return response


@router.post("/{share_id}/cancel")
def cancel(
    share_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    share = fetch_share(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在")
    _require_owner(share, user)
    if share["status"] == "active":
        cancel_share(share, int(user["id"]), client_ip(request))
    return {"ok": True, "share_id": share_id}


@router.delete("/{share_id}")
def delete(
    share_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    share = fetch_share(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在")
    _require_owner(share, user)
    remove_share(share, int(user["id"]), client_ip(request))
    return {"ok": True}
