"""一键自动配置 NPC 的业务层冒烟测试（不触发 UAC、不修改真实 npc.conf）。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
DATA = PROJECT_ROOT / ".tmp" / "auto_npc_smoke"
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True)
os.environ["NEXHUB_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(CLIENT_ROOT))

from nexhub.config import AppConfig  # noqa: E402
from nexhub.services.tunnel_service import TunnelService  # noqa: E402


class FakeApi:
    def __init__(self):
        self.calls: list[dict] = []

    def auto_nps_tunnel(self, payload: dict) -> dict:
        self.calls.append(payload)
        return {
            "created": len(self.calls) == 1,
            "tunnel": {
                "id": 7,
                "name": payload["name"],
                "vkey": "test-vkey",
                "server_addr": "47.108.130.177",
                "server_port": 8024,
                "public_address": "47.108.130.177:30000",
                "enabled": True,
            },
        }


class FakeNpc:
    def __init__(self):
        self.install_calls = 0
        self.apply_calls = 0
        self.config = {"server_addr": "", "vkey": ""}
        self.running = False
        self.service_installed = True

    def status(self) -> dict:
        return {
            "installed": True,
            "service_installed": self.service_installed,
            "running": self.running,
            "config_exists": True,
            "config_path": "npc.conf",
        }

    def install(self) -> dict:
        self.install_calls += 1
        self.service_installed = True
        return self.status()

    def read_config(self) -> dict:
        return dict(self.config)

    def apply_config(self, server_addr: str, vkey: str) -> dict:
        self.apply_calls += 1
        self.config = {"server_addr": server_addr, "vkey": vkey}
        self.running = True
        return dict(self.config)


def main() -> int:
    config = AppConfig()
    config.demo_mode = False
    config.device_name = "TestPC"
    config.save()
    api = FakeApi()
    npc = FakeNpc()
    service = TunnelService(config, api, None, npc)

    first = service.auto_configure_local()
    assert first["created"] is True
    assert api.calls[0]["name"] == "RDP-TestPC"
    assert npc.apply_calls == 1
    assert npc.config == {
        "server_addr": "47.108.130.177:8024",
        "vkey": "test-vkey",
    }
    assert config.nps.host_domain == "47.108.130.177:30000"

    second = service.auto_configure_local()
    assert second["created"] is False
    assert npc.apply_calls == 1, "already configured NPC should not restart"

    third = service.auto_configure_local(force=True)
    assert third["created"] is False
    assert npc.apply_calls == 2, "force should reapply npc.conf"

    npc.service_installed = False
    npc.running = False
    service.auto_configure_local()
    assert npc.install_calls == 1 and npc.apply_calls == 3
    print("PASS: NPC auto configure service behavior")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
