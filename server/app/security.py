"""密码哈希与 HMAC-SHA256 Token（无第三方 JWT 依赖）。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

from .config import settings

PBKDF2_ITERATIONS = 260_000
TOKEN_HEADER = {"alg": "HS256", "typ": "JWT"}


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    raw_salt = bytes.fromhex(salt) if salt else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), raw_salt, PBKDF2_ITERATIONS
    )
    return digest.hex(), raw_salt.hex()


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    try:
        digest, _ = hash_password(password, salt)
    except ValueError:
        return False
    return hmac.compare_digest(digest, password_hash)


def generate_token(length: int = 24) -> str:
    return secrets.token_urlsafe(length)


def get_secret_key() -> str:
    """优先使用管理后台轮换后的密钥，否则使用环境变量密钥。"""
    try:
        from . import db

        stored = db.get_setting("jwt_secret", "")
        return stored or settings.secret_key
    except Exception:
        return settings.secret_key


def create_token(user_id: int, role: str, ttl_hours: int | None = None) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "role": role,
        "iat": now,
        "exp": now + int((ttl_hours or settings.token_ttl_hours) * 3600),
    }
    header_part = _b64encode(json.dumps(TOKEN_HEADER, separators=(",", ":")).encode())
    payload_part = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(
        get_secret_key().encode("utf-8"), signing_input, hashlib.sha256
    ).digest()
    return f"{header_part}.{payload_part}.{_b64encode(signature)}"


def decode_token(token: str) -> dict | None:
    try:
        header_part, payload_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{payload_part}".encode()
        expected = hmac.new(
            get_secret_key().encode("utf-8"), signing_input, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(expected, _b64decode(signature_part)):
            return None
        payload = json.loads(_b64decode(payload_part))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None
