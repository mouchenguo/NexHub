"""NPS 隧道业务层：服务端自动创建 NPS 客户端，本机下发 NPC 配置。"""
from __future__ import annotations

import socket
from typing import Any

from ..api_client import ApiClient
from ..config import AppConfig
from .demo_store import DemoStore
from .npc_service import NpcManager


class TunnelService:
    def __init__(
        self,
        config: AppConfig,
        api: ApiClient,
        demo: DemoStore,
        npc: NpcManager | None = None,
    ):
        self.config = config
        self.api = api
        self.demo = demo
        self.npc = npc or NpcManager()

    @property
    def online(self) -> bool:
        return not self.config.demo_mode

    def list_tunnels(self) -> list[dict]:
        if self.online:
            return self.api.list_nps_tunnels()
        return self.demo.list_nps_tunnels()

    def create_tunnel(
        self, name: str, local_host: str, local_port: int, rdp_task: bool = False
    ) -> dict:
        name = name.strip()
        local_host = local_host.strip()
        if not name:
            raise ValueError("请填写隧道名称")
        if not local_host:
            raise ValueError("请填写本地服务 IP 或主机名")
        if self.online:
            tunnel = self.api.create_nps_tunnel(
                {
                    "name": name,
                    "local_host": local_host,
                    "local_port": int(local_port),
                    "rdp_task": bool(rdp_task),
                }
            )
            try:
                self.apply_tunnel(tunnel)
                if tunnel.get("public_address"):
                    self.config.nps.host_domain = str(tunnel["public_address"]).strip()
                    self.config.save()
            except Exception as exc:  # noqa: BLE001 - 下发给 UI 展示
                tunnel["local_error"] = str(exc)
            return tunnel
        return self.demo.create_nps_tunnel(
            name=name, local_host=local_host, local_port=int(local_port)
        )

    def ensure_local_tunnel(self, local_port: int = 3389) -> dict | None:
        """新设备登录/安装 NPC 后自动确保该设备在 NPS 中有独立主机。

        以设备名作为隧道名，已存在时不重复创建；若本机 vkey 与记录不一致，
        则重新下发该记录对应的 vkey。
        """
        if not self.online:
            return None
        device_name = (self.config.device_name or socket.gethostname() or "Windows PC").strip()
        tunnel_name = f"RDP-{device_name}"[:64]
        rows = self.list_tunnels()
        existing = next(
            (
                row
                for row in rows
                if str(row.get("name") or "") == tunnel_name
                and int(row.get("local_port") or 0) == int(local_port)
            ),
            None,
        )
        if existing:
            if self.config.nps.vkey != str(existing.get("vkey") or ""):
                self.apply_tunnel(existing)
            if existing.get("public_address"):
                self.config.nps.host_domain = str(existing["public_address"]).strip()
                self.config.save()
            return existing
        return self.create_tunnel(
            tunnel_name, "127.0.0.1", int(local_port), rdp_task=True
        )

    def update_tunnel(
        self, tunnel: dict, name: str, local_host: str, local_port: int
    ) -> dict:
        if self.online:
            return self.api.update_nps_tunnel(
                int(tunnel["id"]),
                {
                    "name": name.strip(),
                    "local_host": local_host.strip(),
                    "local_port": int(local_port),
                },
            )
        return self.demo.update_nps_tunnel(
            int(tunnel["id"]),
            name=name.strip(),
            local_host=local_host.strip(),
            local_port=int(local_port),
        )

    def set_enabled(self, tunnel: dict, enabled: bool) -> dict:
        if self.online:
            return self.api.set_nps_tunnel_enabled(int(tunnel["id"]), enabled)
        return self.demo.set_nps_tunnel_enabled(int(tunnel["id"]), enabled)

    def delete_tunnel(self, tunnel: dict) -> dict:
        if self.online:
            return self.api.delete_nps_tunnel(int(tunnel["id"]))
        return self.demo.delete_nps_tunnel(int(tunnel["id"]))

    # ------------------------------------------------------------------
    def apply_tunnel(self, tunnel: dict) -> dict[str, Any]:
        """将隧道 vkey 写入本机 npc.conf，并重启 NPC。"""
        if not self.online:
            return {"demo": True, "message": "离线体验不会写入本机 npc.conf"}
        server_addr = str(tunnel.get("server_addr") or "").strip()
        server_port = int(tunnel.get("server_port") or 8024)
        vkey = str(tunnel.get("vkey") or "").strip()
        if not server_addr or not vkey:
            raise ValueError("隧道缺少 NPS 地址或 vkey")
        if not self.npc.status().get("installed"):
            self.npc.install()
        # 旧 vkey 必须在写文件前停止，否则 NPS 会看到旧客户端仍在线。
        self.npc.apply_config(f"{server_addr}:{server_port}", vkey)
        self._remember_local_config(server_addr, server_port, vkey)
        return {"path": self.npc.status().get("config_path", "")}

    def restart_npc(self) -> dict:
        if not self.online:
            return {"demo": True}
        if not self.npc.status().get("installed"):
            raise RuntimeError("NPC 尚未安装，请先在设置中安装 NPC")
        return self.npc.restart()

    def npc_status(self) -> dict:
        return self.npc.status()

    def _remember_local_config(self, server_addr: str, server_port: int, vkey: str) -> None:
        nps = self.config.nps
        nps.server_addr = server_addr
        nps.server_port = server_port
        nps.vkey = vkey
        self.config.save()
