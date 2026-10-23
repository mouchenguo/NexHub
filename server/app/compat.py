"""服务端协议版本、能力声明与最低客户端版本检查。"""
from __future__ import annotations

from fastapi import HTTPException, Request

from .config import settings
from .update_service import version_key

# 协议版本只在 API 发生不兼容变化时递增。
API_VERSION = 1
MIN_CLIENT_VERSION = "0.1.0-beta"

# 客户端通过 /api/health 读取能力列表，避免仅凭版本号猜测功能。
FEATURES = (
    "auth",
    "registration",
    "registration_review",
    "shares",
    "devices",
    "nps_tunnels",
    "auto_tunnel",
    "server_update_check",
)


def server_info() -> dict:
    return {
        "version": settings.version,
        "api_version": API_VERSION,
        "min_client_version": MIN_CLIENT_VERSION,
        "features": list(FEATURES),
    }


def request_client_version(request: Request) -> str:
    raw = request.headers.get("x-nexhub-client-version", "").strip()
    return raw.lstrip("v")


def require_compatible_client(request: Request) -> None:
    """明确上报了过低版本才拒绝；未上报版本的旧客户端保持兼容。"""
    client_version = request_client_version(request)
    if not client_version:
        return
    if version_key(client_version) < version_key(MIN_CLIENT_VERSION):
        raise HTTPException(
            status_code=426,
            detail=(
                f"客户端 v{client_version} 版本过低，最低要求 v{MIN_CLIENT_VERSION}，"
                "请升级客户端后再试"
            ),
        )
