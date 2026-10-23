"""客户端兼容层冒烟测试。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent
DATA = PROJECT_ROOT / ".tmp" / "compat_smoke"
if DATA.exists():
    shutil.rmtree(DATA)
DATA.mkdir(parents=True)
os.environ["NEXHUB_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(CLIENT_ROOT))

from nexhub.api_client import ApiClient, ApiError  # noqa: E402
from nexhub.compat import (  # noqa: E402
    FEATURE_AUTO_TUNNEL,
    ServerCompatibility,
)
from nexhub.config import AppConfig  # noqa: E402
from nexhub.services.tunnel_service import TunnelService  # noqa: E402


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print("PASS:", message)


current = ServerCompatibility.from_payload(
    {
        "version": "0.1.0-beta",
        "api_version": 1,
        "min_client_version": "0.1.0-beta",
        "features": ["auto_tunnel", "registration"],
    }
)
check(
    current.checked
    and not current.legacy
    and current.supports(FEATURE_AUTO_TUNNEL)
    and not current.message,
    "当前服务端兼容信息识别",
)

legacy = ServerCompatibility.from_payload(
    {"status": "ok", "version": "0.0.9", "service": "nexhub-server"}
)
check(
    legacy.checked
    and legacy.legacy
    and legacy.supports(FEATURE_AUTO_TUNNEL)
    and "旧版" in legacy.warning,
    "旧服务端降级为 legacy 兼容模式并保留尝试入口",
)

missing_feature = ServerCompatibility.from_payload(
    {
        "version": "0.1.0-beta",
        "api_version": 1,
        "min_client_version": "0.1.0-beta",
        "features": ["registration"],
    }
)
check(
    missing_feature.checked and not missing_feature.supports(FEATURE_AUTO_TUNNEL),
    "新服务端明确缺少能力时禁用新功能",
)

future = ServerCompatibility.from_payload(
    {
        "version": "0.2.0-beta",
        "api_version": 2,
        "min_client_version": "0.1.0-beta",
        "features": ["auto_tunnel", "new_feature"],
    }
)
check(
    future.server_newer and future.supports(FEATURE_AUTO_TUNNEL) and future.warning,
    "服务端协议较新时保留能力并提示升级客户端",
)

too_old = ServerCompatibility.from_payload(
    {
        "version": "0.2.0-beta",
        "api_version": 1,
        "min_client_version": "9.9.9",
        "features": ["auto_tunnel"],
    }
)
check(too_old.client_too_old and bool(too_old.error), "客户端低于最低版本时给出错误")

unknown = ServerCompatibility.from_payload({})
check(not unknown.checked and unknown.supports(FEATURE_AUTO_TUNNEL), "未检查时保持乐观兼容")

client = ApiClient(AppConfig())
check(
    client.session.headers.get("X-NexHub-Client-Version") == "0.1.1"
    and client.session.headers.get("X-NexHub-Client-Api-Version") == "1",
    "客户端请求携带版本头",
)
client.close()


class MissingAutoApi:
    def auto_nps_tunnel(self, _payload: dict) -> dict:
        raise ApiError("请求的资源不存在", 404)


config = AppConfig()
config.demo_mode = False
config.device_name = "compat-test"
service = TunnelService(config, MissingAutoApi(), demo=None)
try:
    service.auto_configure_local()
except RuntimeError as exc:
    check("服务端版本过低" in str(exc), "旧服务端 404 转为明确的兼容提示")
else:
    raise AssertionError("旧服务端 404 未转换为兼容提示")

print("\nALL COMPATIBILITY CHECKS PASSED")
