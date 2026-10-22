"""nps 穿透配置与状态探测。"""
from __future__ import annotations

import socket
import time

from . import db
from .config import settings


def get_config() -> dict:
    return {
        "server_addr": db.get_setting("nps_server_addr", settings.nps_server_addr),
        "server_port": int(db.get_setting("nps_server_port", str(settings.nps_server_port)) or 8024),
        "vkey": db.get_setting("nps_vkey", settings.nps_vkey),
        "api_url": db.get_setting("nps_api_url", settings.nps_api_url),
        "auth_key": db.get_setting("nps_auth_key", settings.nps_auth_key),
        "domain": db.get_setting("nps_domain", settings.nps_domain),
    }


def save_config(data: dict) -> None:
    for key in (
        "nps_server_addr",
        "nps_server_port",
        "nps_vkey",
        "nps_api_url",
        "nps_auth_key",
        "nps_domain",
    ):
        if key in data:
            db.set_setting(key, str(data[key] or ""))


def check_status(timeout: float = 2.0) -> dict:
    """TCP 探测 nps Bridge 端口，避免依赖 nps 管理 API 版本差异。"""
    config = get_config()
    host = config["server_addr"]
    port = int(config["server_port"])
    if not host:
        return {"online": False, "message": "未配置 nps 服务端地址", "latency_ms": None}
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency = int((time.perf_counter() - started) * 1000)
            return {
                "online": True,
                "message": f"nps 端口可达（{latency} ms）",
                "latency_ms": latency,
            }
    except OSError as exc:
        return {"online": False, "message": f"无法连接 {host}:{port}（{exc}）", "latency_ms": None}


def active_temp_tunnels() -> list[dict]:
    return db.query_all(
        "SELECT s.id, s.token, s.name, s.owner_id, s.created_at, s.total_size, "
        "u.username AS owner FROM shares s JOIN users u ON u.id = s.owner_id "
        "WHERE s.storage_type = 'temp' AND s.status = 'active' "
        "ORDER BY s.created_at DESC"
    )
