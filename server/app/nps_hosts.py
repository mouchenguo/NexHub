"""NPS 主机（client）与隧道的关系服务。

NPS 的客户端就是承载 NPC 连接的“主机”，一个主机只有一个 vkey；同一主机下
可以有多条 TCP 隧道。旧版本把每条隧道都建成独立客户端，导致本机 npc.conf
只能保留最后一个 vkey，其他隧道全部失联。本模块统一负责主机的创建、复用与修复。
"""
from __future__ import annotations

from . import db
from .nps_api import NpsApiError, create_client, delete_client, find_client, set_client_enabled
from .security import generate_token


def host_payload(row: dict) -> dict:
    return {
        "id": int(row["id"]),
        "name": row.get("name") or "",
        "nps_client_id": row.get("nps_client_id"),
        "vkey": row.get("vkey") or "",
        "server_addr": row.get("server_addr") or "",
        "server_port": int(row.get("server_port") or 8024),
        "enabled": bool(row.get("enabled", 1)),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def list_hosts(owner_id: int) -> list[dict]:
    return db.query_all(
        "SELECT h.*, u.username AS owner_username FROM nps_hosts h "
        "JOIN users u ON u.id = h.owner_id "
        "WHERE h.owner_id = ? ORDER BY h.created_at ASC, h.id ASC",
        (int(owner_id),),
    )


def get_host(host_id: int, owner_id: int) -> dict | None:
    return db.query_one(
        "SELECT h.*, u.username AS owner_username FROM nps_hosts h "
        "JOIN users u ON u.id = h.owner_id WHERE h.id = ? AND h.owner_id = ?",
        (int(host_id), int(owner_id)),
    )


def find_host(owner_id: int, name: str) -> dict | None:
    return db.query_one(
        "SELECT h.*, u.username AS owner_username FROM nps_hosts h "
        "JOIN users u ON u.id = h.owner_id "
        "WHERE h.owner_id = ? AND h.name = ? ORDER BY h.id ASC LIMIT 1",
        (int(owner_id), name),
    )


def _remark(username: str, name: str) -> str:
    return f"NexHub/{username}/{name}"[:160]


def _new_client(username: str, name: str) -> tuple[int, str]:
    last_error = "创建 NPS 客户端失败"
    for _ in range(3):
        vkey = generate_token(24)
        try:
            client = create_client(_remark(username, name), vkey)
            return int(client["id"]), vkey
        except NpsApiError as exc:
            last_error = str(exc)
            if "vkey" not in last_error.lower() and "密钥" not in last_error:
                break
    raise NpsApiError(last_error)


def create_host(
    owner_id: int,
    username: str,
    name: str,
    server_addr: str,
    server_port: int,
) -> dict:
    client_id, vkey = _new_client(username, name)
    now = db.utcnow()
    try:
        host_id = db.execute(
            "INSERT INTO nps_hosts(owner_id, name, nps_client_id, vkey, server_addr, "
            "server_port, enabled, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (int(owner_id), name, client_id, vkey, server_addr, int(server_port), now, now),
        )
    except Exception:
        try:
            delete_client(client_id)
        except NpsApiError:
            pass
        raise
    return get_host(host_id, owner_id) or {}


def repair_host(host: dict) -> dict:
    """确认 host 对应的 NPS 客户端仍存在；不存在时重建并同步所有隧道。"""
    host_id = int(host["id"])
    vkey = str(host.get("vkey") or "")
    client = find_client(vkey) if vkey else None
    if client is None:
        client_id, vkey = _new_client(
            str(host.get("owner_username") or "nexhub"),
            str(host.get("name") or "NPS 主机"),
        )
        db.execute(
            "UPDATE nps_hosts SET nps_client_id = ?, vkey = ?, updated_at = ? WHERE id = ?",
            (client_id, vkey, db.utcnow(), host_id),
        )
        db.execute(
            "UPDATE nps_tunnels SET nps_client_id = ?, vkey = ? WHERE host_id = ?",
            (client_id, vkey, host_id),
        )
        host = get_host(host_id, int(host["owner_id"])) or host
    elif not client.get("status", True):
        try:
            set_client_enabled(int(host["nps_client_id"]), True)
        except NpsApiError:
            pass
    return host


def ensure_host(
    owner_id: int,
    username: str,
    name: str,
    server_addr: str,
    server_port: int,
    *,
    exact_name: bool = True,
) -> dict:
    host = find_host(owner_id, name) if exact_name else None
    if host is None and not exact_name:
        existing = list_hosts(owner_id)
        host = existing[0] if existing else None
    if host is None:
        host = create_host(owner_id, username, name, server_addr, server_port)
    return repair_host(host)


def delete_host(host: dict) -> None:
    owner_id = int(host["owner_id"])
    host_id = int(host["id"])
    row = db.query_one(
        "SELECT COUNT(*) AS count FROM nps_tunnels WHERE host_id = ?", (host_id,)
    )
    if row and int(row["count"] or 0) > 0:
        raise ValueError("主机下还有隧道，请先删除或迁移隧道")
    if host.get("nps_client_id"):
        delete_client(int(host["nps_client_id"]))
    db.execute("DELETE FROM nps_hosts WHERE id = ? AND owner_id = ?", (host_id, owner_id))
