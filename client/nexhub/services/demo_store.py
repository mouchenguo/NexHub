"""本地演示数据（离线体验模式）。

服务器不可达或在登录页选择“离线体验”时，客户端使用该模块的数据。
演示文件会写入 ``%APPDATA%\\NexHub\\demo``，因此浏览、下载等交互仍然可用，
后续接入 ECS 后可无缝切换到真实 API。
"""
from __future__ import annotations

import json
import random
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import AppConfig
from ..file_utils import GIB
from ..paths import demo_dir, demo_store_path

DEMO_QUOTA = 50 * GIB


def _iso(delta_minutes: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)).isoformat()


def _write_sized_file(path: Path, size: int) -> None:
    """写入指定大小的演示文件，避免一次性构造大字符串。"""
    block = ("NexHub demo file. 这是一个用于演示浏览与下载流程的占位文件。\n" * 64).encode("utf-8")
    remaining = max(0, size)
    with path.open("wb") as handle:
        while remaining > 0:
            chunk = block[:remaining]
            handle.write(chunk)
            remaining -= len(chunk)
        if size == 0:
            handle.write(b"")


class DemoStore:
    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.RLock()
        self._path = demo_store_path()
        self._data = self._load()
        self._ensure_files()

    # ------------------------------------------------------------------
    def _load(self) -> dict:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if data.get("version") == 2:
                    return data
                # 旧版演示数据（示例共享、文件、下载记录、隧道）一次性清空。
                self._clear_demo_assets()
                data = self._empty_data()
                self._write(data)
                return data
            except (OSError, json.JSONDecodeError):
                pass
        self._clear_demo_assets()
        data = self._empty_data()
        self._write(data)
        return data

    @staticmethod
    def _empty_data() -> dict:
        return {
            "version": 2,
            "shares": [],
            "tunnels": [],
            "downloads": [],
            "next_share_id": 1,
            "next_tunnel_id": 1,
        }

    @staticmethod
    def _clear_demo_assets() -> None:
        """删除旧版本生成在 %APPDATA%/NexHub/demo 下的示例文件。"""
        base = demo_dir()
        if not base.exists():
            return
        for child in base.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError:
                pass

    def _write(self, data: dict | None = None) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(data or self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def _ensure_files(self) -> None:
        """确保演示文件仍存在于磁盘（用户清理缓存后可自动恢复）。"""
        for share in self._data.get("shares", []):
            root = Path(share.get("local_root", ""))
            for item in share.get("manifest", []):
                target = root / item["path"]
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    _write_sized_file(target, int(item.get("size") or 0))

    def _seed(self) -> dict:
        base = demo_dir()
        specs = [
            {
                "id": 1,
                "name": "项目交付资料",
                "type": "temp",
                "owner": "林屿",
                "owner_id": 2,
                "desc": "Q3 项目结项资料，包含说明文档与演示数据。",
                "downloads": 26,
                "created": -1660,
                "files": {
                    "README.md": 1200,
                    "docs/接口说明.md": 76000,
                    "交付清单.txt": 8300,
                    "assets/演示数据.json": 5_400_000,
                    "assets/版本记录.csv": 480_000,
                },
            },
            {
                "id": 2,
                "name": "NexHub 快速上手指南",
                "type": "oss",
                "owner": "Yuki",
                "owner_id": 3,
                "desc": "客户端安装、共享与远程主机配置说明。",
                "downloads": 87,
                "created": -7300,
                "files": {"NexHub-快速上手指南.md": 320_000},
            },
            {
                "id": 3,
                "name": "团队设计规范 v2",
                "type": "oss",
                "owner": "",
                "owner_id": 1,
                "desc": "统一视觉规范与组件命名约定。",
                "downloads": 14,
                "created": -2600,
                "files": {
                    "设计规范/色彩系统.md": 21000,
                    "设计规范/组件命名.md": 38000,
                    "设计规范/图标清单.csv": 96000,
                    "素材/渐变示例.svg": 5400,
                },
            },
            {
                "id": 4,
                "name": "服务器巡检日志",
                "type": "temp",
                "owner": "",
                "owner_id": 1,
                "desc": "每周主机巡检记录（演示数据）。",
                "downloads": 6,
                "created": -420,
                "files": {"巡检日志_2026-W41.csv": 1_100_000},
            },
            {
                "id": 5,
                "name": "公共素材 · 品牌图标",
                "type": "oss",
                "owner": "陈默",
                "owner_id": 4,
                "desc": "常用品牌图标与占位素材。",
                "downloads": 42,
                "created": -12000,
                "files": {
                    "品牌图标/logo-mark.svg": 4300,
                    "品牌图标/color-guide.md": 18000,
                    "占位素材/使用说明.txt": 6400,
                },
            },
        ]
        shares = self._build_seed_shares(base, specs)
        tunnels = self._build_seed_tunnels()
        downloads = self._build_seed_downloads()
        return {
            "version": 1,
            "shares": shares,
            "tunnels": tunnels,
            "downloads": downloads,
            "next_share_id": 6,
            "next_tunnel_id": max([item["id"] for item in tunnels], default=0) + 1,
        }

    # ------------------------------------------------------------------ 种子
    def _build_seed_shares(self, base: Path, specs: list[dict]) -> list[dict]:
        owner = self.config.display_name or self.config.username or "我"
        shares = []
        for spec in specs:
            root = base / "shares" / f"share_{spec['id']}"
            manifest = []
            for relative, size in spec["files"].items():
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists() or target.stat().st_size != size:
                    _write_sized_file(target, size)
                manifest.append({"path": relative, "size": size})
            shares.append(
                {
                    "id": spec["id"],
                    "token": f"demo-{spec['id']}-{random.randint(1000, 9999)}",
                    "name": spec["name"],
                    "description": spec["desc"],
                    "storage_type": spec["type"],
                    "owner_id": spec["owner_id"],
                    "owner": spec["owner"] or owner,
                    "total_size": sum(item["size"] for item in manifest),
                    "file_count": len(manifest),
                    "downloads": spec["downloads"],
                    "status": "active",
                    "created_at": _iso(spec["created"]),
                    "expires_at": None,
                    "manifest": manifest,
                    "local_root": str(root),
                    "public_url": "",
                }
            )
        return shares

    def _build_seed_tunnels(self) -> list[dict]:
        return [
            {
                "id": 1,
                "name": "本地开发服务",
                "local_host": "127.0.0.1",
                "local_port": 8080,
                "nps_client_id": 101,
                "vkey": "demo-tunnel-7f3a9c21",
                "server_addr": "nps.example.com",
                "server_port": 8024,
                "enabled": True,
                "created_at": _iso(-1200),
                "updated_at": _iso(-20),
            },
            {
                "id": 2,
                "name": "家庭 NAS",
                "local_host": "192.168.1.20",
                "local_port": 5000,
                "nps_client_id": 102,
                "vkey": "demo-tunnel-1b8e4d02",
                "server_addr": "nps.example.com",
                "server_port": 8024,
                "enabled": False,
                "created_at": _iso(-4300),
                "updated_at": _iso(-800),
            },
        ]

    def _build_seed_downloads(self) -> list[dict]:
        user = self.config.username or "demo"
        return [
            {
                "id": 1,
                "file_name": "NexHub-快速上手指南.md",
                "share_name": "NexHub 快速上手指南",
                "size": 320_000,
                "created_at": _iso(-120),
                "status": "success",
                "user": user,
            },
            {
                "id": 2,
                "file_name": "assets/演示数据.json",
                "share_name": "项目交付资料",
                "size": 5_400_000,
                "created_at": _iso(-300),
                "status": "success",
                "user": user,
            },
            {
                "id": 3,
                "file_name": "设计规范/组件命名.md",
                "share_name": "团队设计规范 v2",
                "size": 38_000,
                "created_at": _iso(-1500),
                "status": "success",
                "user": user,
            },
        ]

    # ------------------------------------------------------------------ 用户
    def current_user(self) -> dict:
        shares = self._data["shares"]
        oss_used = sum(
            s["total_size"] for s in shares if s["owner_id"] == 1 and s["storage_type"] == "oss"
        )
        return {
            "id": 1,
            "username": self.config.username or "demo",
            "display_name": self.config.display_name or self.config.username or "本地体验用户",
            "role": self.config.role if self.config.role == "admin" else "user",
            "enabled": True,
            "oss_used": oss_used,
            "oss_quota": DEMO_QUOTA,
            "created_at": _iso(-20000),
            "demo_mode": True,
        }

    # ------------------------------------------------------------------ 共享
    def list_shares(self, scope: str = "public") -> list[dict]:
        with self._lock:
            items = [dict(item) for item in self._data["shares"]]
        if scope == "mine":
            items = [item for item in items if item["owner_id"] == 1]
        else:
            items = [item for item in items if item["status"] == "active"]
        for item in items:
            item["is_mine"] = item["owner_id"] == 1
            item.pop("local_root", None)
            item.pop("manifest", None)
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return items

    def get_share(self, share_id: int) -> dict | None:
        with self._lock:
            for share in self._data["shares"]:
                if share["id"] == share_id:
                    return dict(share)
        return None

    def manifest_for(self, share_id: int) -> dict:
        share = self.get_share(share_id)
        if not share:
            raise ValueError("共享不存在或已被删除")
        return {
            "share_id": share_id,
            "name": share["name"],
            "storage_type": share["storage_type"],
            "token": share["token"],
            "files": share["manifest"],
            "total_size": share["total_size"],
            "file_count": share["file_count"],
        }

    def create_share(
        self,
        *,
        name: str,
        storage_type: str,
        manifest: list[dict],
        local_root: str,
        description: str = "",
    ) -> dict:
        with self._lock:
            share_id = int(self._data.get("next_share_id", 1))
            self._data["next_share_id"] = share_id + 1
            owner_name = self.config.display_name or self.config.username or "我"
            share = {
                "id": share_id,
                "token": f"demo-{share_id}-{random.randint(1000, 9999)}",
                "name": name,
                "description": description,
                "storage_type": storage_type,
                "owner_id": 1,
                "owner": owner_name,
                "total_size": sum(int(item.get("size") or 0) for item in manifest),
                "file_count": len(manifest),
                "downloads": 0,
                "status": "active",
                "created_at": _iso(),
                "expires_at": None,
                "manifest": manifest,
                "local_root": local_root,
                "public_url": "",
            }
            self._data["shares"].append(share)
            self._write()
        result = dict(share)
        result["is_mine"] = True
        result.pop("local_root", None)
        result.pop("manifest", None)
        return result

    def cancel_share(self, share_id: int) -> dict:
        with self._lock:
            for share in self._data["shares"]:
                if share["id"] == share_id:
                    share["status"] = "cancelled"
                    self._write()
                    return {"ok": True, "share_id": share_id}
        raise ValueError("共享不存在")

    def delete_share(self, share_id: int) -> dict:
        with self._lock:
            before = len(self._data["shares"])
            self._data["shares"] = [s for s in self._data["shares"] if s["id"] != share_id]
            if len(self._data["shares"]) == before:
                raise ValueError("共享不存在")
            self._write()
        return {"ok": True}

    def local_share_file(self, share_id: int, relative: str) -> str:
        share = self.get_share(share_id)
        if not share:
            raise ValueError("共享不存在")
        root = Path(share["local_root"])
        allowed = {item["path"] for item in share["manifest"]}
        if relative not in allowed:
            raise ValueError("文件不在共享范围内")
        return str(root / relative)

    # ------------------------------------------------------------------ NPS 隧道
    def list_nps_tunnels(self) -> list[dict]:
        with self._lock:
            items = [dict(item) for item in self._data.get("tunnels", [])]
        items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return items

    def create_nps_tunnel(self, *, name: str, local_host: str, local_port: int) -> dict:
        with self._lock:
            tunnel_id = int(self._data.get("next_tunnel_id", 1))
            self._data["next_tunnel_id"] = tunnel_id + 1
            now = _iso()
            tunnel = {
                "id": tunnel_id,
                "name": name,
                "local_host": local_host,
                "local_port": int(local_port),
                "nps_client_id": 1000 + tunnel_id,
                "vkey": f"demo-tunnel-{random.randint(10000000, 99999999):08x}",
                "server_addr": "nps.example.com",
                "server_port": 8024,
                "enabled": True,
                "created_at": now,
                "updated_at": now,
            }
            self._data.setdefault("tunnels", []).append(tunnel)
            self._write()
        return dict(tunnel)

    def update_nps_tunnel(
        self, tunnel_id: int, *, name: str, local_host: str, local_port: int
    ) -> dict:
        with self._lock:
            tunnel = next(
                (item for item in self._data.get("tunnels", []) if item["id"] == tunnel_id),
                None,
            )
            if tunnel is None:
                raise ValueError("隧道不存在")
            tunnel.update(
                name=name,
                local_host=local_host,
                local_port=int(local_port),
                updated_at=_iso(),
            )
            self._write()
            return dict(tunnel)

    def set_nps_tunnel_enabled(self, tunnel_id: int, enabled: bool) -> dict:
        with self._lock:
            tunnel = next(
                (item for item in self._data.get("tunnels", []) if item["id"] == tunnel_id),
                None,
            )
            if tunnel is None:
                raise ValueError("隧道不存在")
            tunnel["enabled"] = bool(enabled)
            tunnel["updated_at"] = _iso()
            self._write()
            return dict(tunnel)

    def delete_nps_tunnel(self, tunnel_id: int) -> dict:
        with self._lock:
            tunnels = self._data.get("tunnels", [])
            before = len(tunnels)
            self._data["tunnels"] = [item for item in tunnels if item["id"] != tunnel_id]
            if len(self._data["tunnels"]) == before:
                raise ValueError("隧道不存在")
            self._write()
        return {"ok": True}

    # ------------------------------------------------------------------ 统计
    def dashboard(self) -> dict:
        with self._lock:
            shares = [dict(item) for item in self._data["shares"]]
            downloads = [dict(item) for item in self._data["downloads"]]
        active = [s for s in shares if s["status"] == "active"]
        oss_used = sum(s["total_size"] for s in active if s["storage_type"] == "oss")
        recent = []
        for record in downloads[-3:][::-1]:
            recent.append(
                {
                    "title": f"下载了 {record['file_name']}",
                    "detail": f"来自共享「{record['share_name']}」",
                    "time": record["created_at"],
                    "tone": "accent",
                }
            )
        for share in active[:2]:
            recent.append(
                {
                    "title": f"共享「{share['name']}」正在分发",
                    "detail": "存储方式："
                    + ("OSS 长期缓存" if share["storage_type"] == "oss" else "临时穿透"),
                    "time": share["created_at"],
                    "tone": "success",
                }
            )
        return {
            "active_shares": len(active),
            "oss_used": oss_used,
            "oss_quota": DEMO_QUOTA,
            "total_downloads": sum(s["downloads"] for s in shares) + len(downloads),
            "total_shared_bytes": sum(s["total_size"] for s in active),
            "recent_activity": recent[:5],
            "server": {
                "mode": "demo",
                "version": "0.1.0",
                "status": "offline-demo",
                "uptime": "本地演示数据",
            },
        }

    def download_records(self) -> list[dict]:
        with self._lock:
            return [dict(item) for item in reversed(self._data["downloads"])]

    def record_download(self, file_name: str, share_name: str, size: int) -> None:
        with self._lock:
            record_id = max((item["id"] for item in self._data["downloads"]), default=0) + 1
            self._data["downloads"].append(
                {
                    "id": record_id,
                    "file_name": file_name,
                    "share_name": share_name,
                    "size": size,
                    "created_at": _iso(),
                    "status": "success",
                    "user": self.config.username or "demo",
                }
            )
            for share in self._data["shares"]:
                if share["name"] == share_name:
                    share["downloads"] += 1
                    break
            self._write()
