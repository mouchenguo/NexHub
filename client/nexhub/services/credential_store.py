"""登录凭证本地加密存储（Windows DPAPI）。"""
from __future__ import annotations

import base64
import json
import logging

log = logging.getLogger(__name__)


class CredentialStore:
    """只保存 DPAPI 保护的用户名/密码，不保存明文 token。"""

    _DESCRIPTION = "NexHub login credential"

    @staticmethod
    def save(username: str, password: str) -> str:
        try:
            import win32crypt
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("当前系统缺少 Windows 凭证保护组件") from exc
        data = json.dumps(
            {"username": username, "password": password}, ensure_ascii=False
        ).encode("utf-8")
        protected = win32crypt.CryptProtectData(
            data, CredentialStore._DESCRIPTION, None, None, None, 0
        )
        return base64.b64encode(protected).decode("ascii")

    @staticmethod
    def load(value: str) -> tuple[str, str] | None:
        if not value:
            return None
        try:
            import win32crypt

            protected = base64.b64decode(value.encode("ascii"))
            _description, data = win32crypt.CryptUnprotectData(
                protected, None, None, None, 0
            )
            payload = json.loads(data.decode("utf-8"))
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            if not username or not password:
                return None
            return username, password
        except Exception:
            log.warning("登录凭证解密失败", exc_info=True)
            return None
