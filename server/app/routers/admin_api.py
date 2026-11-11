"""管理员只读概览接口（客户端仪表盘使用）。

- 鉴权复用 ``get_admin_user``：未登录 401，普通用户 403；
- 只返回用量、最近系统日志与服务器信息，不含用户列表、密钥等敏感字段。
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request

from .. import db
from ..common import global_oss_used, quota_bytes, web_base
from ..config import settings
from ..deps import get_admin_user

router = APIRouter(prefix="/api/admin", tags=["admin-api"])

MAX_LOGS = 50


def _count(sql: str) -> int:
    row = db.query_one(sql)
    return int(row["count"]) if row else 0


@router.get("/overview")
def overview(
    request: Request,
    limit: int = Query(default=30, ge=1, le=MAX_LOGS),
    admin: dict = Depends(get_admin_user),
):
    oss_used = int(global_oss_used(active_only=True))
    oss_quota = int(quota_bytes())
    oss_percent = int(round(oss_used / oss_quota * 100)) if oss_quota else 0

    rows = db.query_all(
        "SELECT l.created_at, l.level, l.category, l.message, u.username "
        "FROM system_logs l LEFT JOIN users u ON u.id = l.user_id "
        "ORDER BY l.created_at DESC, l.id DESC LIMIT ?",
        (int(limit),),
    )
    started = getattr(request.app.state, "started_at", datetime.now(timezone.utc))
    uptime = datetime.now(timezone.utc) - started
    base = web_base(request)
    return {
        "usage": {
            "oss_used": oss_used,
            "oss_quota": oss_quota,
            "oss_percent": oss_percent,
            "active_shares": _count(
                "SELECT COUNT(*) AS count FROM shares WHERE status = 'active'"
            ),
            "downloads": _count("SELECT COUNT(*) AS count FROM download_logs"),
            "users": _count("SELECT COUNT(*) AS count FROM users"),
            "pending_users": _count(
                "SELECT COUNT(*) AS count FROM users WHERE status = 'pending'"
            ),
        },
        "logs": [
            {
                "created_at": row["created_at"],
                "level": row["level"],
                "category": row["category"],
                "username": row.get("username") or "",
                "message": row["message"],
            }
            for row in rows
        ],
        "server": {
            "version": settings.version,
            "uptime": f"{int(uptime.total_seconds() // 3600)}h",
            "web_url": f"{base.rstrip('/')}/admin" if base else "/admin",
        },
    }
