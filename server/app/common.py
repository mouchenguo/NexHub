"""序列化、容量统计、路径校验等公共工具。"""
from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from . import db
from .config import settings

GIB = 1024 ** 3


def normalize_account_text(value: str) -> str:
    """统一账号/昵称文本：去首尾空格并转为 Unicode NFC。"""
    return unicodedata.normalize("NFC", (value or "").strip())


def quota_bytes() -> int:
    try:
        return max(
            0,
            int(db.get_setting("oss_quota_bytes", str(settings.oss_default_quota_gb * GIB))),
        )
    except ValueError:
        return settings.oss_default_quota_gb * GIB


def user_oss_used(user_id: int) -> int:
    row = db.query_one(
        "SELECT COALESCE(SUM(total_size), 0) AS used FROM shares "
        "WHERE owner_id = ? AND storage_type = 'oss' AND status = 'active'",
        (user_id,),
    )
    return int(row["used"]) if row else 0


def global_oss_used(active_only: bool = True) -> int:
    clause = (
        "WHERE storage_type = 'oss' AND status = 'active'"
        if active_only
        else "WHERE storage_type = 'oss'"
    )
    row = db.query_one(f"SELECT COALESCE(SUM(total_size), 0) AS used FROM shares {clause}")
    return int(row["used"]) if row else 0


def web_base(request=None) -> str:
    configured = db.get_setting("web_base", settings.web_base).rstrip("/")
    if configured:
        return configured
    if request is not None:
        return str(request.base_url).rstrip("/")
    return ""


def user_payload(user: dict) -> dict:
    used = user_oss_used(int(user["id"]))
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user.get("display_name") or user["username"],
        "role": user.get("role", "user"),
        "enabled": bool(user.get("enabled", 1)),
        "status": user.get("status") or "approved",
        "has_avatar": bool(user.get("avatar_ext")),
        "oss_used": used,
        "oss_quota": quota_bytes(),
        "created_at": user.get("created_at"),
        "last_login": user.get("last_login"),
    }


def tunnel_payload(tunnel: dict) -> dict:
    return {
        "id": int(tunnel["id"]),
        "name": tunnel["name"],
        "local_host": tunnel["local_host"],
        "local_port": int(tunnel["local_port"]),
        "nps_client_id": tunnel.get("nps_client_id"),
        "vkey": tunnel["vkey"],
        "server_addr": tunnel.get("server_addr") or "",
        "server_port": int(tunnel.get("server_port") or 8024),
        "enabled": bool(tunnel.get("enabled", 1)),
        "public_address": (
            f"{tunnel.get('server_addr')}:{tunnel['public_port']}"
            if tunnel.get("public_port")
            else ""
        ),
        "public_port": tunnel.get("public_port"),
        "created_at": tunnel.get("created_at"),
        "updated_at": tunnel.get("updated_at"),
    }


def share_manifest(share: dict) -> list[dict]:
    try:
        data = json.loads(share.get("manifest_json") or "[]")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict) and item.get("path")]
    except json.JSONDecodeError:
        pass
    return []


def share_payload(share: dict, current_user_id: int | None = None, base: str = "") -> dict:
    token = share["token"]
    owner = share.get("owner_username") or ""
    return {
        "id": share["id"],
        "token": token,
        "name": share["name"],
        "description": share.get("description", ""),
        "storage_type": share["storage_type"],
        "total_size": share["total_size"],
        "file_count": share["file_count"],
        "status": share["status"],
        "downloads": share["downloads"],
        "created_at": share["created_at"],
        "expires_at": share.get("expires_at"),
        "owner": owner,
        "owner_display": share.get("owner_display") or owner,
        "owner_id": share["owner_id"],
        "is_mine": current_user_id is not None
        and int(share["owner_id"]) == int(current_user_id),
        "public_url": f"{base}/s/{token}" if base else "",
    }


def normalize_rel_path(path: str) -> str:
    """规范化共享内相对路径，拒绝目录穿越。"""
    raw = (path or "").replace("\\", "/")
    pure = PurePosixPath(raw)
    parts = [part for part in pure.parts if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("非法文件路径")
    if pure.is_absolute() or raw.startswith("/") or ":" in parts[0]:
        raise ValueError("非法文件路径")
    return "/".join(parts)


def sanitize_manifest(files: list[dict]) -> list[dict]:
    normalized: dict[str, int] = {}
    for item in files:
        path = normalize_rel_path(str(item.get("path", "")))
        size = int(item.get("size") or 0)
        if size < 0:
            raise ValueError("文件大小不能为负数")
        normalized[path] = size
    if not normalized:
        raise ValueError("共享至少需要包含一个文件")
    return [{"path": path, "size": size} for path, size in normalized.items()]


def human_size(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "--"
    if number < 1024:
        return f"{int(number)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        number /= 1024
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}"
    return f"{number:.1f} TB"


def format_dt(value: str | None, with_time: bool = True) -> str:
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone()
        return local.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")
    except ValueError:
        return value


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def log_download(
    share_id: int | None,
    user_id: int | None,
    file_path: str,
    file_size: int,
    ip: str,
    status: str = "success",
) -> None:
    db.execute(
        "INSERT INTO download_logs(share_id, user_id, file_path, file_size, ip, status, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?, ?)",
        (share_id, user_id, file_path, file_size, ip, status, db.utcnow()),
    )
    db.log_event(
        "info",
        "download",
        f"下载文件 {file_path}（{human_size(file_size)}）",
        user_id=user_id,
        ip=ip,
    )


