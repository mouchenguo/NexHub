"""NPS 主机（客户端）管理 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..common import client_ip
from ..config import settings
from ..deps import get_current_user
from ..nps_api import NpsApiError
from ..nps_hosts import create_host, delete_host, host_payload, list_hosts
from ..nps_service import get_config as get_nps_config
from ..schemas import NpsHostCreateIn

router = APIRouter(prefix="/api/nps/hosts", tags=["nps"])


def _connection() -> tuple[str, int]:
    config = get_nps_config()
    server_addr = str(config.get("server_addr") or "").strip()
    server_port = int(config.get("server_port") or settings.nps_server_port)
    if not server_addr:
        raise HTTPException(status_code=503, detail="服务端尚未配置 NPS 服务地址")
    return server_addr, server_port


@router.get("")
def list_nps_hosts(user: dict = Depends(get_current_user)):
    return {"items": [host_payload(row) for row in list_hosts(int(user["id"]))]}


@router.post("")
def create_nps_host(
    payload: NpsHostCreateIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="主机名称不能为空")
    server_addr, server_port = _connection()
    try:
        host = create_host(
            int(user["id"]),
            str(user["username"]),
            name,
            server_addr,
            server_port,
        )
    except NpsApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.log_event(
        "info",
        "nps",
        f"创建 NPS 主机：{name}（客户端 #{host.get('nps_client_id')}）",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return host_payload(host)


@router.delete("/{host_id}")
def remove_nps_host(
    host_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    from ..nps_hosts import get_host

    host = get_host(host_id, int(user["id"]))
    if not host:
        raise HTTPException(status_code=404, detail="NPS 主机不存在")
    try:
        delete_host(host)
    except (NpsApiError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.log_event(
        "info",
        "nps",
        f"删除 NPS 主机：{host.get('name')}",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return {"ok": True}
