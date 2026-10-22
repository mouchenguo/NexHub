"""客户端仪表盘统计（按当前用户维度）。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request

from .. import db
from ..common import quota_bytes, share_payload, user_oss_used, web_base
from ..config import settings
from ..deps import get_current_user

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: dict = Depends(get_current_user),
):
    user_id = int(user["id"])
    active = db.query_one(
        "SELECT COUNT(*) AS count FROM shares WHERE owner_id = ? AND status = 'active'",
        (user_id,),
    )
    totals = db.query_one(
        "SELECT COALESCE(SUM(total_size), 0) AS bytes, COALESCE(SUM(downloads), 0) AS downloads "
        "FROM shares WHERE owner_id = ? AND status = 'active'",
        (user_id,),
    )
    recent = _recent_activity(user_id, request)
    started = getattr(request.app.state, "started_at", datetime.now(timezone.utc))
    uptime = datetime.now(timezone.utc) - started
    return {
        "active_shares": int(active["count"]) if active else 0,
        "oss_used": user_oss_used(user_id),
        "oss_quota": quota_bytes(),
        "total_downloads": int(totals["downloads"]) if totals else 0,
        "total_shared_bytes": int(totals["bytes"]) if totals else 0,
        "recent_activity": recent,
        "server": {
            "mode": "online",
            "version": settings.version,
            "status": "online",
            "uptime": f"{int(uptime.total_seconds() // 3600)}h",
        },
    }


def _recent_activity(user_id: int, request: Request) -> list[dict]:
    base = web_base(request)
    shares = db.query_all(
        "SELECT s.*, u.username AS owner_username, u.display_name AS owner_display "
        "FROM shares s JOIN users u ON u.id = s.owner_id "
        "WHERE s.owner_id = ? AND s.status = 'active' ORDER BY s.created_at DESC LIMIT 3",
        (user_id,),
    )
    downloads = db.query_all(
        "SELECT d.*, s.name AS share_name FROM download_logs d "
        "LEFT JOIN shares s ON s.id = d.share_id "
        "WHERE d.user_id = ? ORDER BY d.created_at DESC LIMIT 3",
        (user_id,),
    )
    activities = []
    for record in downloads:
        activities.append(
            {
                "title": f"下载了 {record['file_path'].rsplit('/', 1)[-1]}",
                "detail": f"来自共享「{record.get('share_name') or '未知'}」",
                "time": record["created_at"],
                "tone": "accent",
            }
        )
    for share in shares[:2]:
        info = share_payload(share, user_id, base)
        activities.append(
            {
                "title": f"共享「{info['name']}」正在分发",
                "detail": "存储方式："
                + ("OSS 长期缓存" if share["storage_type"] == "oss" else "临时穿透"),
                "time": share["created_at"],
                "tone": "success",
            }
        )
    activities.sort(key=lambda item: item["time"], reverse=True)
    return activities[:5]
