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

    def ensure_local_tunnel(self, local_port: int = 3389, force: bool = False) -> dict | None:
        """安装/启动后确保当前设备在 NPS 中有主机、隧道和可用 NPC 配置。"""
        if not self.online:
            return None
        return self.auto_configure_local(local_port, force=force)

    def auto_configure_local(self, local_port: int = 3389, force: bool = False) -> dict:
        """一键自动配置：服务端幂等创建 NPS 主机/隧道，本机写入 npc.conf 并重启 NPC。"""
        if not self.online:
            raise RuntimeError("请先登录后再自动配置 NPC")
        device_name = (
            self.config.device_name or socket.gethostname() or "Windows PC"
        ).strip()
        tunnel_name = f"RDP-{device_name}"[:64]
        response = self.api.auto_nps_tunnel(
            {
                "name": tunnel_name,
                "local_host": "127.0.0.1",
                "local_port": int(local_port),
                "rdp_task": True,
            }
        )
        tunnel = response.get("tunnel") if isinstance(response, dict) else None
        if not isinstance(tunnel, dict) or not tunnel.get("vkey"):
            raise RuntimeError("服务端未返回可用的 NPS 主机配置")
        local = self.apply_tunnel(tunnel, force=force)
        result = dict(tunnel)
        result["created"] = bool(response.get("created"))
        result["local"] = local
        result["npc"] = local.get("status", {})
        return result

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
    def apply_tunnel(self, tunnel: dict, force: bool = True) -> dict[str, Any]:
        """将隧道 vkey 写入本机 npc.conf，并确保 NPC 正在运行。"""
        if not self.online:
            return {"demo": True, "message": "离线体验不会写入本机 npc.conf"}
        server_addr = str(tunnel.get("server_addr") or "").strip()
        server_port = int(tunnel.get("server_port") or 8024)
        vkey = str(tunnel.get("vkey") or "").strip()
        if not server_addr or not vkey:
            raise ValueError("隧道缺少 NPS 地址或 vkey")
        target_server = f"{server_addr}:{server_port}"

        status = self.npc.status()
        if not status.get("installed") or not status.get("service_installed"):
            # 未安装或缺少开机自启服务时，先解压内置资源/注册服务（可能触发 UAC）。
            status = self.npc.install()
        if not status.get("installed"):
            raise RuntimeError("NPC 安装失败，未找到 npc.exe")

        current = self.npc.read_config() if status.get("config_exists") else {}
        already_configured = (
            current.get("server_addr") == target_server
            and current.get("vkey") == vkey
            and bool(status.get("running"))
        )
        changed = False
        if force or not already_configured:
            # 旧 vkey 必须在写文件前停止，否则 NPS 会看到旧客户端仍在线。
            self.npc.apply_config(target_server, vkey)
            changed = True

        status = self.npc.status()
        if not status.get("running"):
            raise RuntimeError(
                "npc.conf 已写入，但 NPC 服务未运行。请检查管理员授权或 Npc 服务日志。"
            )
        if tunnel.get("public_address"):
            self.config.nps.host_domain = str(tunnel["public_address"]).strip()
        self._remember_local_config(server_addr, server_port, vkey)
        return {
            "changed": changed,
            "path": status.get("config_path", ""),
            "status": status,
            "server_addr": target_server,
            "vkey": vkey,
        }

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
