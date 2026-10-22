"""鉴权依赖：Bearer Token 与管理员 Web 会话。"""
from __future__ import annotations

from fastapi import Header, HTTPException, Request

from . import db
from .security import create_token, decode_token

ADMIN_COOKIE = "nexhub_admin"


def _user_from_token(token: str) -> dict | None:
    payload = decode_token(token)
    if not payload:
        return None
    user = db.query_one("SELECT * FROM users WHERE id = ?", (payload.get("sub"),))
    if not user or not user.get("enabled"):
        return None
    return user


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def get_current_user(
    request: Request, authorization: str | None = Header(default=None)
) -> dict:
    token = _bearer_token(authorization) or request.cookies.get(ADMIN_COOKIE, "")
    user = _user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录状态已失效")
    return user


def get_optional_user(
    request: Request, authorization: str | None = Header(default=None)
) -> dict | None:
    token = _bearer_token(authorization) or request.cookies.get(ADMIN_COOKIE, "")
    return _user_from_token(token)


def get_admin_user(
    request: Request, authorization: str | None = Header(default=None)
) -> dict:
    token = _bearer_token(authorization) or request.cookies.get(ADMIN_COOKIE, "")
    user = _user_from_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="管理员未登录")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def admin_page_user(request: Request) -> dict:
    """管理后台页面依赖：未登录时 303 跳转到登录页。"""
    token = request.cookies.get(ADMIN_COOKIE, "")
    user = _user_from_token(token)
    if not user or user.get("role") != "admin":
        raise HTTPException(
            status_code=303, headers={"Location": "/admin/login"}, detail="redirect"
        )
    return user


def issue_token(user: dict) -> str:
    return create_token(int(user["id"]), user.get("role", "user"))
