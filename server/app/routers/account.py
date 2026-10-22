"""账号相关：下载记录、OSS 用量与本地头像。"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from .. import db
from ..common import client_ip, quota_bytes, user_oss_used, user_payload
from ..config import settings
from ..deps import get_current_user

router = APIRouter(prefix="/api/account", tags=["account"])

MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "webp": "image/webp",
}


def _detect_avatar(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", AVATAR_TYPES["png"]
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", AVATAR_TYPES["jpg"]
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", AVATAR_TYPES["webp"]
    raise HTTPException(status_code=400, detail="头像仅支持 PNG / JPG / WebP")


def _avatar_path(user_id: int, ext: str) -> Path:
    return settings.avatar_dir / f"{user_id}.{ext}"


@router.post("/avatar")
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user),
):
    data = await file.read(MAX_AVATAR_BYTES + 1)
    await file.close()
    if not data:
        raise HTTPException(status_code=400, detail="头像文件为空")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="头像不能超过 5MB")
    ext, _mime = _detect_avatar(data)

    settings.avatar_dir.mkdir(parents=True, exist_ok=True)
    target = _avatar_path(int(user["id"]), ext)
    temp = target.with_suffix(target.suffix + ".tmp")
    try:
        temp.write_bytes(data)
        os.replace(temp, target)
    except OSError as exc:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"保存头像失败：{exc}") from exc

    old_ext = user.get("avatar_ext") or ""
    if old_ext in AVATAR_TYPES and old_ext != ext:
        try:
            _avatar_path(int(user["id"]), old_ext).unlink(missing_ok=True)
        except OSError:
            pass

    db.execute(
        "UPDATE users SET avatar_ext = ? WHERE id = ?",
        (ext, int(user["id"])),
    )
    updated = db.query_one("SELECT * FROM users WHERE id = ?", (int(user["id"]),))
    db.log_event(
        "info",
        "account",
        "用户更新了头像",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return {
        "ok": True,
        "user": user_payload(updated),
        "avatar_url": "/api/account/avatar",
    }


@router.get("/avatar")
def get_avatar(user: dict = Depends(get_current_user)):
    ext = user.get("avatar_ext") or ""
    if ext not in AVATAR_TYPES:
        raise HTTPException(status_code=404, detail="尚未设置头像")
    path = _avatar_path(int(user["id"]), ext)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="头像文件不存在")
    return FileResponse(
        path,
        media_type=AVATAR_TYPES[ext],
        headers={
            "Content-Disposition": "inline",
            "Cache-Control": "private, max-age=60",
        },
    )


@router.get("/downloads")
def download_records(user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT d.*, s.name AS share_name FROM download_logs d "
        "LEFT JOIN shares s ON s.id = d.share_id "
        "WHERE d.user_id = ? ORDER BY d.created_at DESC LIMIT 100",
        (user["id"],),
    )
    items = []
    for row in rows:
        items.append(
            {
                "id": row["id"],
                "file_name": row["file_path"],
                "share_name": row.get("share_name") or "远程主机 / 已删除共享",
                "size": row["file_size"],
                "created_at": row["created_at"],
                "status": row["status"],
            }
        )
    return {"items": items}


@router.get("/oss")
def oss_usage(user: dict = Depends(get_current_user)):
    used = user_oss_used(int(user["id"]))
    quota = quota_bytes()
    return {
        "oss_used": used,
        "oss_quota": quota,
        "percent": round(used / quota * 100, 2) if quota else 0,
    }
