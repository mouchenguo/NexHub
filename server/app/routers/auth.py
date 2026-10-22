"""账号注册、登录、资料与密码修改。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..common import client_ip, user_payload
from ..deps import get_current_user, issue_token
from ..schemas import ChangePasswordIn, LoginIn, RegisterIn
from ..security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register")
def register(payload: RegisterIn, request: Request):
    username = payload.username.strip()
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise HTTPException(status_code=409, detail="该用户名已被注册")
    password_hash, salt = hash_password(payload.password)
    user_id = db.execute(
        "INSERT INTO users(username, display_name, password_hash, password_salt, role, enabled, created_at) "
        "VALUES(?, ?, ?, ?, 'user', 1, ?)",
        (username, payload.display_name or username, password_hash, salt, db.utcnow()),
    )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    db.log_event(
        "info", "auth", f"新用户注册：{username}", user_id=user_id, ip=client_ip(request)
    )
    return {"token": issue_token(user), "user": user_payload(user)}


@router.post("/login")
def login(payload: LoginIn, request: Request):
    user = db.query_one("SELECT * FROM users WHERE username = ?", (payload.username.strip(),))
    if not user or not verify_password(
        payload.password, user["password_hash"], user["password_salt"]
    ):
        db.log_event(
            "warning",
            "auth",
            f"登录失败：{payload.username}",
            ip=client_ip(request),
        )
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    if not user.get("enabled"):
        raise HTTPException(status_code=403, detail="账号已被管理员禁用")
    db.execute("UPDATE users SET last_login = ? WHERE id = ?", (db.utcnow(), user["id"]))
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
    db.log_event(
        "info",
        "auth",
        f"用户登录：{user['username']}",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return {"token": issue_token(user), "user": user_payload(user)}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": user_payload(user), "quota": user_payload(user)["oss_quota"]}


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
