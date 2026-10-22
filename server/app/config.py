"""服务端配置。

敏感配置（OSS AccessKey、JWT 密钥、管理员初始密码）从环境变量 / ``server/.env``
读取，绝不返回给客户端或展示在管理页面。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
GIB = 1024 ** 3


def _load_env_file() -> None:
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _resolve_path(raw: str, default: Path) -> Path:
    path = Path(raw).expanduser() if raw else default
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


@dataclass
class Settings:
    app_name: str = "NexHub Server"
    version: str = "0.1.0-beta"
    base_dir: Path = BASE_DIR
    templates_dir: Path = BASE_DIR / "app" / "templates"
    static_dir: Path = BASE_DIR / "app" / "static"
    data_dir: Path = BASE_DIR / "data"
    db_path: Path = BASE_DIR / "data" / "nexhub.db"
    secret_key: str = "nexhub-dev-secret-change-me"
    token_ttl_hours: int = _env_int("NEXHUB_TOKEN_TTL_HOURS", 24 * 7)
    admin_username: str = _env("NEXHUB_ADMIN_USERNAME", "admin")
    admin_password: str = _env("NEXHUB_ADMIN_PASSWORD", "Admin@12345")
    demo_password: str = _env("NEXHUB_DEMO_PASSWORD", "demo1234")
    web_base: str = _env("NEXHUB_WEB_BASE", "").rstrip("/")
    cookie_secure: bool = _env_bool("NEXHUB_COOKIE_SECURE", False)
    max_oss_bytes: int = _env_int("NEXHUB_MAX_OSS_BYTES", 5 * GIB)
    oss_default_quota_gb: int = _env_int("NEXHUB_OSS_QUOTA_GB", 50)
    oss_endpoint: str = _env("OSS_ENDPOINT", "")
    oss_bucket: str = _env("OSS_BUCKET", "")
    oss_access_key_id: str = _env("OSS_ACCESS_KEY_ID", "")
    oss_access_key_secret: str = _env("OSS_ACCESS_KEY_SECRET", "")
    oss_prefix: str = _env("OSS_PREFIX", "nexhub/shares").strip("/")
    nps_server_addr: str = _env("NPS_SERVER_ADDR", "")
    nps_server_port: int = _env_int("NPS_SERVER_PORT", 8024)
    nps_vkey: str = _env("NPS_VKEY", "")
    nps_api_url: str = _env("NPS_API_URL", "")
    nps_auth_key: str = _env("NPS_AUTH_KEY", "")
    nps_domain: str = _env("NPS_DOMAIN", "")
    nps_tunnel_port_start: int = _env_int("NPS_TUNNEL_PORT_START", 30000)
    nps_tunnel_port_end: int = _env_int("NPS_TUNNEL_PORT_END", 30100)
    nps_auto_tunnel: bool = _env_bool("NPS_AUTO_TUNNEL", True)
    device_online_seconds: int = _env_int("NEXHUB_DEVICE_ONLINE_SECONDS", 75)
    upload_temp_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "tmp")
    local_oss_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "oss_files")
    avatar_dir: Path = field(default_factory=lambda: BASE_DIR / "data" / "avatars")

    @classmethod
    def load(cls) -> "Settings":
        data_dir = _resolve_path(_env("NEXHUB_DATA_DIR", ""), BASE_DIR / "data")
        db_path = _resolve_path(_env("NEXHUB_DB_PATH", ""), data_dir / "nexhub.db")
        instance = cls(
            data_dir=data_dir,
            db_path=db_path,
            nps_server_addr=_env("NPS_SERVER_ADDR", ""),
            nps_server_port=_env_int("NPS_SERVER_PORT", 8024),
            nps_vkey=_env("NPS_VKEY", ""),
            nps_api_url=_env("NPS_API_URL", ""),
            nps_auth_key=_env("NPS_AUTH_KEY", ""),
            nps_domain=_env("NPS_DOMAIN", ""),
        )
        instance.upload_temp_dir = _resolve_path(
            _env("NEXHUB_UPLOAD_TMP_DIR", ""), data_dir / "tmp"
        )
        instance.local_oss_dir = _resolve_path(
            _env("NEXHUB_LOCAL_OSS_DIR", ""), data_dir / "oss_files"
        )
        instance.avatar_dir = _resolve_path(
            _env("NEXHUB_AVATAR_DIR", ""), data_dir / "avatars"
        )
        for directory in (
            instance.data_dir,
            instance.upload_temp_dir,
            instance.local_oss_dir,
            instance.avatar_dir,
            instance.data_dir / "logs",
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return instance


settings = Settings.load()
