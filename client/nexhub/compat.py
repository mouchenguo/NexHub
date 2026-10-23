"""客户端与服务端协议兼容性判断。"""
from __future__ import annotations

from dataclasses import dataclass

from .paths import VERSION
from .services.update_service import version_key

CLIENT_API_VERSION = 1
MIN_SERVER_API_VERSION = 1
FEATURE_AUTO_TUNNEL = "auto_tunnel"


@dataclass(frozen=True)
class ServerCompatibility:
    """服务端通过 health / 登录响应声明的兼容信息。"""

    server_version: str = ""
    api_version: int = 0
    min_client_version: str = ""
    features: tuple[str, ...] = ()
    checked: bool = False
    legacy: bool = False
    client_too_old: bool = False
    server_newer: bool = False
    warning: str = ""
    error: str = ""

    @classmethod
    def from_payload(cls, payload: dict | None) -> "ServerCompatibility":
        if not isinstance(payload, dict) or not payload:
            return cls()
        server_version = str(
            payload.get("version") or payload.get("server_version") or ""
        ).strip().lstrip("v")
        api_version = _as_int(payload.get("api_version"), 0)
        min_client = str(payload.get("min_client_version") or "").strip().lstrip("v")
        raw_features = payload.get("features") or []
        if isinstance(raw_features, str):
            raw_features = [item.strip() for item in raw_features.split(",")]
        features = tuple(
            dict.fromkeys(str(item).strip() for item in raw_features if str(item).strip())
        )
        checked = bool(server_version or api_version or features)
        legacy = checked and api_version < MIN_SERVER_API_VERSION
        client_too_old = bool(
            min_client and version_key(VERSION) < version_key(min_client)
        )
        server_newer = api_version > CLIENT_API_VERSION
        warning = ""
        error = ""
        if client_too_old:
            error = (
                f"当前客户端 v{VERSION} 低于服务端最低要求 v{min_client}，"
                "请升级客户端后再试"
            )
        elif legacy:
            warning = (
                "服务端未声明 API 版本（可能是旧版服务端），"
                "仅启用核心兼容功能；一键自动配置等新功能不可用。"
            )
        elif server_newer:
            warning = (
                f"服务端 API v{api_version} 高于当前客户端支持的 v{CLIENT_API_VERSION}，"
                "部分新功能可能不可用，建议升级客户端。"
            )
        return cls(
            server_version=server_version,
            api_version=api_version,
            min_client_version=min_client,
            features=features,
            checked=checked,
            legacy=legacy,
            client_too_old=client_too_old,
            server_newer=server_newer,
            warning=warning,
            error=error,
        )

    def supports(self, feature: str) -> bool:
        """未检查或旧版未声明能力时保持乐观；明确的 API v1+ 能力列表才限制。"""
        if not self.checked or self.legacy:
            return True
        return feature in self.features

    @property
    def summary(self) -> str:
        if not self.checked:
            return "兼容性：未获取服务端信息"
        version = f"v{self.server_version}" if self.server_version else "v未知"
        api = f"API v{self.api_version}" if self.api_version else "旧版协议"
        text = f"兼容性：服务端 {version} · {api}"
        if self.legacy:
            text += " · 旧版兼容模式"
        elif self.server_newer:
            text += " · 服务端较新"
        return text

    @property
    def message(self) -> str:
        return self.error or self.warning


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
