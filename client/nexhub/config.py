"""本地配置模块（JSON 持久化）。

注意：客户端配置中 **绝不保存阿里云 OSS AccessKey / Secret**。
OSS 的全部读写都由 ECS 后端代理，客户端只保存服务器地址与普通设置。
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .paths import config_path, default_download_dir

THEME_CHOICES = ("dark", "light", "system")


def normalize_server_url(raw: str) -> str:
    """补全协议并去掉末尾斜杠，例如 ``1.2.3.4:8000`` -> ``http://1.2.3.4:8000``。"""
    value = (raw or "").strip().rstrip("/")
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.IGNORECASE):
        value = "http://" + value
    return value


def split_server_url(raw: str) -> tuple[str, str, int]:
    """把服务器 URL 拆成 ``(http/https, IP或域名, 端口)``。"""
    value = normalize_server_url(raw) or "http://127.0.0.1:8000"
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "http", "127.0.0.1", 8000
    scheme = (parsed.scheme or "http").lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    host = parsed.hostname or "127.0.0.1"
    try:
        port = parsed.port
    except ValueError:
        port = None
    if not port:
        port = 443 if scheme == "https" else 8000
    return scheme, host, int(port)


def build_server_url(scheme: str, host: str, port: int) -> str:
    """由协议、IP/域名和端口生成统一 URL，始终保留端口。"""
    scheme = (scheme or "http").strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    value = (host or "").strip().strip("/")
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://")):
        value = value.split("://", 1)[1]
    value = value.split("/", 1)[0]
    if not value.startswith("["):
        if value.count(":") == 1:
            host_part, port_part = value.rsplit(":", 1)
            if port_part.isdigit():
                value = host_part
        elif ":" in value:
            value = f"[{value}]"
    elif "]" not in value:
        value = value.split("]", 1)[0] + "]"
    try:
        port_value = int(port)
    except (TypeError, ValueError):
        port_value = 443 if scheme == "https" else 8000
    if port_value < 1 or port_value > 65535:
        port_value = 443 if scheme == "https" else 8000
    return f"{scheme}://{value}:{port_value}"


@dataclass
class NpsConfig:
    """nps 内网穿透客户端参数（客户端本地使用）。"""

    server_addr: str = ""
    server_port: int = 8024
    vkey: str = ""
    web_domain: str = ""
    host_domain: str = ""
    local_port: int = 8765

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NpsConfig":
        data = data or {}
        return cls(
            server_addr=str(data.get("server_addr", "")),
            server_port=_as_int(data.get("server_port"), 8024),
            vkey=str(data.get("vkey", "")),
            web_domain=str(data.get("web_domain", "")),
            host_domain=str(data.get("host_domain", "")),
            local_port=_as_int(data.get("local_port"), 8765),
        )


@dataclass
class AppConfig:
    """客户端完整本地配置。"""

    theme: str = "dark"
    server_url: str = "http://127.0.0.1:8000"
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    local_api_port: int = 8765
    nps: NpsConfig = field(default_factory=NpsConfig)
    # RDP 主机；password_enc 为 AES-GCM 密文，绝不保存明文密码
    rdp_hosts: list[dict[str, Any]] = field(default_factory=list)
    # 在线设备心跳标识（同一账号多台电脑）
    device_id: str = ""
    device_name: str = ""
    # 记住我：只保存 DPAPI 加密后的用户名/密码，不保存明文 token
    remember_me: bool = False
    credential_enc: str = ""
    # 登录会话（仅内存使用，to_dict 不落盘）
    token: str = ""
    username: str = ""
    display_name: str = ""
    role: str = "user"
    demo_mode: bool = False
    # 不加载到工具箱的插件 ID
    disabled_plugins: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        target = path or config_path()
        if not target.exists():
            cfg = cls()
            cfg.save(target)
            return cfg
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AppConfig":
        theme = str(raw.get("theme", "dark"))
        return cls(
            theme=theme if theme in THEME_CHOICES else "dark",
            server_url=normalize_server_url(str(raw.get("server_url", ""))) or "http://127.0.0.1:8000",
            download_dir=str(raw.get("download_dir") or default_download_dir()),
            local_api_port=_as_int(raw.get("local_api_port"), 8765),
            nps=NpsConfig.from_dict(raw.get("nps")),
            rdp_hosts=[
                item for item in raw.get("rdp_hosts", []) if isinstance(item, dict)
            ],
            device_id=str(raw.get("device_id", "")),
            device_name=str(raw.get("device_name", "")),
            remember_me=bool(raw.get("remember_me", False)),
            credential_enc=str(raw.get("credential_enc", "")),
            token=str(raw.get("token", "")),
            username=str(raw.get("username", "")),
            display_name=str(raw.get("display_name", "")),
            role=str(raw.get("role", "user")),
            demo_mode=bool(raw.get("demo_mode", False)),
            disabled_plugins=[
                str(item)
                for item in raw.get("disabled_plugins", [])
                if isinstance(item, str)
            ],
        )

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # token 只在内存中使用；重启后通过加密凭证重新登录。
        data.pop("token", None)
        # 防御性清理：即使旧版本误存了 OSS 凭证，也永不写入磁盘
        for key in ("oss_access_key_id", "oss_access_key_secret", "oss"):
            data.pop(key, None)
        return data

    def save(self, path: Path | None = None) -> None:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    def update(self, **values: Any) -> None:
        for key, value in values.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.save()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_logged_in(self) -> bool:
        return bool(self.demo_mode or self.token)

    def enter_demo(self) -> None:
        self.token = ""
        self.username = "demo"
        self.display_name = "请登录"
        self.role = "user"
        self.demo_mode = True
        self.remember_me = False
        self.credential_enc = ""
        self.save()

    def clear_session(self) -> None:
        self.token = ""
        self.username = ""
        self.display_name = ""
        self.role = "user"
        self.demo_mode = False
        self.remember_me = False
        self.credential_enc = ""
        self.save()

    @property
    def download_path(self) -> Path:
        path = Path(self.download_dir).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
