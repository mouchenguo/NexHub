"""个人网盘（OSS）业务：登记文件、删除、从网盘直接创建共享。"""
from __future__ import annotations

import json

from . import db
from .common import drive_used, human_size, normalize_rel_path, share_payload
from .oss_service import oss_service
from .security import generate_token
from .share_ops import cancel_share, fetch_share


def drive_prefix(owner_id: int) -> str:
    """网盘对象的 OSS 前缀，与共享 token 前缀分开，取消共享时不会删掉网盘副本。"""
    return f"drive-{int(owner_id)}"


def drive_file(owner_id: int, file_id: int) -> dict | None:
    return db.query_one(
        "SELECT * FROM drive_files WHERE id = ? AND owner_id = ?",
        (int(file_id), int(owner_id)),
    )


def drive_items(owner_id: int) -> list[dict]:
    rows = db.query_all(
        "SELECT * FROM drive_files WHERE owner_id = ? ORDER BY created_at DESC, id DESC",
        (int(owner_id),),
    )
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "path": row["rel_path"],
            "size": int(row["size"] or 0),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def register_drive_file(owner_id: int, rel_path: str, object_key: str, size: int) -> int:
    """把已上传到 OSS 的对象登记到网盘（不会再次上传）。"""
    relative = normalize_rel_path(rel_path)
    name = relative.rsplit("/", 1)[-1]
    existing = db.query_one(
        "SELECT id, object_key FROM drive_files WHERE owner_id = ? AND rel_path = ?",
        (int(owner_id), relative),
    )
    now = db.utcnow()
    if existing:
        db.execute(
            "UPDATE drive_files SET object_key = ?, size = ?, name = ?, created_at = ? WHERE id = ?",
            (object_key, int(size), name, now, int(existing["id"])),
        )
        old_key = existing["object_key"]
        if old_key and old_key != object_key and not _key_referenced(old_key):
            oss_service.delete_object(old_key)
        return int(existing["id"])
    db.execute(
        "INSERT INTO drive_files(owner_id, name, rel_path, object_key, size, created_at) "
        "VALUES(?, ?, ?, ?, ?, ?)",
        (int(owner_id), name, relative, object_key, int(size), now),
    )
    return 0


def _key_referenced(object_key: str) -> bool:
    share_row = db.query_one(
        "SELECT 1 AS hit FROM oss_objects WHERE object_key = ? LIMIT 1", (object_key,)
    )
    drive_row = db.query_one(
        "SELECT 1 AS hit FROM drive_files WHERE object_key = ? LIMIT 1", (object_key,)
    )
    return bool(share_row or drive_row)


def delete_drive_file(user: dict, file_id: int, ip: str = "") -> None:
    row = drive_file(int(user["id"]), file_id)
    if not row:
        raise KeyError("网盘文件不存在")
    referencing = db.query_all(
        "SELECT s.* FROM shares s JOIN oss_objects o ON o.share_id = s.id "
        "WHERE o.object_key = ?",
        (row["object_key"],),
    )
    for share in referencing:
        if share.get("status") == "active":
            cancel_share(share, int(user["id"]), ip, reason="网盘文件已删除")
    db.execute("DELETE FROM oss_objects WHERE object_key = ?", (row["object_key"],))
    oss_service.delete_object(row["object_key"])
    db.execute("DELETE FROM drive_files WHERE id = ?", (int(row["id"]),))
    db.log_event(
        "info",
        "oss",
        f"删除网盘文件：{row['name']}（{human_size(row['size'])}）",
        user_id=user["id"],
        ip=ip,
    )


def share_drive_files(
    user: dict,
    rows: list[dict],
    name: str = "",
    description: str = "",
    base: str = "",
) -> dict:
    """用网盘里已有的 OSS 对象创建共享，不再上传任何文件。"""
    if not rows:
        raise ValueError("请选择要共享的网盘文件")
    manifest: dict[str, int] = {}
    for row in rows:
        manifest[row["name"]] = int(row["size"] or 0)
    items = [{"path": path, "size": size} for path, size in manifest.items()]
    total = sum(item["size"] for item in items)
    share_name = (name or "").strip() or (items[0]["path"] if len(items) == 1 else f"{user['username']} 的网盘共享")
    token = generate_token(18)
    share_id = db.execute(
        "INSERT INTO shares(token, owner_id, name, description, storage_type, total_size, "
        "file_count, status, local_url, manifest_json, downloads, created_at, mirror_to_drive) "
        "VALUES(?, ?, ?, ?, 'oss', ?, ?, 'active', '', ?, 0, ?, 1)",
        (
            token,
            user["id"],
            share_name,
            (description or "").strip(),
            total,
            len(items),
            json.dumps(items, ensure_ascii=False),
            db.utcnow(),
        ),
    )
    for row in rows:
        db.execute(
            "INSERT INTO oss_objects(share_id, object_key, relative_path, size, created_at) "
            "VALUES(?, ?, ?, ?, ?) "
            "ON CONFLICT(share_id, relative_path) DO UPDATE SET "
            "object_key = excluded.object_key, size = excluded.size",
            (share_id, row["object_key"], row["name"], int(row["size"] or 0), db.utcnow()),
        )
    share = fetch_share(share_id)
    db.log_event(
        "info",
        "share",
        f"从网盘创建共享：{share_name}（{len(items)} 个文件，{human_size(total)}）",
        user_id=user["id"],
    )
    payload = share_payload(share, int(user["id"]), base)
    payload["drive_used"] = drive_used(int(user["id"]))
    return payload
