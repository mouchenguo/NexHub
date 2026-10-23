"""NPS 隧道管理 API。

NPS 主机（客户端）拥有唯一 vkey，多条 TCP 隧道共享同一主机；创建隧道不会
重复创建客户端。客户端拿到主机的 vkey 后写入本机
``C:\\Program Files\\npc\\conf\\npc.conf``，再重启 NPC。
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import db
from ..common import client_ip, tunnel_payload
from ..config import settings
from ..deps import get_current_user
from ..nps_api import (
    NpsApiError,
    create_tcp_tunnel,
    delete_tunnel,
    list_tunnels as list_nps_tunnels,
    set_tunnel_enabled,
    update_client,
)
from ..nps_hosts import ensure_host, get_host, host_payload, repair_host
from ..nps_service import get_config as get_nps_config
from ..schemas import NpsTunnelCreateIn, NpsTunnelUpdateIn

router = APIRouter(prefix="/api/tunnels", tags=["tunnels"])

LOCAL_HOST_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _validate_local_host(value: str) -> str:
    host = value.strip()
    if not host or not LOCAL_HOST_RE.match(host) or ".." in host:
        raise HTTPException(status_code=400, detail="本地服务 IP/主机名格式不正确")
    return host


def _row(tunnel_id: int) -> dict | None:
    return db.query_one("SELECT * FROM nps_tunnels WHERE id = ?", (tunnel_id,))


def _require_owner(tunnel: dict, user: dict) -> None:
    if int(tunnel["owner_id"]) != int(user["id"]) and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="只能管理自己的隧道")


def _nps_connection() -> tuple[str, int]:
    config = get_nps_config()
    server_addr = str(config.get("server_addr") or "").strip()
    server_port = int(config.get("server_port") or 8024)
    if not server_addr:
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置 NPS 服务地址，请在管理后台 /admin/nps 配置",
        )
    return server_addr, server_port


def _remark(user: dict, name: str) -> str:
    return f"NexHub/{user['username']}/{name}"[:160]


def _resolve_host(
    payload: NpsTunnelCreateIn,
    user: dict,
    server_addr: str,
    server_port: int,
) -> dict:
    """隧道只能挂在 NPS 主机下；兼容旧客户端时自动复用/创建默认主机。"""
    owner_id = int(user["id"])
    if payload.host_id:
        host = get_host(int(payload.host_id), owner_id)
        if not host:
            raise HTTPException(status_code=404, detail="NPS 主机不存在")
        return repair_host(host)
    host_name = payload.host_name.strip() or f"Host-{user['username']}"
    host = ensure_host(
        owner_id,
        str(user["username"]),
        host_name,
        server_addr,
        int(server_port),
        exact_name=bool(payload.host_name.strip()),
    )
    if not host.get("nps_client_id") or not host.get("vkey"):
        raise HTTPException(status_code=502, detail="NPS 主机没有可用的客户端/vkey")
    return host


def _create_tunnel_with_host(
    payload: NpsTunnelCreateIn,
    request: Request,
    user: dict,
    *,
    host: dict | None = None,
) -> dict:
    local_host = _validate_local_host(payload.local_host)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="隧道名称不能为空")
    server_addr, server_port = _nps_connection()
    host = host or _resolve_host(payload, user, server_addr, server_port)
    nps_client_id = int(host["nps_client_id"])
    vkey = str(host["vkey"])
    now = db.utcnow()
    try:
        tunnel_id = db.execute(
            "INSERT INTO nps_tunnels("
            "owner_id, host_id, name, local_host, local_port, nps_client_id, vkey, "
            "server_addr, server_port, enabled, created_at, updated_at"
            ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
            (
                int(user["id"]),
                int(host["id"]),
                name,
                local_host,
                int(payload.local_port),
                nps_client_id,
                vkey,
                server_addr,
                server_port,
                now,
                now,
            ),
        )
    except Exception as exc:
        # 主机是共享资源，数据库失败时不能删除主机/NPS 客户端。
        raise HTTPException(status_code=500, detail=f"保存隧道失败：{exc}") from exc

    # 每条隧道都必须有对应的 TCP 任务；rdp_task 只表示用途，不再决定是否建任务。
    try:
        _create_rdp_task(
            tunnel_id,
            int(user["id"]),
            nps_client_id,
            local_host,
            int(payload.local_port),
            server_addr,
        )
    except (NpsApiError, ValueError) as exc:
        db.execute("DELETE FROM nps_tunnels WHERE id = ?", (tunnel_id,))
        raise HTTPException(status_code=502, detail=f"创建 TCP 隧道任务失败：{exc}") from exc

    row = _row(tunnel_id)
    db.log_event(
        "info",
        "nps",
        f"创建 NPS 隧道：{name}（主机 #{host['id']} / 客户端 #{nps_client_id}）",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return tunnel_payload(row) if row else {}


@router.get("")
def list_tunnels(user: dict = Depends(get_current_user)):
    rows = db.query_all(
        "SELECT * FROM nps_tunnels WHERE owner_id = ? ORDER BY created_at DESC, id DESC",
        (int(user["id"]),),
    )
    return {"items": [tunnel_payload(row) for row in rows]}


@router.post("")
def create_tunnel(
    payload: NpsTunnelCreateIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    return _create_tunnel_with_host(payload, request, user)


@router.post("/auto")
def auto_tunnel(
    payload: NpsTunnelCreateIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    """幂等地为当前设备准备 NPS 主机与 TCP 隧道，供客户端一键配置 NPC。"""
    _validate_local_host(payload.local_host)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="设备名称不能为空")
    server_addr, server_port = _nps_connection()

    existing = db.query_one(
        "SELECT * FROM nps_tunnels WHERE owner_id = ? AND name = ? AND local_port = ? "
        "ORDER BY id ASC LIMIT 1",
        (int(user["id"]), name, int(payload.local_port)),
    )
    # 旧版本为每条隧道单独建过客户端；迁移后优先复用该隧道已有的主机，
    # 避免一键自动配置又新建一个同名 Host-* 客户端。
    host = None
    if existing and existing.get("host_id"):
        host = get_host(int(existing["host_id"]), int(user["id"]))
    if host is None:
        host_name = payload.host_name.strip() or f"Host-{user['username']}"
        host = ensure_host(
            int(user["id"]),
            str(user["username"]),
            host_name,
            server_addr,
            int(server_port),
            exact_name=True,
        )

    if existing and int(existing.get("host_id") or 0) == int(host["id"]):
        row = _repair_auto_tunnel(
            existing,
            user,
            payload.rdp_task,
            server_addr,
            server_port,
            host,
        )
        created = False
    elif existing:
        # 旧客户端创建的隧道属于旧主机；不要删除旧主机，直接把该隧道迁移到
        # 当前设备主机，并重新建立 TCP 任务。
        db.execute(
            "UPDATE nps_tunnels SET host_id = ?, nps_client_id = ?, vkey = ?, "
            "nps_task_id = NULL, public_port = NULL WHERE id = ?",
            (int(host["id"]), host["nps_client_id"], host["vkey"], int(existing["id"])),
        )
        row = _repair_auto_tunnel(
            _row(int(existing["id"])),
            user,
            payload.rdp_task,
            server_addr,
            server_port,
            host,
        )
        created = False
    else:
        tunnel = _create_tunnel_with_host(payload, request, user, host=host)
        row = _row(int(tunnel["id"]))
        created = True

    if not row:
        raise HTTPException(status_code=500, detail="NPS 隧道记录不存在")
    db.log_event(
        "info",
        "nps",
        f"{'创建' if created else '复用'}自动配置隧道：{row['name']}"
        f"（主机 #{host['id']} / 公网端口 {row.get('public_port') or '--'}）",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return {"created": created, "tunnel": tunnel_payload(row), "host": host_payload(host)}


def _nps_row_id(row: dict) -> int:
    return int(row.get("Id") or row.get("id") or 0)


def _repair_auto_tunnel(
    tunnel: dict,
    user: dict,
    rdp_task: bool,
    server_addr: str,
    server_port: int,
    host: dict,
) -> dict | None:
    """确认共享主机和该隧道对应的 TCP 任务仍存在。"""
    tunnel_id = int(tunnel["id"])
    host = repair_host(host)
    nps_client_id = int(host["nps_client_id"])
    if nps_client_id <= 0:
        raise HTTPException(status_code=502, detail="NPS 主机客户端 id 无效")

    now = db.utcnow()
    db.execute(
        "UPDATE nps_tunnels SET host_id = ?, nps_client_id = ?, vkey = ?, "
        "server_addr = ?, server_port = ?, enabled = 1, updated_at = ? WHERE id = ?",
        (
            int(host["id"]),
            nps_client_id,
            host["vkey"],
            server_addr,
            int(server_port),
            now,
            tunnel_id,
        ),
    )
    tunnel = _row(tunnel_id)

    if rdp_task:
        task_id = int(tunnel.get("nps_task_id") or 0)
        try:
            tasks = list_nps_tunnels("tcp")
        except NpsApiError as exc:
            raise HTTPException(status_code=502, detail=f"查询 NPS 隧道失败：{exc}") from exc
        if not task_id or not any(_nps_row_id(row) == task_id for row in tasks):
            local_host = str(tunnel.get("local_host") or "127.0.0.1")
            try:
                _create_rdp_task(
                    tunnel_id,
                    int(user["id"]),
                    nps_client_id,
                    local_host,
                    int(tunnel["local_port"]),
                    server_addr,
                )
            except (NpsApiError, ValueError) as exc:
                raise HTTPException(
                    status_code=502, detail=f"创建 NPS TCP 隧道失败：{exc}"
                ) from exc
            tunnel = _row(tunnel_id)
    return tunnel


def _create_rdp_task(
    tunnel_id: int,
    owner_id: int,
    nps_client_id: int,
    local_host: str,
    local_port: int,
    server_addr: str,
) -> None:
    used = {
        int(row.get("Port") or 0)
        for row in list_nps_tunnels("tcp")
        if row.get("Port")
    }
    last_error = ""
    for port in range(settings.nps_tunnel_port_start, settings.nps_tunnel_port_end + 1):
        if port in used:
            continue
        try:
            task = create_tcp_tunnel(
                nps_client_id,
                f"{local_host}:{local_port}",
                port,
                f"NexHub RDP/{owner_id}"[:160],
            )
            db.execute(
                "UPDATE nps_tunnels SET nps_task_id = ?, public_port = ? WHERE id = ?",
                (int(task["id"]), int(port), tunnel_id),
            )
            return
        except NpsApiError as exc:
            last_error = str(exc)
    raise ValueError(last_error or "没有可用的 NPS 公网端口")


@router.patch("/{tunnel_id}")
def edit_tunnel(
    tunnel_id: int,
    payload: NpsTunnelUpdateIn,
    request: Request,
    user: dict = Depends(get_current_user),
):
    tunnel = _row(tunnel_id)
    if not tunnel:
        raise HTTPException(status_code=404, detail="隧道不存在")
    _require_owner(tunnel, user)
    local_host = _validate_local_host(payload.local_host)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="隧道名称不能为空")

    if tunnel.get("nps_client_id"):
        try:
            update_client(int(tunnel["nps_client_id"]), _remark(user, name))
        except NpsApiError as exc:
            # NPS 备注同步失败不阻止本地编辑，下次保存可重试。
            db.log_event(
                "warning",
                "nps",
                f"同步 NPS 客户端备注失败：{exc}",
                user_id=int(user["id"]),
                ip=client_ip(request),
            )

    now = db.utcnow()
    db.execute(
        "UPDATE nps_tunnels SET name = ?, local_host = ?, local_port = ?, updated_at = ? "
        "WHERE id = ?",
        (name, local_host, int(payload.local_port), now, tunnel_id),
    )
    row = _row(tunnel_id)
    db.log_event(
        "info",
        "nps",
        f"编辑 NPS 隧道：{name}",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return tunnel_payload(row) if row else {}


@router.post("/{tunnel_id}/start")
def start_tunnel(
    tunnel_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    return _set_state(tunnel_id, True, request, user)


@router.post("/{tunnel_id}/stop")
def stop_tunnel(
    tunnel_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    return _set_state(tunnel_id, False, request, user)


def _set_state(tunnel_id: int, enabled: bool, request: Request, user: dict) -> dict:
    tunnel = _row(tunnel_id)
    if not tunnel:
        raise HTTPException(status_code=404, detail="隧道不存在")
    _require_owner(tunnel, user)
    if tunnel.get("nps_task_id"):
        try:
            set_tunnel_enabled(int(tunnel["nps_task_id"]), enabled)
        except NpsApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    db.execute(
        "UPDATE nps_tunnels SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, db.utcnow(), tunnel_id),
    )
    row = _row(tunnel_id)
    db.log_event(
        "info",
        "nps",
        f"{'启用' if enabled else '停用'} NPS 隧道：{tunnel['name']}",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return tunnel_payload(row) if row else {}


@router.delete("/{tunnel_id}")
def remove_tunnel(
    tunnel_id: int,
    request: Request,
    user: dict = Depends(get_current_user),
):
    tunnel = _row(tunnel_id)
    if not tunnel:
        raise HTTPException(status_code=404, detail="隧道不存在")
    _require_owner(tunnel, user)
    if tunnel.get("nps_task_id"):
        try:
            delete_tunnel(int(tunnel["nps_task_id"]))
        except NpsApiError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"删除 NPS TCP 任务失败，未删除本地记录：{exc}",
            ) from exc
    # 主机（NPS 客户端）是共享资源，删除隧道时只删除 TCP 任务。
    # 主机由用户在“主机”管理中单独删除，避免误删其他隧道共用的 vkey。
    db.execute("DELETE FROM nps_tunnels WHERE id = ?", (tunnel_id,))
    db.log_event(
        "info",
        "nps",
        f"删除 NPS 隧道：{tunnel['name']}",
        user_id=int(user["id"]),
        ip=client_ip(request),
    )
    return {"ok": True}

