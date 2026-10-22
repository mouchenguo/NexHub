"""客户端 nps 配置与连通性工具。"""
from __future__ import annotations

import socket
import time

from ..config import AppConfig


def temp_share_base(config: AppConfig, fallback: str = "") -> str:
    """临时共享对外地址：优先使用 nps 临时共享域名。"""
    return (config.nps.web_domain or fallback).rstrip("/")


def check_nps(server_addr: str, server_port: int, timeout: float = 2.0) -> dict:
    """从客户端探测 nps Bridge 端口。"""
    if not server_addr:
        return {"online": False, "message": "未填写 nps 服务端地址", "latency_ms": None}
    started = time.perf_counter()
    try:
        with socket.create_connection((server_addr, server_port), timeout=timeout):
            latency = int((time.perf_counter() - started) * 1000)
            return {
                "online": True,
                "message": f"nps 端口可达（{latency} ms）",
                "latency_ms": latency,
            }
    except OSError as exc:
        return {
            "online": False,
            "message": f"无法连接 {server_addr}:{server_port}（{exc}）",
            "latency_ms": None,
        }
