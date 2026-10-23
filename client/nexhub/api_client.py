"""ECS API 通信模块。

所有请求的 base URL 都实时读取 ``AppConfig.server_url``，因此用户修改
服务器地址后立即生效，客户端不存在任何硬编码的 ECS 地址。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .compat import CLIENT_API_VERSION
from .config import AppConfig, normalize_server_url
from .paths import VERSION

DEFAULT_TIMEOUT = 15


class ApiError(Exception):
    """API 请求失败（网络错误 / 非 2xx / 服务端错误）。"""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ApiClient:
    def __init__(self, config: AppConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": f"NexHub-Client/{VERSION}",
                "X-NexHub-Client-Version": VERSION,
                "X-NexHub-Client-Api-Version": str(CLIENT_API_VERSION),
            }
        )

    # ------------------------------------------------------------------
    @property
    def base_url(self) -> str:
        return normalize_server_url(self.config.server_url)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, *, timeout: int = DEFAULT_TIMEOUT, **kwargs) -> Any:
        headers = kwargs.pop("headers", {}) or {}
        if "json" in kwargs:
            payload = kwargs.pop("json")
            kwargs["data"] = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        try:
            response = self.session.request(
                method, self._url(path), headers=headers, timeout=timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise ApiError(f"无法连接服务器，请检查地址与网络：{exc}") from exc

        if response.status_code >= 400:
            message = _extract_error(response)
            raise ApiError(message, response.status_code)

        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                return response.json()
            except ValueError as exc:
                raise ApiError("服务器返回了无法解析的数据") from exc
        return response.text

    # ------------------------------------------------------------------ 认证
    def login(self, identifier: str, password: str) -> dict:
        """identifier 可以是用户名或昵称；请求体统一使用 UTF-8 JSON。"""
        return self._request(
            "POST", "/api/auth/login", json={"username": identifier, "password": password}
        )

    def register(self, username: str, password: str, display_name: str = "") -> dict:
        return self._request(
            "POST",
            "/api/auth/register",
            json={"username": username, "password": password, "display_name": display_name},
        )

    def me(self) -> dict:
        return self._request("GET", "/api/auth/me")

    def update_profile(self, display_name: str) -> dict:
        return self._request(
            "PATCH", "/api/auth/profile", json={"display_name": display_name}
        )

    def change_password(self, old_password: str, new_password: str) -> dict:
        return self._request(
            "POST",
            "/api/auth/password",
            json={"old_password": old_password, "new_password": new_password},
        )

    # ------------------------------------------------------------------ 仪表盘
    def dashboard(self) -> dict:
        return self._request("GET", "/api/dashboard")

    # ------------------------------------------------------------------ 共享
    def list_shares(self, scope: str = "public") -> list[dict]:
        data = self._request("GET", "/api/shares", params={"scope": scope})
        return data.get("items", data) if isinstance(data, dict) else data

    def create_share(self, payload: dict) -> dict:
        return self._request("POST", "/api/shares", json=payload)

    def upload_oss_file(self, share_id: int, relative_path: str, file_path: str) -> dict:
        """经 ECS 后端代理流式上传到 OSS（客户端不接触 OSS 密钥）。"""
        with open(file_path, "rb") as handle:
            return self._request(
                "POST",
                f"/api/shares/{share_id}/files",
                params={"path": relative_path},
                data=handle,
                headers={"Content-Type": "application/octet-stream"},
                timeout=3600,
            )

    def cancel_share(self, share_id: int) -> dict:
        return self._request("POST", f"/api/shares/{share_id}/cancel")

    def delete_share(self, share_id: int) -> dict:
        return self._request("DELETE", f"/api/shares/{share_id}")

    def share_manifest(self, share_id: int) -> dict:
        return self._request("GET", f"/api/shares/{share_id}/manifest")

    def share_download_url(self, share_id: int, path: str) -> str:
        return self._url(f"/api/shares/{share_id}/download?path={quote(path)}")

    # ------------------------------------------------------------------ 在线设备
    def device_heartbeat(self, payload: dict) -> dict:
        return self._request("POST", "/api/devices/heartbeat", json=payload, timeout=8)

    def list_online_devices(self, exclude_device_id: str = "") -> list[dict]:
        params = {"exclude_device_id": exclude_device_id} if exclude_device_id else {}
        data = self._request("GET", "/api/devices", params=params, timeout=8)
        return data.get("items", data) if isinstance(data, dict) else data

    def device_offline(self, device_id: str) -> dict:
        return self._request("DELETE", f"/api/devices/{quote(device_id)}", timeout=5)

    # ------------------------------------------------------------------ NPS 隧道
    def list_nps_tunnels(self) -> list[dict]:
        data = self._request("GET", "/api/tunnels")
        return data.get("items", data) if isinstance(data, dict) else data

    def create_nps_tunnel(self, payload: dict) -> dict:
        return self._request("POST", "/api/tunnels", json=payload)

    def auto_nps_tunnel(self, payload: dict) -> dict:
        """幂等申请本机 NPS 主机与 TCP 隧道。"""
        return self._request("POST", "/api/tunnels/auto", json=payload, timeout=25)

    def update_nps_tunnel(self, tunnel_id: int, payload: dict) -> dict:
        return self._request("PATCH", f"/api/tunnels/{tunnel_id}", json=payload)

    def delete_nps_tunnel(self, tunnel_id: int) -> dict:
        return self._request("DELETE", f"/api/tunnels/{tunnel_id}")

    def set_nps_tunnel_enabled(self, tunnel_id: int, enabled: bool) -> dict:
        action = "start" if enabled else "stop"
        return self._request("POST", f"/api/tunnels/{tunnel_id}/{action}")

    # ------------------------------------------------------------------ 账号
    def download_records(self) -> list[dict]:
        data = self._request("GET", "/api/account/downloads")
        return data.get("items", data) if isinstance(data, dict) else data

    def oss_usage(self) -> dict:
        return self._request("GET", "/api/account/oss")

    def upload_avatar(self, file_path: str) -> dict:
        """上传头像到 ECS 本地存储（不经过 OSS）。"""
        path = Path(file_path)
        if not path.is_file():
            raise ApiError("找不到头像文件")
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ApiError("头像不能超过 5MB")
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(path.suffix.lower())
        if not mime:
            raise ApiError("头像仅支持 PNG / JPG / WebP")
        with path.open("rb") as handle:
            return self._request(
                "POST",
                "/api/account/avatar",
                files={"file": (path.name, handle, mime)},
                timeout=60,
            )

    def fetch_avatar(self) -> bytes:
        """读取当前用户头像；没有头像时返回空字节。"""
        headers = {"Accept": "image/png,image/jpeg,image/webp"}
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        try:
            response = self.session.get(
                self._url("/api/account/avatar"), headers=headers, timeout=15
            )
        except requests.RequestException as exc:
            raise ApiError(f"读取头像失败：{exc}") from exc
        if response.status_code == 404:
            return b""
        if response.status_code >= 400:
            raise ApiError(_extract_error(response), response.status_code)
        return response.content

    # ------------------------------------------------------------------ 其他
    def health(self) -> dict:
        return self._request("GET", "/api/health")

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def test_connection(url: str, timeout: int = 6) -> dict:
        """设置页“测试连接”使用，不依赖已保存的服务器地址。"""
        target = normalize_server_url(url)
        if not target:
            raise ApiError("请填写服务器地址")
        try:
            response = requests.get(
                f"{target}/api/health",
                timeout=timeout,
                headers={
                    "User-Agent": f"NexHub-Client/{VERSION}",
                    "X-NexHub-Client-Version": VERSION,
                    "X-NexHub-Client-Api-Version": str(CLIENT_API_VERSION),
                },
            )
        except requests.RequestException as exc:
            raise ApiError(f"连接失败：{exc}") from exc
        if response.status_code >= 400:
            raise ApiError(f"服务器返回 HTTP {response.status_code}", response.status_code)
        try:
            return response.json()
        except ValueError:
            return {"status": "ok"}


def _extract_error(response: requests.Response) -> str:
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, list):
            return "；".join(str(item.get("msg", item)) for item in detail)
        if detail:
            return str(detail)
    except ValueError:
        pass
    mapping = {
        401: "登录状态已失效，请重新登录",
        403: "没有权限执行该操作",
        404: "请求的资源不存在",
        426: "客户端版本过低，请升级客户端后重试",
        429: "请求过于频繁，请稍后再试",
    }
    return mapping.get(response.status_code, f"服务器错误（HTTP {response.status_code}）")
