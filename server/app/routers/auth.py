"""账号注册、登录、资料与密码修改。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..common import client_ip, normalize_account_text, user_payload
from ..compat import (
    require_compatible_client,
    request_client_version,
    server_info,
)
from ..deps import get_current_user, issue_token
from ..registration import STATUS_APPROVED, evaluate_registration, status_error
from ..schemas import ChangePasswordIn, LoginIn, RegisterIn, UpdateProfileIn
from ..security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(payload: RegisterIn, request: Request):
    require_compatible_client(request)
    username = normalize_account_text(payload.username)
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="该用户名已被注册")
    display_name = normalize_account_text(payload.display_name) or username
    status, review_message = evaluate_registration(username, display_name)
    password_hash, salt = hash_password(payload.password)
    user_id = db.execute(
        "INSERT INTO users(username, display_name, password_hash, password_salt, role, enabled, status, created_at) "
        "VALUES(?, ?, ?, ?, 'user', 1, ?, ?)",
        (username, display_name, password_hash, salt, status, db.utcnow()),
    )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    client_version = request_client_version(request) or "未上报"
    db.log_event(
        "info",
        "auth",
        f"新用户注册：{username}（{review_message}，客户端 v{client_version}）",
        user_id=user_id,
        ip=client_ip(request),
    )
    approved = status == STATUS_APPROVED
    return {
        "status": status,
        "auto_approved": approved,
        "requires_review": not approved,
        "message": (
            "注册成功，已通过自动审核"
            if approved
            else "注册成功，账号需管理员审核通过后即可登录"
        ),
        "token": issue_token(user) if approved else "",
        "user": user_payload(user),
        "server": server_info(),
    }


@router.post("/login")
def login(payload: LoginIn, request: Request):
    require_compatible_client(request)
    identifier = normalize_account_text(payload.username)
    if not identifier:
        raise HTTPException(status_code=400, detail="请输入用户名或昵称")
    user = db.query_one("SELECT * FROM users WHERE username = ?", (identifier,))
    if not user:
        matches = db.query_all(
            "SELECT * FROM users WHERE display_name = ? LIMIT 2", (identifier,)
        )
        if len(matches) > 1:
            db.log_event(
                "warning",
                "auth",
                f"昵称登录冲突：{identifier}",
                ip=client_ip(request),
            )
            raise HTTPException(
                status_code=409,
                detail="该昵称对应多个账号，请使用用户名登录",
            )
        user = matches[0] if matches else None
    if not user or not verify_password(
        payload.password, user["password_hash"], user["password_salt"]
    ):
        db.log_event(
            "warning",
            "auth",
            f"登录失败：{payload.username}",
            ip=client_ip(request),
        )
        raise HTTPException(status_code=401, detail="用户名/昵称或密码错误")
    status = str(user.get("status") or STATUS_APPROVED)
    if status != STATUS_APPROVED:
        db.log_event(
            "warning",
            "auth",
            f"审核未通过账号尝试登录：{user['username']}（{status}）",
            user_id=user["id"],
            ip=client_ip(request),
        )
        raise HTTPException(status_code=403, detail=status_error(status))
    if not user.get("enabled"):
        raise HTTPException(status_code=403, detail="账号已被管理员禁用")
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.utcnow(), user["id"]))
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    client_version = request_client_version(request) or "未上报"
    db.log_event(
        "info",
        "auth",
        f"用户登录：{user['username']}（客户端 v{client_version}）",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return {
        "token": issue_token(user),
        "user": user_payload(user),
        "server": server_info(),
    }


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {
        "user": user_payload(user),
        "quota": user_payload(user)["oss_quota"],
        "server": server_info(),
    }


@router.patch("/profile")
def update_profile(
    payload: UpdateProfileIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    display_name = normalize_account_text(payload.display_name)
    if not display_name:
        raise HTTPException(status_code=400, detail="昵称不能为空")
    if any(ord(char) < 32 or ord(char) == 127 for char in display_name):
        raise HTTPException(status_code=400, detail="昵称不能包含换行或控制字符")
    db.execute(
        "UPDATE users SET display_name = ? WHERE id = ?",
        (display_name, user["id"]),
    )
    updated = db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    db.log_event(
        "info",
        "account",
        f"用户 {user['username']} 修改昵称为：{display_name}",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return {"ok": True, "user": user_payload(updated)}


@router.post("/password")
def change_password(
    payload: ChangePasswordIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    if not verify_password(
        payload.old_password, user["password_hash"], user["password_salt"]
    ):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if payload.old_password == payload.new_password:
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")
    password_hash, salt = hash_password(payload.new_password)
    db.execute(
        "UPDATE users SET password_hash = ?, password_salt = ? WHERE id = ?",
        (password_hash, salt, user["id"]),
    )
    db.log_event(
        "info", "auth", "用户修改了登录密码", user_id=user["id"], ip=client_ip(request)
    )
    return {"ok": True}
