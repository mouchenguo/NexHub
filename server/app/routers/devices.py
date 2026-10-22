"""同账号在线设备心跳 API。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request

from .. import db
from ..common import client_ip
from ..config import settings
from ..deps import get_current_user
from ..schemas import DeviceHeartbeatIn

router = APIRouter(prefix="/api/devices", tags=["devices"])


def _cutoff() -> str:
    now = datetime.now(timezone.utc) - timedelta(seconds=settings.device_online_seconds)
    return now.isoformat(timespec="seconds")


def _payload(row: dict) -> dict:
    return {
        "device_id": row["device_id"],
        "name": row["name"],
        "tunnel_address": row.get("tunnel_address") or "",
        "online": True,
        "last_seen": row.get("last_seen"),
    }


@router.post("/heartbeat")
def heartbeat(
    payload: DeviceHeartbeatIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    now = db.utcnow()
    db.execute(
        "INSERT INTO devices(owner_id, device_id, name, tunnel_address, status, last_seen, created_at) "
        "VALUES(?, ?, ?, ?, 'online', ?, ?) "
        "ON CONFLICT(owner_id, device_id) DO UPDATE SET "
        "name = excluded.name, tunnel_address = excluded.tunnel_address, "
        "status = 'online', last_seen = excluded.last_seen",
        (
            int(user["id"]),
            payload.device_id.strip(),
            payload.name.strip(),
            payload.tunnel_address.strip().rstrip("/"),
            now,
            now,
        ),
    )
    db.log_event(
        "info",
        "device",
        f"设备上线：{payload.name.strip()}",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return {"ok": True, "online_seconds": settings.device_online_seconds}


@router.get("")
def list_devices(
    exclude_device_id: str = "",
    user: dict = Depends(get_current_user),
):
    cutoff = _cutoff()
    db.execute(
        "DELETE FROM devices WHERE owner_id = ? AND last_seen < ?",
        (int(user["id"]), cutoff),
    )
    rows = db.query_all(
        "SELECT * FROM devices WHERE owner_id = ? AND status = 'online' "
        "AND last_seen >= ? AND device_id != ? ORDER BY name COLLATE NOCASE, id",
        (int(user["id"]), cutoff, exclude_device_id.strip()),
    )
    return {"items": [_payload(row) for row in rows]}


@router.delete("/{device_id}")
def offline(
    device_id: str,
    request: Request,
    user: dict = Depends(get_current_user),
):
    db.execute(
        "DELETE FROM devices WHERE owner_id = ? AND device_id = ?",
        (int(user["id"]), device_id),
    )
    db.log_event(
        "info",
        "device",
        f"设备离线：{device_id}",
        user_id=user["id"],
        ip=client_ip(request),
    )
    return {"ok": True}
