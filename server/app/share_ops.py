"""共享查询、OSS 容量自动清理与删除操作。"""
from __future__ import annotations

from . import db
from .common import global_oss_used, quota_bytes
from .config import settings
from .nps_api import NpsApiError, create_tcp_tunnel, delete_tunnel, list_tunnels
from .nps_service import get_config as get_nps_config
from .oss_service import oss_service

SHARE_SELECT = (
    "SELECT s.*, u.username AS owner_username, u.display_name AS owner_display "
    "FROM shares s JOIN users u ON u.id = s.owner_id "
)


def fetch_share(share_id: int) -> dict | None:
    return db.query_one(SHARE_SELECT + "WHERE s.id = ?", (share_id,))


def fetch_share_by_token(token: str) -> dict | None:
    return db.query_one(SHARE_SELECT + "WHERE s.token = ?", (token,))


def create_temp_route(owner_id: int, share_name: str) -> dict:
    """为临时共享创建独立 NPS TCP 隧道，返回公网 URL 和任务信息。"""
    tunnel = db.query_one(
        "SELECT * FROM nps_tunnels WHERE owner_id = ? AND enabled = 1 "
        "AND nps_client_id IS NOT NULL ORDER BY updated_at DESC, id DESC LIMIT 1",
        (owner_id,),
    )
    if not tunnel:
        raise NpsApiError("请先在「设置 → NPC 管理 → 隧道管理」创建本机 NPS 隧道")
    config = get_nps_config()
    server_addr = str(config.get("server_addr") or settings.nps_server_addr).strip()
    if not server_addr:
        raise NpsApiError("服务端未配置 NPS 公网地址")
    target = f"{tunnel['local_host']}:{tunnel['local_port']}"
    client_id = int(tunnel["nps_client_id"])
    used_ports = {
        int(row.get("Port") or 0)
        for row in list_tunnels("tcp")
        if row.get("Port")
    }
    last_error = ""
    for port in range(settings.nps_tunnel_port_start, settings.nps_tunnel_port_end + 1):
        if port in used_ports:
            continue
        try:
            task = create_tcp_tunnel(
                client_id,
                target,
                port,
                f"NexHub temp/{share_name}"[:160],
            )
            return {
                "local_url": f"http://{server_addr}:{port}",
                "task_id": int(task["id"]),
                "public_port": int(port),
            }
        except NpsApiError as exc:
            last_error = str(exc)
            continue
    raise NpsApiError(last_error or "没有可用的 NPS 公网端口")


def remove_temp_route(share: dict) -> None:
    task_id = share.get("nps_task_id")
    if not task_id:
        return
    try:
        delete_tunnel(int(task_id))
        db.execute(
            "UPDATE shares SET nps_task_id = NULL, public_port = NULL WHERE id = ?",
            (share["id"],),
        )
    except NpsApiError as exc:
        db.log_event(
            "warning",
            "nps",
            f"删除临时共享 TCP 任务失败（share={share['id']}）：{exc}",
        )


def delete_share_assets(share: dict) -> None:
    if share.get("storage_type") == "oss":
        oss_service.delete_prefix(share["token"])
    elif share.get("storage_type") == "temp":
        remove_temp_route(share)
    db.execute("DELETE FROM shares WHERE id = ?", (share["id"],))


def cancel_share(share: dict, actor_id: int | None, ip: str, reason: str = "手动取消") -> None:
    db.execute("UPDATE shares SET status = 'cancelled' WHERE id = ?", (share["id"],))
    if share.get("storage_type") == "temp":
        remove_temp_route(share)
    if share.get("storage_type") == "oss":
        # 关闭即释放实际 OSS 存储，避免继续产生容量与流量成本
        oss_service.delete_prefix(share["token"])
        db.execute("DELETE FROM oss_objects WHERE share_id = ?", (share["id"],))
    db.log_event(
        "info",
        "share",
        f"共享已取消：{share['name']}（{reason}）",
        user_id=actor_id,
        ip=ip,
    )


def remove_share(share: dict, actor_id: int | None, ip: str, automatic: bool = False) -> None:
    delete_share_assets(share)
    message = f"共享已删除：{share['name']}"
    if automatic:
        message = f"OSS 容量达到上限，自动清理最早共享：{share['name']}"
    db.log_event(
        "warning" if automatic else "info",
        "oss" if automatic else "share",
        message,
        user_id=actor_id,
        ip=ip,
    )


def cleanup_oss_capacity(needed_bytes: int, actor_id: int | None, ip: str) -> bool:
    """按创建时间从早到晚清理 OSS 共享，直到容量足够。

    返回 False 表示即使清理全部历史 OSS 共享仍无法容纳新共享。
    """
    quota = quota_bytes()
    used = global_oss_used(active_only=True)
    candidates = db.query_all(
        SHARE_SELECT
        + "WHERE s.storage_type = 'oss' AND s.status = 'active' "
        "ORDER BY s.created_at ASC"
    )
    for candidate in candidates:
        if used + needed_bytes <= quota:
            break
        remove_share(candidate, actor_id, ip, automatic=True)
        used = max(0, used - int(candidate["total_size"]))
    return used + needed_bytes <= quota
