"""SQLite 数据访问层（stdlib sqlite3，无需 ORM）。"""
from __future__ import annotations

import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import settings
from .security import hash_password

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'approved',
    created_at TEXT NOT NULL,
    last_login TEXT,
    avatar_ext TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE NOT NULL,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    storage_type TEXT NOT NULL CHECK(storage_type IN ('oss', 'temp')),
    total_size INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    local_url TEXT NOT NULL DEFAULT '',
    nps_task_id INTEGER,
    public_port INTEGER,
    manifest_json TEXT NOT NULL DEFAULT '[]',
    downloads INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_shares_owner ON shares(owner_id);
CREATE INDEX IF NOT EXISTS idx_shares_status ON shares(status);

CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    device_id TEXT NOT NULL,
    name TEXT NOT NULL,
    tunnel_address TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'online',
    last_seen TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(owner_id, device_id),
    FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_devices_owner_seen ON devices(owner_id, last_seen);

CREATE TABLE IF NOT EXISTS oss_objects (
    share_id INTEGER NOT NULL,
    object_key TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    size INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    PRIMARY KEY(share_id, relative_path),
    FOREIGN KEY(share_id) REFERENCES shares(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS download_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER,
    user_id INTEGER,
    file_path TEXT NOT NULL,
    file_size INTEGER NOT NULL DEFAULT 0,
    ip TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'success',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_download_share ON download_logs(share_id);
CREATE INDEX IF NOT EXISTS idx_download_user ON download_logs(user_id);

CREATE TABLE IF NOT EXISTS system_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL DEFAULT 'info',
    category TEXT NOT NULL DEFAULT 'system',
    message TEXT NOT NULL,
    user_id INTEGER,
    ip TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_category ON system_logs(category);
CREATE INDEX IF NOT EXISTS idx_logs_created ON system_logs(created_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nps_tunnels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    local_host TEXT NOT NULL,
    local_port INTEGER NOT NULL,
    nps_client_id INTEGER,
    vkey TEXT NOT NULL,
    server_addr TEXT NOT NULL DEFAULT '',
    server_port INTEGER NOT NULL DEFAULT 8024,
    enabled INTEGER NOT NULL DEFAULT 1,
    nps_task_id INTEGER,
    public_port INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_nps_tunnels_owner ON nps_tunnels(owner_id);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query_all(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    with connection() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    with connection() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    with connection() as conn:
        cursor = conn.execute(sql, tuple(params))
        return int(cursor.lastrowid or 0)


def execute_many(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    with connection() as conn:
        conn.executemany(sql, rows)


def get_setting(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def log_event(
    level: str,
    category: str,
    message: str,
    *,
    user_id: int | None = None,
    ip: str = "",
) -> None:
    execute(
        "INSERT INTO system_logs(level, category, message, user_id, ip, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (level, category, message[:2000], user_id, ip, utcnow()),
    )


def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.executescript(SCHEMA)
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        if "avatar_ext" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN avatar_ext TEXT NOT NULL DEFAULT ''"
            )
        if "status" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"
            )
        for user_row in conn.execute(
            "SELECT id, display_name FROM users WHERE display_name <> ''"
        ).fetchall():
            normalized = unicodedata.normalize(
                "NFC", user_row["display_name"].strip()
            )
            if normalized != user_row["display_name"]:
                conn.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (normalized, user_row["id"]),
                )
        share_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(shares)").fetchall()
        }
        if "nps_task_id" not in share_columns:
            conn.execute("ALTER TABLE shares ADD COLUMN nps_task_id INTEGER")
        if "public_port" not in share_columns:
            conn.execute("ALTER TABLE shares ADD COLUMN public_port INTEGER")
        tunnel_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(nps_tunnels)").fetchall()
        }
        if "nps_task_id" not in tunnel_columns:
            conn.execute("ALTER TABLE nps_tunnels ADD COLUMN nps_task_id INTEGER")
        if "public_port" not in tunnel_columns:
            conn.execute("ALTER TABLE nps_tunnels ADD COLUMN public_port INTEGER")
    _seed()


def _seed() -> None:
    if query_one("SELECT id FROM users LIMIT 1"):
        return
    now = utcnow()
    for username, password, display, role in (
        (settings.admin_username, settings.admin_password, "系统管理员", "admin"),
        ("demo", settings.demo_password, "演示用户", "user"),
    ):
        password_hash, salt = hash_password(password)
        execute(
            "INSERT INTO users(username, display_name, password_hash, password_salt, role, enabled, created_at) "
            "VALUES(?, ?, ?, ?, ?, 1, ?)",
            (username, display, password_hash, salt, role, now),
        )
    set_setting("oss_quota_bytes", str(settings.oss_default_quota_gb * 1024 ** 3))
    set_setting("web_base", settings.web_base)
    set_setting("nps_server_addr", settings.nps_server_addr)
    set_setting("nps_server_port", str(settings.nps_server_port))
    set_setting("nps_vkey", settings.nps_vkey)
    set_setting("nps_api_url", settings.nps_api_url)
    set_setting("nps_auth_key", settings.nps_auth_key)
    set_setting("nps_domain", settings.nps_domain)
    log_event("info", "system", "NexHub 服务端初始化完成")
