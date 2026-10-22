"""RDP 主机管理：本地加密存储与原生 mstsc 启动。"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import AppConfig
from ..paths import app_data_dir

log = logging.getLogger(__name__)
_KEY_FILE = "rdp_key.dat"
_RDP_DEFAULT_PORT = 3389


class RdpError(RuntimeError):
    """可展示给用户的 RDP 错误。"""


@dataclass
class RdpHost:
    id: str
    name: str
    address: str
    port: int = _RDP_DEFAULT_PORT
    username: str = ""
    password_enc: str = ""
    notes: str = ""
    use_nps: bool = False
    updated_at: str = ""

    @classmethod
    def from_dict(cls, raw: dict) -> "RdpHost":
        try:
            port = int(raw.get("port") or _RDP_DEFAULT_PORT)
        except (TypeError, ValueError):
            port = _RDP_DEFAULT_PORT
        return cls(
            id=str(raw.get("id") or uuid.uuid4().hex),
            name=str(raw.get("name") or "").strip(),
            address=str(raw.get("address") or "").strip(),
            port=min(65535, max(1, port)),
            username=str(raw.get("username") or "").strip(),
            password_enc=str(raw.get("password_enc") or ""),
            notes=str(raw.get("notes") or "").strip(),
            use_nps=bool(raw.get("use_nps", False)),
            updated_at=str(raw.get("updated_at") or ""),
        )


class _PasswordBox:
    """使用 DPAPI 保护 AES-256-GCM 密钥，密码本身用 AES-GCM 加密。"""

    def __init__(self) -> None:
        self._key = self._load_key()
        self._aes = AESGCM(self._key)

    def _load_key(self) -> bytes:
        key_path = app_data_dir() / _KEY_FILE
        try:
            import win32crypt
        except ImportError as exc:  # pragma: no cover
            raise RdpError("当前系统缺少安全存储组件，无法加密 RDP 密码") from exc
        if key_path.exists():
            try:
                protected = key_path.read_bytes()
                _description, key = win32crypt.CryptUnprotectData(
                    protected, None, None, None, 0
                )
                if len(key) == 32:
                    return key
            except Exception:
                log.warning("RDP 密钥损坏，重新生成", exc_info=True)
        key = secrets.token_bytes(32)
        protected = win32crypt.CryptProtectData(
            key, "NexHub RDP key", None, None, None, 0
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(protected)
        return key

    def encrypt(self, value: str) -> str:
        if not value:
            return ""
        nonce = secrets.token_bytes(12)
        ciphertext = self._aes.encrypt(nonce, value.encode("utf-8"), None)
        return "v1:" + base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, value: str) -> str:
        if not value:
            return ""
        if not value.startswith("v1:"):
            raise RdpError("RDP 密码格式无效")
        try:
            raw = base64.b64decode(value[3:])
            nonce, ciphertext = raw[:12], raw[12:]
            return self._aes.decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception as exc:
            raise RdpError("RDP 密码解密失败，请重新填写密码") from exc


def _normalize_target(raw: str, port: int) -> str:
    value = (raw or "").strip()
    if not value:
        raise RdpError("请填写 RDP 目标地址")
    if "://" in value:
        value = urlsplit(value).netloc or urlsplit(value).path
    value = value.split("/", 1)[0].strip()
    if value.startswith("["):  # IPv6 [::1]:3389
        closing = value.find("]")
        if closing < 0:
            raise RdpError("IPv6 地址格式不正确")
        if value[closing + 1 :].startswith(":"):
            return value
        return f"{value}:{port}"
    if ":" not in value:
        return f"{value}:{port}"
    return value


class RdpService:
    def __init__(self, config: AppConfig):
        self.config = config
        self._box: _PasswordBox | None = None

    # ------------------------------------------------------------------
    def list_hosts(self) -> list[RdpHost]:
        hosts = [RdpHost.from_dict(item) for item in self.config.rdp_hosts]
        return sorted(hosts, key=lambda item: item.name.lower())

    def find(self, host_id: str) -> RdpHost | None:
        return next((item for item in self.list_hosts() if item.id == host_id), None)

    def nps_tunnel_target(self) -> str:
        """从 NPC 配置读取 RDP 隧道映射地址。"""
        raw = (self.config.nps.host_domain or "").strip()
        if not raw:
            return ""
        try:
            return _normalize_target(raw, _RDP_DEFAULT_PORT)
        except RdpError:
            return ""

    def target_for(self, host: RdpHost) -> str:
        if host.use_nps:
            target = self.nps_tunnel_target()
            if not target:
                raise RdpError(
                    "RDP 主机已启用 NPS 隧道，但未配置隧道地址。"
                    "请先到「设置 → NPC 管理」填写 RDP/NPS 隧道地址。"
                )
            return target
        return _normalize_target(host.address, host.port)

    # ------------------------------------------------------------------
    def save_host(
        self,
        *,
        host_id: str | None,
        name: str,
        address: str,
        port: int,
        username: str,
        password: str,
        notes: str,
        use_nps: bool,
    ) -> RdpHost:
        name = name.strip()
        address = address.strip()
        if not name:
            raise RdpError("请填写主机名称")
        if not address:
            raise RdpError("请填写地址（IP 或域名）")
        if not 1 <= int(port) <= 65535:
            raise RdpError("端口必须在 1-65535 之间")
        if use_nps and not self.nps_tunnel_target():
            raise RdpError(
                "启用 NPS 隧道前，请先在「设置 → NPC 管理」配置 RDP/NPS 隧道地址"
            )
        existing = self.find(host_id) if host_id else None
        encrypted = existing.password_enc if existing else ""
        if password:
            encrypted = self._password_box().encrypt(password)
        host = RdpHost(
            id=existing.id if existing else uuid.uuid4().hex,
            name=name,
            address=address,
            port=int(port),
            username=username.strip(),
            password_enc=encrypted,
            notes=notes.strip(),
            use_nps=bool(use_nps),
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        rows = [item for item in self.config.rdp_hosts if str(item.get("id")) != host.id]
        rows.append(asdict(host))
        self.config.rdp_hosts = rows
        self.config.save()
        log.info("RDP 主机已保存：%s", host.name)
        return host

    def delete_host(self, host_id: str) -> None:
        before = len(self.config.rdp_hosts)
        self.config.rdp_hosts = [
            item for item in self.config.rdp_hosts if str(item.get("id")) != host_id
        ]
        if len(self.config.rdp_hosts) == before:
            raise RdpError("RDP 主机不存在")
        self.config.save()
        log.info("RDP 主机已删除：%s", host_id)

    # ------------------------------------------------------------------
    def export_hosts(self, path: str | Path) -> int:
        """导出静态主机元数据；出于安全考虑不导出密码。"""
        rows = []
        for host in self.list_hosts():
            item = asdict(host)
            item["password_enc"] = ""
            rows.append(item)
        Path(path).write_text(
            json.dumps({"version": 1, "hosts": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(rows)

    def import_hosts(self, path: str | Path) -> int:
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RdpError(f"导入文件读取失败：{exc}") from exc
        rows = raw.get("hosts") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            raise RdpError("导入文件格式不正确")
        existing = {str(item.get("id")) for item in self.config.rdp_hosts}
        imported = 0
        for item in rows:
            if not isinstance(item, dict):
                continue
            host = RdpHost.from_dict({**item, "id": uuid.uuid4().hex, "password_enc": ""})
            if not host.name or not host.address:
                continue
            data = asdict(host)
            if data["id"] in existing:
                continue
            self.config.rdp_hosts.append(data)
            existing.add(data["id"])
            imported += 1
        if imported:
            self.config.save()
        log.info("导入 RDP 静态主机 %s 条", imported)
        return imported

    # ------------------------------------------------------------------
    def connect(self, host: RdpHost) -> Path:
        return self._launch(host.name, self.target_for(host), host.username)

    def connect_device(self, device: dict) -> Path:
        target = str(device.get("tunnel_address") or "").strip()
        if not target:
            raise RdpError(
                "在线设备未上报 NPS 隧道地址。请在该设备「设置 → NPC 管理」"
                "填写 RDP/NPS 隧道地址后重试。"
            )
        return self._launch(
            str(device.get("name") or "在线设备"),
            _normalize_target(target, _RDP_DEFAULT_PORT),
            "",
        )

    def _launch(self, name: str, target: str, username: str) -> Path:
        mstsc = self.find_mstsc()
        if not mstsc:
            raise RdpError("当前系统不支持 RDP：未找到 mstsc.exe")
        rdp_file = self._write_rdp_file(name, target, username)
        log.info("启动 RDP：%s -> %s（临时文件=%s）", name, target, rdp_file)
        try:
            process = subprocess.Popen(
                [str(mstsc), str(rdp_file)],
                close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            rdp_file.unlink(missing_ok=True)
            raise RdpError(f"启动 mstsc.exe 失败：{exc}") from exc
        threading.Thread(
            target=self._cleanup_rdp_file,
            args=(rdp_file, process),
            name="nexhub-rdp-cleanup",
            daemon=True,
        ).start()
        return rdp_file

    @staticmethod
    def find_mstsc() -> Path | None:
        if os.name != "nt":
            return None
        candidates = [
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "mstsc.exe",
            Path(shutil.which("mstsc.exe") or ""),
        ]
        return next((path for path in candidates if path.is_file()), None)

    @staticmethod
    def _write_rdp_file(name: str, target: str, username: str = "") -> Path:
        fd, filename = tempfile.mkstemp(prefix="nexhub-rdp-", suffix=".rdp")
        os.close(fd)
        path = Path(filename)
        lines = [
            "full address:s:" + target,
            "prompt for credentials:i:1",
            "authentication level:i:2",
            "screen mode id:i:2",
            "use multimon:i:0",
            "redirectclipboard:i:1",
        ]
        if username:
            lines.append("username:s:" + username)
        # 文本模式在 Windows 会把 \n 转成 \r\n；不要预先写 \r\n，否则会变成空行。
        path.write_text("\n".join(lines) + "\n", encoding="utf-16")
        return path

    @staticmethod
    def _cleanup_rdp_file(path: Path, process: subprocess.Popen) -> None:
        try:
            process.wait()
        finally:
            try:
                path.unlink(missing_ok=True)
                log.info("已清理临时 RDP 文件：%s", path)
            except OSError:
                log.warning("清理临时 RDP 文件失败：%s", path, exc_info=True)

    def _password_box(self) -> _PasswordBox:
        if self._box is None:
            self._box = _PasswordBox()
        return self._box

