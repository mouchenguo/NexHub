"""NPC 安装/配置读写冒烟测试（不触碰真实 Program Files 和系统服务）。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
TMP = PROJECT_ROOT / ".tmp" / "npc_smoke"
TARGET = TMP / "target"
if TMP.exists():
    shutil.rmtree(TMP)
TARGET.mkdir(parents=True)
os.environ["NEXHUB_NPC_DIR"] = str(TARGET)
os.environ["NEXHUB_NPC_BUNDLE"] = str(CLIENT_ROOT / "nexhub" / "resources" / "npc")
sys.path.insert(0, str(CLIENT_ROOT))

from nexhub.services.npc_service import NpcManager, read_npc_config  # noqa: E402


def main() -> int:
    manager = NpcManager()
    status = manager.install(repair=False)
    assert status["installed"], "npc.exe was not installed"
    assert status["config_exists"], "npc.conf was not installed"

    initial = read_npc_config()
    assert initial == {"server_addr": "", "vkey": ""}, initial

    manager.save_config("1.2.3.4:8024", "unit-test-vkey")
    saved = read_npc_config()
    assert saved["server_addr"] == "1.2.3.4:8024", saved
    assert saved["vkey"] == "unit-test-vkey", saved

    config_path = TARGET / "conf" / "npc.conf"
    text = config_path.read_text(encoding="utf-8")
    assert "[common]" in text
    assert "unit-test-vkey" in text
    assert text.count("server_addr=1.2.3.4:8024") == 1

    # 修复安装不能覆盖已有 vkey/配置。
    config_path.write_text(text + "\nkeep_me=1\n", encoding="utf-8")
    manager.install(repair=True)
    repaired = read_npc_config()
    assert repaired["vkey"] == "unit-test-vkey", repaired
    assert "keep_me=1" in config_path.read_text(encoding="utf-8")

    print("NPC INSTALL/CONFIG OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
