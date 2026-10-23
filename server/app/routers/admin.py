"""ECS Web 管理后台（1Panel 简约风格）。

管理员通过 HttpOnly Cookie 登录；普通用户无法访问。
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import db
from ..common import client_ip, global_oss_used, quota_bytes
from ..config import settings
from ..deps import ADMIN_COOKIE, admin_page_user, issue_token
from ..nps_service import active_temp_tunnels, check_status, get_config as get_nps_config, save_config as save_nps_config
from ..oss_service import oss_service
from ..registration import (
    STATUS_APPROVED,
    STATUS_LABELS,
    STATUS_PENDING,
    STATUS_REJECTED,
    auto_approve_enabled,
    set_auto_approve,
    status_error,
)
from ..security import hash_password, verify_password
from ..share_ops import cancel_share, fetch_share, remove_share
from ..templating import render
from ..update_service import check_for_server_update

router = APIRouter(tags=["admin"])

LEVEL_LABELS = {"info": "信息", "warning": "警告", "error": "错误"}
CATEGORY_LABELS = {
    "auth": "认证",
    "share": "共享",
    "download": "下载",
    "device": "设备",
    "oss": "OSS",
    "nps": "NPS",
    "admin": "管理",
    "system": "系统",
    "error": "错误",
}


def _redirect(url: str, message: str = "", message_type: str = "success") -> RedirectResponse:
    if message:
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'msg': message, 'type': message_type})}"
    return RedirectResponse(url, status_code=303)


def _base_context(admin: dict, active: str, title: str) -> dict:
    return {
        "admin": admin,
        "active": active,
        "title": title,
        "version": settings.version,
        "message": "",
        "message_type": "success",
    }


# ---------------------------------------------------------------------- 登录
@router.get("/admin/login")
def admin_login_page(request: Request):
    token = request.cookies.get(ADMIN_COOKIE, "")
    if token:
        from ..deps import _user_from_token  # 内部复用

        user = _user_from_token(token)
        if user and user.get("role") == "admin":
            return RedirectResponse("/admin", status_code=303)
    return render(request, "admin/login.html", {"title": "管理员登录", "version": settings.version})


@router.post("/admin/login")
def admin_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    user = db.query_one("SELECT * FROM users WHERE username = ?", (username.strip(),))
    if (
        not user
        or user.get("role") != "admin"
        or not verify_password(password, user["password_hash"], user["password_salt"])
    ):
        db.log_event("warning", "auth", f"管理后台登录失败：{username}", ip=client_ip(request))
        return render(
            request,
            "admin/login.html",
            {
                "title": "管理员登录",
                "version": settings.version,
                "error": "账号或密码错误，或该账号不是管理员",
                "username": username,
            },
        )
    if str(user.get("status") or STATUS_APPROVED) != STATUS_APPROVED:
        return render(
            request,
            "admin/login.html",
            {
                "title": "管理员登录",
                "version": settings.version,
                "error": status_error(str(user.get("status") or STATUS_APPROVED)),
            },
        )
    if not user.get("enabled"):
        return render(
            request,
            "admin/login.html",
            {"title": "管理员登录", "version": settings.version, "error": "账号已被禁用"},
        )
    token = issue_token(user)
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.utcnow(), user["id"]))
    db.log_event(
        "info", "auth", "管理员登录 Web 后台", user_id=user["id"], ip=client_ip(request)
    )
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=settings.token_ttl_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/",
    )
    return response


@router.get("/admin/logout")
def admin_logout(request: Request, admin: dict = Depends(admin_page_user)):
    db.log_event("info", "auth", "管理员退出 Web 后台", user_id=admin["id"], ip=client_ip(request))
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response


# ---------------------------------------------------------------------- 仪表盘
@router.get("/admin")
def admin_dashboard(request: Request, admin: dict = Depends(admin_page_user)):
    context = _base_context(admin, "dashboard", "仪表盘")
    users = db.query_one("SELECT COUNT(*) AS count FROM users")
    pending_users = db.query_one(
        "SELECT COUNT(*) AS count FROM users WHERE status = ?", (STATUS_PENDING,)
    )
    shares = db.query_one("SELECT COUNT(*) AS count FROM shares WHERE status = 'active'")
    downloads = db.query_one("SELECT COUNT(*) AS count FROM download_logs")
    recent_shares = db.query_all(
        "SELECT s.*, u.username AS owner_username FROM shares s JOIN users u ON u.id = s.owner_id "
        "ORDER BY s.created_at DESC LIMIT 6"
    )
    recent_logs = db.query_all(
        "SELECT * FROM system_logs ORDER BY created_at DESC, id DESC LIMIT 8"
    )
    oss_used = global_oss_used(active_only=True)
    quota = quota_bytes()
    started = getattr(request.app.state, "started_at", datetime.now(timezone.utc))
    uptime = datetime.now(timezone.utc) - started
    nps = check_status(timeout=0.8)
    context.update(
        {
            "stats": {
                "users": int(users["count"]) if users else 0,
                "pending_users": int(pending_users["count"]) if pending_users else 0,
                "shares": int(shares["count"]) if shares else 0,
                "downloads": int(downloads["count"]) if downloads else 0,
                "oss_used": oss_used,
                "oss_quota": quota,
                "oss_percent": round(oss_used / quota * 100, 1) if quota else 0,
            },
            "recent_shares": recent_shares,
            "recent_logs": recent_logs,
            "oss_mode": oss_service.status()["mode"],
            "nps_status": nps,
            "uptime": f"{int(uptime.total_seconds() // 3600)} 小时 "
            f"{int(uptime.total_seconds() % 3600 // 60)} 分钟",
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/dashboard.html", context)


# ---------------------------------------------------------------------- 用户
@router.get("/admin/users")
def admin_users(request: Request, admin: dict = Depends(admin_page_user)):
    context = _base_context(admin, "users", "用户管理")
    rows = db.query_all(
        "SELECT u.*, "
        "COALESCE((SELECT SUM(total_size) FROM shares s WHERE s.owner_id = u.id "
        "AND s.storage_type = 'oss' AND s.status = 'active'), 0) AS oss_used, "
        "(SELECT COUNT(*) FROM shares s WHERE s.owner_id = u.id) AS share_count "
        "FROM users u "
        "ORDER BY CASE u.status WHEN 'pending' THEN 0 WHEN 'rejected' THEN 2 ELSE 1 END, "
        "u.created_at DESC"
    )
    pending = db.query_one(
        "SELECT COUNT(*) AS count FROM users WHERE status = ?", (STATUS_PENDING,)
    )
    context.update(
        {
            "users": rows,
            "quota": quota_bytes(),
            "pending_count": int(pending["count"]) if pending else 0,
            "auto_approve": auto_approve_enabled(),
            "status_labels": STATUS_LABELS,
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/users.html", context)


@router.post("/admin/users/{user_id}/toggle")
def admin_toggle_user(
    user_id: int,
    request: Request,
    admin: dict = Depends(admin_page_user),
):
    target = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not target:
        return _redirect("/admin/users", "用户不存在", "error")
    if int(target["id"]) == int(admin["id"]):
        return _redirect("/admin/users", "不能禁用当前登录的管理员账号", "error")
    if target.get("role") == "admin":
        return _redirect("/admin/users", "管理员账号请通过数据库或 CLI 调整", "error")
    new_state = 0 if target["enabled"] else 1
    db.execute("UPDATE users SET enabled = ? WHERE id = ?", (new_state, user_id))
    db.log_event(
        "warning" if new_state == 0 else "info",
        "admin",
        f"{'禁用' if new_state == 0 else '启用'}用户：{target['username']}",
        user_id=admin["id"],
        ip=client_ip(request),
    )
    return _redirect(
        "/admin/users", f"已{'启用' if new_state else '禁用'}用户 {target['username']}"
    )


@router.post("/admin/users/{user_id}/review")
def admin_review_user(
    user_id: int,
    request: Request,
    action: str = Form(...),
    admin: dict = Depends(admin_page_user),
):
    target = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not target:
        return _redirect("/admin/users", "用户不存在", "error")
    if target.get("role") == "admin":
        return _redirect("/admin/users", "管理员账号无需注册审核", "error")
    action = action.strip().lower()
    if action not in ("approve", "reject"):
        return _redirect("/admin/users", "无效的审核操作", "error")
    if action == "approve":
        db.execute(
            "UPDATE users SET status = ?, enabled = 1 WHERE id = ?",
            (STATUS_APPROVED, user_id),
        )
        message = f"已通过 {target['username']} 的注册审核"
        level = "info"
    else:
        db.execute(
            "UPDATE users SET status = ? WHERE id = ?", (STATUS_REJECTED, user_id)
        )
        message = f"已拒绝 {target['username']} 的注册申请"
        level = "warning"
    db.log_event(
        level,
        "admin",
        message,
        user_id=admin["id"],
        ip=client_ip(request),
    )
    return _redirect("/admin/users", message)


@router.post("/admin/users/{user_id}/reset-password")
def admin_reset_password(
    user_id: int,
    request: Request,
    new_password: str = Form(...),
    admin: dict = Depends(admin_page_user),
):
    target = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not target:
        return _redirect("/admin/users", "用户不存在", "error")
    if len(new_password) < 6:
        return _redirect("/admin/users", "新密码至少需要 6 位", "error")
    password_hash, salt = hash_password(new_password)
    db.execute(
        "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
        (password_hash, salt, user_id),
    )
    db.log_event(
        "warning",
        "admin",
        f"重置用户密码：{target['username']}",
        user_id=admin["id"],
        ip=client_ip(request),
    )
    return _redirect("/admin/users", f"已重置 {target['username']} 的密码")


# ---------------------------------------------------------------------- 共享
@router.get("/admin/shares")
def admin_shares(
    request: Request,
    admin: dict = Depends(admin_page_user),
    status: str = "",
    q: str = "",
):
    context = _base_context(admin, "shares", "共享资源")
    where = []
    params: list = []
    if status in ("active", "cancelled"):
        where.append("s.status = ?")
        params.append(status)
    if q:
        where.append("(s.name LIKE ? OR u.username LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = db.query_all(
        "SELECT s.*, u.username AS owner_username, u.display_name AS owner_display "
        "FROM shares s JOIN users u ON u.id = s.owner_id "
        f"{clause} ORDER BY s.created_at DESC LIMIT 500",
        params,
    )
    context.update(
        {
            "shares": rows,
            "status": status,
            "q": q,
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/shares.html", context)


@router.post("/admin/shares/{share_id}/cancel")
def admin_cancel_share(
    share_id: int,
    request: Request,
    admin: dict = Depends(admin_page_user),
):
    share = fetch_share(share_id)
    if not share:
        return _redirect("/admin/shares", "共享不存在", "error")
    if share["status"] == "active":
        cancel_share(share, int(admin["id"]), client_ip(request), reason="管理员关闭")
    return _redirect("/admin/shares", f"共享「{share['name']}」已关闭")


@router.post("/admin/shares/{share_id}/delete")
def admin_delete_share(
    share_id: int,
    request: Request,
    admin: dict = Depends(admin_page_user),
):
    share = fetch_share(share_id)
    if not share:
        return _redirect("/admin/shares", "共享不存在", "error")
    remove_share(share, int(admin["id"]), client_ip(request))
    return _redirect("/admin/shares", f"共享「{share['name']}」已删除")


@router.get("/admin/shares/{share_id}/logs")
def admin_share_logs(
    share_id: int,
    request: Request,
    admin: dict = Depends(admin_page_user),
):
    share = fetch_share(share_id)
    if not share:
        raise HTTPException(status_code=404, detail="共享不存在")
    rows = db.query_all(
        "SELECT d.*, u.username FROM download_logs d LEFT JOIN users u ON u.id = d.user_id "
        "WHERE d.share_id = ? ORDER BY d.created_at DESC LIMIT 300",
        (share_id,),
    )
    context = _base_context(admin, "shares", f"访问日志 · {share['name']}")
    context.update({"share": share, "logs": rows})
    return render(request, "admin/share_logs.html", context)


# ---------------------------------------------------------------------- OSS
@router.get("/admin/oss")
def admin_oss(request: Request, admin: dict = Depends(admin_page_user)):
    context = _base_context(admin, "oss", "OSS 系统配置")
    objects = db.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(size), 0) AS bytes FROM oss_objects"
    )
    active_oss = db.query_one(
        "SELECT COUNT(*) AS count, COALESCE(SUM(total_size), 0) AS bytes FROM shares "
        "WHERE storage_type = 'oss' AND status = 'active'"
    )
    context.update(
        {
            "quota": quota_bytes(),
            "used": global_oss_used(active_only=True),
            "object_count": int(objects["count"]) if objects else 0,
            "object_bytes": int(objects["bytes"]) if objects else 0,
            "active_oss_count": int(active_oss["count"]) if active_oss else 0,
            "oss": oss_service.status(),
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/oss.html", context)


@router.post("/admin/oss")
def admin_oss_update(
    request: Request,
    quota_gb: float = Form(...),
    admin: dict = Depends(admin_page_user),
):
    if quota_gb <= 0:
        return _redirect("/admin/oss", "容量上限必须大于 0", "error")
    quota = int(quota_gb * 1024 ** 3)
    db.set_setting("oss_quota_bytes", str(quota))
    db.log_event(
        "warning",
        "admin",
        f"更新 OSS 全局容量上限为 {quota_gb:.1f} GB",
        user_id=admin["id"],
        ip=client_ip(request),
    )
    if quota < global_oss_used(active_only=True):
        return _redirect(
            "/admin/oss",
            "容量上限已更新，但当前占用已超过上限；下次发布 OSS 共享将自动清理最早共享",
            "warning",
        )
    return _redirect("/admin/oss", "OSS 容量上限已更新")


# ---------------------------------------------------------------------- NPS
@router.get("/admin/nps")
def admin_nps(request: Request, admin: dict = Depends(admin_page_user)):
    context = _base_context(admin, "nps", "服务器与 NPS 配置")
    context.update(
        {
            "config": get_nps_config(),
            "status": check_status(timeout=1.0),
            "tunnels": active_temp_tunnels(),
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/nps.html", context)


@router.post("/admin/nps")
def admin_nps_update(
    request: Request,
    server_addr: str = Form(default=""),
    server_port: int = Form(default=8024),
    vkey: str = Form(default=""),
    api_url: str = Form(default=""),
    auth_key: str = Form(default=""),
    domain: str = Form(default=""),
    admin: dict = Depends(admin_page_user),
):
    if not (1 <= int(server_port) <= 65535):
        return _redirect("/admin/nps", "nps 端口不合法", "error")
    payload = {
        "nps_server_addr": server_addr.strip(),
        "nps_server_port": int(server_port),
        "nps_api_url": api_url.strip(),
        "nps_domain": domain.strip(),
    }
    if vkey.strip():
        payload["nps_vkey"] = vkey.strip()
    if auth_key.strip():
        payload["nps_auth_key"] = auth_key.strip()
    save_nps_config(payload)
    db.log_event(
        "info", "admin", "更新 nps 穿透配置", user_id=admin["id"], ip=client_ip(request)
    )
    return _redirect("/admin/nps", "nps 配置已保存")


@router.post("/admin/nps/tunnels/{share_id}/cancel")
def admin_close_tunnel(
    share_id: int,
    request: Request,
    admin: dict = Depends(admin_page_user),
):
    share = fetch_share(share_id)
    if not share:
        return _redirect("/admin/nps", "穿透通道不存在", "error")
    cancel_share(share, int(admin["id"]), client_ip(request), reason="管理员关闭通道")
    return _redirect("/admin/nps", "穿透通道已关闭")


# ---------------------------------------------------------------------- 日志
@router.get("/admin/logs")
def admin_logs(
    request: Request,
    admin: dict = Depends(admin_page_user),
    category: str = "",
    level: str = "",
    q: str = "",
):
    context = _base_context(admin, "logs", "全局日志")
    where = []
    params: list = []
    if category:
        where.append("l.category = ?")
        params.append(category)
    if level in ("info", "warning", "error"):
        where.append("l.level = ?")
        params.append(level)
    if q:
        where.append("l.message LIKE ?")
        params.append(f"%{q}%")
    clause = "WHERE " + " AND ".join(where) if where else ""
    rows = db.query_all(
        "SELECT l.*, u.username FROM system_logs l LEFT JOIN users u ON u.id = l.user_id "
        f"{clause} ORDER BY l.created_at DESC, l.id DESC LIMIT 500",
        params,
    )
    categories = db.query_all("SELECT DISTINCT category FROM system_logs ORDER BY category")
    context.update(
        {
            "logs": rows,
            "categories": [row["category"] for row in categories],
            "category": category,
            "level": level,
            "q": q,
            "level_labels": LEVEL_LABELS,
            "category_labels": CATEGORY_LABELS,
        }
    )
    return render(request, "admin/logs.html", context)


# ---------------------------------------------------------------------- 设置
@router.get("/admin/settings")
def admin_settings(request: Request, admin: dict = Depends(admin_page_user)):
    context = _base_context(admin, "settings", "系统设置")
    update_check = None
    update_error = ""
    if request.query_params.get("check", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        try:
            update_check = check_for_server_update(
                settings.version, settings.update_feed_url
            )
            db.log_event(
                "info",
                "system",
                f"检查服务端更新：当前 v{update_check.current_version}，"
                f"最新 v{update_check.latest_version}，"
                f"{'发现新版本' if update_check.update_available else '已是最新'}",
                user_id=admin["id"],
                ip=client_ip(request),
            )
        except Exception as exc:
            update_error = str(exc) or exc.__class__.__name__
            db.log_event(
                "warning",
                "system",
                f"检查服务端更新失败：{update_error}",
                user_id=admin["id"],
                ip=client_ip(request),
            )
    context.update(
        {
            "cfg": settings,
            "web_base_value": db.get_setting("web_base", settings.web_base),
            "auto_approve_value": auto_approve_enabled(),
            "oss": oss_service.status(),
            "secret_is_default": not bool(db.get_setting("jwt_secret", "")),
            "update_check": update_check,
            "update_error": update_error,
            "update_feed_url": settings.update_feed_url,
            "message": request.query_params.get("msg", ""),
            "message_type": request.query_params.get("type", "success"),
        }
    )
    return render(request, "admin/settings.html", context)


@router.post("/admin/settings")
def admin_settings_update(
    request: Request,
    web_base: str = Form(default=""),
    registration_auto_approve: str = Form(default=""),
    admin: dict = Depends(admin_page_user),
):
    value = web_base.strip().rstrip("/")
    if value and not value.startswith(("http://", "https://")):
        return _redirect("/admin/settings", "域名必须以 http(s):// 开头", "error")
    db.set_setting("web_base", value)
    auto_approve = registration_auto_approve.strip().lower() in ("1", "true", "yes", "on")
    set_auto_approve(auto_approve)
    db.log_event(
        "info",
        "admin",
        f"更新系统设置：域名={value or '（使用请求地址）'}，"
        f"注册自动审核={'开启' if auto_approve else '关闭'}",
        user_id=admin["id"],
        ip=client_ip(request),
    )
    return _redirect("/admin/settings", "系统设置已保存")


@router.post("/admin/settings/rotate-secret")
def admin_rotate_secret(
    request: Request,
    new_secret: str = Form(...),
    admin: dict = Depends(admin_page_user),
):
    if len(new_secret.strip()) < 16:
        return _redirect("/admin/settings", "新密钥至少需要 16 个字符", "error")
    db.set_setting("jwt_secret", new_secret.strip())
    db.log_event(
        "warning",
        "admin",
        "轮换后端签名密钥，所有登录会话已失效",
        user_id=admin["id"],
        ip=client_ip(request),
    )
    response = RedirectResponse(
        "/admin/login?" + urlencode({"msg": "密钥已轮换，请重新登录"}), status_code=303
    )
    response.delete_cookie(ADMIN_COOKIE, path="/")
    return response
