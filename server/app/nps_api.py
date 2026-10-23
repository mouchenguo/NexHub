"""NPS Web API 客户端。

NPS 的 Web API 使用 ``md5(auth_key + timestamp)`` 鉴权，密钥只保存在 ECS
服务端；NexHub 客户端不会接触这个密钥。

本项目目前使用 NPS 的 client API：
- POST /client/add/          新建 NPS 客户端（每台内网主机独立 vkey）
- POST /client/list/         查询客户端，取得 NPS 客户端 id / 状态
- POST /client/edit/         修改客户端备注
- POST /client/changestatus/ 启用/停用客户端
- POST /client/del/          删除客户端
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

import requests

from . import db
from .config import settings


class NpsApiError(RuntimeError):
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


def _api_config() -> tuple[str, str]:
    api_url = db.get_setting("nps_api_url", settings.nps_api_url).strip().rstrip("/")
    auth_key = db.get_setting("nps_auth_key", settings.nps_auth_key).strip()
    if not api_url:
        raise NpsApiError("服务端未配置 NPS API 地址（NPS_API_URL）")
    if not auth_key:
        raise NpsApiError("服务端未配置 NPS API 鉴权密钥（NPS_AUTH_KEY）")
    return api_url, auth_key


def _auth_data(auth_key: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hashlib.md5(f"{auth_key}{timestamp}".encode("utf-8")).hexdigest()
    return {"auth_key": signature, "timestamp": timestamp}


def _post(path: str, data: dict[str, Any], timeout: int = 10) -> dict:
    api_url, auth_key = _api_config()
    payload = {key: "" if value is None else str(value) for key, value in data.items()}
    payload.update(_auth_data(auth_key))
    try:
        response = requests.post(
            f"{api_url}/{path.lstrip('/')}",
            data=payload,
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise NpsApiError(f"无法连接 NPS API：{exc}") from exc

    if 300 <= response.status_code < 400:
        raise NpsApiError("NPS API 鉴权失败，请检查 NPS_AUTH_KEY")
    if response.status_code >= 400:
        raise NpsApiError(
            f"NPS API 返回 HTTP {response.status_code}", response.status_code
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise NpsApiError("NPS API 返回了非 JSON 数据，可能鉴权失败") from exc
    if not isinstance(body, dict):
        raise NpsApiError("NPS API 返回格式不正确")
    if int(body.get("status", 1) or 0) == 0:
        raise NpsApiError(str(body.get("msg") or "NPS API 操作失败"))
    return body


def _row_value(row: dict, *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
        for actual, value in row.items():
            if actual.lower() == key.lower():
                return value
    return None


def list_clients(limit: int = 1000) -> list[dict]:
    body = _post(
        "/client/list/",
        {"offset": 0, "limit": limit, "search": "", "sort": "", "order": ""},
    )
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise NpsApiError("NPS 客户端列表格式不正确")
    return [row for row in rows if isinstance(row, dict)]


def find_client(vkey: str) -> dict | None:
    for _ in range(3):
        for row in list_clients():
            if str(_row_value(row, "VerifyKey", "vkey", "verify_key") or "") == vkey:
                return row
        time.sleep(0.2)
    return None


def create_client(remark: str, vkey: str) -> dict:
    """新建 NPS 客户端并返回其 NPS id。"""
    body = _post(
        "/client/add/",
        {
            "remark": remark,
            "vkey": vkey,
            "u": "",
            "p": "",
            "compress": 1,
            "crypt": 0,
            "config_conn_allow": 1,
            "rate_limit": 0,
            "flow_limit": 0,
            "max_conn": 0,
            "max_tunnel": 0,
        },
    )
    client_id = int(body.get("id") or 0)
    if client_id <= 0:
        row = find_client(vkey)
        if row is None:
            raise NpsApiError("NPS 客户端已提交，但未能查询到新客户端 id")
        client_id = int(_row_value(row, "Id", "id") or 0)
        if client_id <= 0:
            raise NpsApiError("NPS 客户端 id 无效")
        return {
            "id": client_id,
            "vkey": vkey,
            "remark": str(_row_value(row, "Remark", "remark") or remark),
            "status": bool(_row_value(row, "Status", "status")),
        }
    return {"id": client_id, "vkey": vkey, "remark": remark, "status": True}


def update_client(client_id: int, remark: str) -> None:
    row = _find_by_id(client_id)
    if row is None:
        raise NpsApiError("NPS 客户端不存在")
    cnf = row.get("Cnf") or row.get("cnf") or {}
    if not isinstance(cnf, dict):
        cnf = {}
    flow = row.get("Flow") or row.get("flow") or {}
    if not isinstance(flow, dict):
        flow = {}
    _post(
        "/client/edit/",
        {
            "id": client_id,
            "remark": remark,
            "vkey": _row_value(row, "VerifyKey", "vkey", "verify_key") or "",
            "u": _row_value(cnf, "U", "u") or "",
            "p": _row_value(cnf, "P", "p") or "",
            "compress": 1 if _row_value(cnf, "Compress", "compress") else 0,
            "crypt": 1 if _row_value(cnf, "Crypt", "crypt") else 0,
            "config_conn_allow": 1
            if _row_value(row, "ConfigConnAllow", "config_conn_allow")
            else 0,
            "rate_limit": _row_value(row, "RateLimit", "rate_limit") or 0,
            "flow_limit": _row_value(flow, "FlowLimit", "flow_limit") or 0,
            "max_conn": _row_value(row, "MaxConn", "max_conn") or 0,
            "max_tunnel": _row_value(row, "MaxTunnelNum", "max_tunnel") or 0,
        },
    )


def set_client_enabled(client_id: int, enabled: bool) -> None:
    data = {"id": client_id, "status": 1 if enabled else 0}
    # NPS 版本之间 action 路由大小写略有差异，404 时尝试兼容路径。
    for path in ("/client/changestatus/", "/client/changeStatus/", "/client/change_status/"):
        try:
            _post(path, data)
            return
        except NpsApiError as exc:
            if exc.status_code != 404:
                raise
    raise NpsApiError("NPS API 不支持修改客户端状态", 404)


def delete_client(client_id: int) -> None:
    _post("/client/del/", {"id": client_id})


def list_tunnels(mode: str = "tcp", limit: int = 1000) -> list[dict]:
    body = _post(
        "/index/gettunnel/",
        {"offset": 0, "limit": limit, "type": mode, "client_id": 0, "search": ""},
    )
    rows = body.get("rows") or []
    if not isinstance(rows, list):
        raise NpsApiError("NPS 隧道列表格式不正确")
    return [row for row in rows if isinstance(row, dict)]


def create_tcp_tunnel(
    client_id: int,
    target: str,
    public_port: int,
    remark: str,
) -> dict:
    body = _post(
        "/index/add/",
        {
            "type": "tcp",
            "remark": remark,
            "port": int(public_port),
            "target": target,
            "client_id": int(client_id),
        },
    )
    task_id = int(body.get("id") or 0)
    if task_id <= 0:
        raise NpsApiError("NPS TCP 隧道创建成功但未返回任务 id")
    return {"id": task_id, "port": int(public_port), "target": target}


def set_tunnel_enabled(task_id: int, enabled: bool) -> None:
    """切换单条隧道状态，不触碰同一主机下的其他隧道。

    NPS 的 IndexController 提供 Start/Stop 路由，而不是 changestatus；
    这两个操作只作用于指定 TCP 任务。
    """
    _post("/index/start/" if enabled else "/index/stop/", {"id": int(task_id)})


def delete_tunnel(task_id: int) -> None:
    _post("/index/del/", {"id": int(task_id)})


def _find_by_id(client_id: int) -> dict | None:
    for row in list_clients():
        if int(_row_value(row, "Id", "id") or 0) == int(client_id):
            return row
    return None

