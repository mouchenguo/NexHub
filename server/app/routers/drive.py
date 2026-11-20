"""个人网盘：API（客户端）+ 网页端（登录、文件管理、分享）。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import db
from ..common import (
    client_ip,
    drive_used,
    human_size,
    normalize_rel_path,
    quota_bytes,
    user_oss_used,
    web_base,
)
from ..config import settings
from ..deps import ADMIN_COOKIE, _user_from_token, get_current_user, issue_token
from ..drive_service import (
    delete_drive_file,
    drive_file,
    drive_items,
    drive_prefix,
    register_drive_file,
    share_drive_files,
)
from ..oss_service import oss_service
from ..registration import status_error
from ..schemas import DriveShareIn
from ..security import verify_password
from ..templating import render

router = APIRouter(tags=["drive"])


def _redirect(url: str, message: str = "", message_type: str = "success") -> RedirectResponse:
    if message:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'msg': message, 'type': message_type})}"
    return RedirectResponse(url, status_code=303)


def _page_user(request: Request) -> dict | None:
    return _user_from_token(request.cookies.get(ADMIN_COOKIE, ""))


def _require_page_user(request: Request) -> dict:
    user = _page_user(request)
    if not user:
        raise HTTPException(
            status_code=303, headers={"Location": "/drive/login"}, detail="redirect"
        )
    return user


def _store_upload(owner_id: int, relative_path: str, temp_name: str) -> tuple[str, int]:
    key, size = oss_service.put_file(
        drive_prefix(owner_id), relative_path, Path(temp_name)
    )
    register_drive_file(owner_id, relative_path, key, size)
    return key, size


# --------------------------------------------------------------------- 客户端 API
@router.get("/api/drive")
def api_list(user: dict = Depends(get_current_user)):
    uid = int(user["id"])
    return {
        "items": drive_items(uid),
        "used": user_oss_used(uid),
        "drive_used": drive_used(uid),
        "quota": quota_bytes(),
    }


@router.post("/api/drive/files")
async def api_upload(
    request: Request,
    relative_path: str = Query(alias="path", min_length=1, max_length=1024),
    user: dict = Depends(get_current_user),
):
    try:
        relative = normalize_rel_path(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if user_oss_used(int(user["id"])) >= quota_bytes():
        raise HTTPException(status_code=507, detail="网盘容量已满，请先删除文件")

    settings.upload_temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=settings.upload_temp_dir, prefix="nexhub-drive-")
    total = 0
    try:
        with os.fdopen(fd, "wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > settings.max_oss_bytes:
                    raise HTTPException(status_code=413, detail="单个文件超过 5GB 限制")
                handle.write(chunk)
        _, size = await run_in_threadpool(
            _store_upload, int(user["id"]), relative, temp_name
        )
        db.log_event(
            "info",
            "oss",
            f"上传网盘：{relative}（{size} bytes）",
            user_id=user["id"],
            ip=client_ip(request),
        )
        return {"ok": True, "path": relative, "size": size}
    finally:
        try:
            os.unlink(temp_name)
        except OSError:
            pass


@router.get("/api/drive/files/{file_id}/download")
def api_download(
    file_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    row = drive_file(int(user["id"]), file_id)
    if not row:
        raise HTTPException(status_code=404, detail="网盘文件不存在")
    if not oss_service.exists(row["object_key"]):
        raise HTTPException(status_code=404, detail="文件已丢失")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{row['name']}",
        "Content-Length": str(int(row["size"] or 0)),
    }
    return StreamingResponse(
        oss_service.open_iter(row["object_key"]),
        media_type="application/octet-stream",
        headers=headers,
    )


@router.delete("/api/drive/files/{file_id}")
def api_delete(file_id: int, request: Request, user: dict = Depends(get_current_user)):
    try:
        delete_drive_file(user, file_id, client_ip(request))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/api/drive/files/{file_id}/share")
def api_share(
    file_id: int,
    payload: DriveShareIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    row = drive_file(int(user["id"]), file_id)
    if not row:
        raise HTTPException(status_code=404, detail="网盘文件不存在")
    return share_drive_files(
        user, [row], payload.name, payload.description, web_base(request)
    )


# --------------------------------------------------------------------- 网页端
@router.get("/drive/login", include_in_schema=False)
def drive_login_page(request: Request):
    if _page_user(request):
        return RedirectResponse("/drive", status_code=303)
    return render(
        request,
        "drive/login.html",
        {"error": "", "username": "", "next": request.query_params.get("next", "")},
    )


@router.post("/drive/login", include_in_schema=False)
def drive_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    identifier = (username or "").strip()
    user = db.query_one("SELECT * FROM users WHERE username = ?", (identifier,))
    if not user:
        matches = db.query_all(
            "SELECT * FROM users WHERE display_name = ? LIMIT 2", (identifier,)
        )
        user = matches[0] if len(matches) == 1 else None
    if not user or not verify_password(
        password, user["password_hash"], user["password_salt"]
    ):
        db.log_event("warning", "auth", f"网页网盘登录失败：{identifier}", ip=client_ip(request))
        return render(
            request,
            "drive/login.html",
            {"error": "用户名/昵称或密码错误", "username": identifier, "next": ""},
            status_code=401,
        )
    status = str(user.get("status") or "approved")
    if status != "approved":
        return render(
            request,
            "drive/login.html",
            {"error": status_error(status), "username": identifier, "next": ""},
            status_code=403,
        )
    if not user.get("enabled"):
        return render(
            request,
            "drive/login.html",
            {"error": "账号已被管理员禁用", "username": identifier, "next": ""},
            status_code=403,
        )
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.utcnow(), user["id"]))
    db.log_event("info", "auth", "网页网盘登录", user_id=user["id"], ip=client_ip(request))
    response = RedirectResponse("/drive", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        issue_token(user),
        max_age=settings.token_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.get("/drive/logout", include_in_schema=False)
def drive_logout():
    response = RedirectResponse("/drive/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


@router.get("/drive", include_in_schema=False)
def drive_page(request: Request, user: dict = Depends(_require_page_user)):
    uid = int(user["id"])
    return render(
        request,
        "drive/index.html",
        {
            "user": user,
            "items": drive_items(uid),
            "used": used_bytes(uid),
            "quota": quota_bytes(),
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        },
    )


@router.post("/drive/upload", include_in_schema=False)
async def drive_upload_page(
    request: Request,
    files: list[UploadFile] = File(default=[]),
    target_path: str = Form(default=""),
    user: dict = Depends(_require_page_user),
):
    uid = int(user["id"])
    if not files:
        return _redirect("/drive", "请选择要上传的文件", "error")
    os.makedirs(settings.upload_temp_dir, exist_ok=True)
    stored = 0
    for upload in files:
        raw_name = Path(upload.filename or "file.bin").name
        folder = (target_path or "").strip().strip("/")
        relative = normalize_rel_path(f"{folder}/{raw_name}" if folder else raw_name)
        fd, temp_name = tempfile.mkstemp(
            dir=settings.upload_temp_dir, prefix="nexhub-drive-web-"
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    handle.write(chunk)
            await run_in_threadpool(_store_upload, uid, relative, temp_name)
            stored += 1
        except Exception as exc:  # noqa: BLE001
            db.log_event(
                "error", "oss", f"网页上传网盘失败：{raw_name}（{exc}）", user_id=uid
            )
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            await upload.close()
    if not stored:
        return _redirect("/drive", "上传失败，请稍后重试", "error")
    return _redirect("/drive", f"已上传 {stored} 个文件到网盘")


@router.post("/drive/share", include_in_schema=False)
def drive_share_page(
    request: Request,
    file_ids: list[int] = Form(default=[]),
    name: str = Form(default=""),
    description: str = Form(default=""),
    user: dict = Depends(_require_page_user),
):
    rows = [drive_file(int(user["id"]), fid) for fid in file_ids]
    rows = [row for row in rows if row]
    if not rows:
        return _redirect("/drive", "请先勾选要共享的文件", "error")
    try:
        payload = share_drive_files(
            user, rows, name, description, web_base(request)
        )
    except ValueError as exc:
        return _redirect("/drive", str(exc), "error")
    return _redirect("/drive", f"共享已创建：{payload['public_url']}")


@router.post("/drive/delete", include_in_schema=False)
def drive_delete_page(
    request: Request,
    file_id: int = Form(...),
    user: dict = Depends(_require_page_user),
):
    try:
        delete_drive_file(user, file_id, client_ip(request))
    except KeyError as exc:
        return _redirect("/drive", str(exc), "error")
    return _redirect("/drive", "文件已从网盘删除（相关共享已取消）")


def used_bytes(user_id: int) -> int:
    return user_oss_used(user_id)
