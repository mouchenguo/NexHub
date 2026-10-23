"""NexHub 插件包校验、待审核队列与发布清单。

插件广场的公开下载目录和审核队列都位于 8001 服务自己的 ``dist`` 目录；
这里不依赖主站 FastAPI 服务，符合“插件系统单独部署”的边界。
"""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9._+-]{1,32}$")
SAFE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ICON_BYTES = 2 * 1024 * 1024
MAX_PENDING = 50


class MarketError(ValueError):
    """上传或审核失败时返回给页面的可读错误。"""


@dataclass
class ValidatedPackage:
    manifest: dict[str, str]
    icon: bytes | None
    sha256: str
    size: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _clean_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) > limit:
        raise MarketError(f"字段长度不能超过 {limit} 个字符")
    return text


class PluginMarket:
    def __init__(self, dist_dir: Path, base_dir: Path | None = None) -> None:
        self.dist = Path(dist_dir).resolve()
        self.base_dir = Path(base_dir or self.dist.parent).resolve()
        self.downloads_dir = self.dist / "downloads"
        self.icons_dir = self.dist / "icons"
        self.pending_dir = self.dist / "pending"
        for directory in (self.downloads_dir, self.icons_dir, self.pending_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.index_path = self.dist / "index.json"
        self.review_log_path = self.dist / "review_log.json"
        self.password_file = self.base_dir / "review_password.txt"
        self._lock = threading.RLock()

    # ------------------------------------------------------------- public data
    def plugins(self) -> list[dict]:
        data = _json_read(self.index_path, {"plugins": []})
        items = data.get("plugins") if isinstance(data, dict) else []
        return [item for item in items if isinstance(item, dict) and item.get("id")]

    def stats(self) -> dict:
        return {
            "plugins": len(self.plugins()),
            "pending": len(self.pending()),
        }

    def pending(self) -> list[dict]:
        items: list[dict] = []
        for meta_path in sorted(self.pending_dir.glob("*.json")):
            meta = _json_read(meta_path, None)
            if not isinstance(meta, dict):
                continue
            upload_id = meta_path.stem
            package = self.pending_dir / f"{upload_id}.zip"
            if not package.is_file():
                continue
            meta["upload_id"] = upload_id
            items.append(meta)
        items.sort(key=lambda item: str(item.get("submitted_at") or ""), reverse=True)
        return items

    def review_log(self, limit: int = 20) -> list[dict]:
        data = _json_read(self.review_log_path, [])
        return data[:limit] if isinstance(data, list) else []

    def get_pending(self, upload_id: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{32}", upload_id or ""):
            raise MarketError("待审核编号不合法")
        meta = _json_read(self.pending_dir / f"{upload_id}.json", None)
        if not isinstance(meta, dict):
            raise MarketError("待审核插件不存在或已处理")
        return meta

    # ------------------------------------------------------------- credentials
    def review_credentials(self) -> tuple[str, str]:
        user = os.environ.get("NEXHUB_PLUGIN_REVIEW_USER", "admin").strip() or "admin"
        password = os.environ.get("NEXHUB_PLUGIN_REVIEW_PASSWORD", "").strip()
        if password:
            return user, password
        try:
            password = self.password_file.read_text(encoding="utf-8").strip()
        except OSError:
            password = ""
        if not password:
            password = secrets.token_urlsafe(18)
            self.base_dir.mkdir(parents=True, exist_ok=True)
            self.password_file.write_text(password + "\n", encoding="utf-8")
            try:
                self.password_file.chmod(0o600)
            except OSError:
                pass
        return user, password

    def check_review_auth(self, user: str, password: str) -> bool:
        expected_user, expected_password = self.review_credentials()
        return hmac.compare_digest(user, expected_user) and hmac.compare_digest(password, expected_password)

    # ------------------------------------------------------------- validation
    def _validate_zip(self, data: bytes, filename: str) -> ValidatedPackage:
        if not data:
            raise MarketError("上传文件为空")
        if len(data) > MAX_ARCHIVE_BYTES:
            raise MarketError("插件包不能超过 128 MB")
        safe_filename = Path(filename or "").name
        if not safe_filename.lower().endswith(".zip"):
            raise MarketError("只接受 .zip 插件包")
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise MarketError("文件不是有效的 zip 包") from exc
        with archive:
            names = [item.filename for item in archive.infolist() if not item.is_dir()]
            total_size = sum(max(0, item.file_size) for item in archive.infolist())
            if total_size > MAX_UNCOMPRESSED_BYTES:
                raise MarketError("插件包解压后过大")
            normalized_names: list[PurePosixPath] = []
            for name in names:
                normalized = PurePosixPath(name)
                if (
                    normalized.is_absolute()
                    or ".." in normalized.parts
                    or "\\" in name
                    or not normalized.parts
                ):
                    raise MarketError("插件包包含不安全的文件路径")
                normalized_names.append(normalized)
            # 兼容两种常见布局：manifest 在 zip 根目录，或整个插件位于
            # 唯一一层文件夹中（例如 memory_optimizer/manifest.json）。
            prefix = ""
            if "manifest.json" not in names:
                top_levels = {item.parts[0] for item in normalized_names if len(item.parts) > 1}
                if len(top_levels) == 1:
                    candidate = next(iter(top_levels)) + "/"
                    if candidate + "manifest.json" in names:
                        prefix = candidate
            required = {prefix + "manifest.json", prefix + "plugin.py"}
            missing = required - set(names)
            if missing:
                raise MarketError(f"插件包缺少：{', '.join(sorted(item.removeprefix(prefix) for item in missing))}")
            try:
                raw_manifest = archive.read(prefix + "manifest.json")
                manifest_data = json.loads(raw_manifest.decode("utf-8"))
            except (KeyError, UnicodeDecodeError, ValueError, TypeError) as exc:
                raise MarketError("manifest.json 不是有效的 UTF-8 JSON") from exc
            if not isinstance(manifest_data, dict):
                raise MarketError("manifest.json 必须是 JSON 对象")
            plugin_id = _clean_text(manifest_data.get("id"), 64)
            if not PLUGIN_ID_RE.fullmatch(plugin_id):
                raise MarketError("插件 id 只能包含字母、数字、下划线、点和短横线")
            version = _clean_text(manifest_data.get("version"), 32)
            if not VERSION_RE.fullmatch(version):
                raise MarketError("插件 version 不合法")
            manifest = {
                "id": plugin_id,
                "name": _clean_text(manifest_data.get("name"), 80) or plugin_id,
                "version": version,
                "description": _clean_text(manifest_data.get("description"), 500),
                "requirements": _clean_text(manifest_data.get("requirements"), 300),
                "category": _clean_text(manifest_data.get("category"), 40) or "其他",
                "glyph": _clean_text(manifest_data.get("glyph"), 4) or "✦",
            }
            if not manifest["description"]:
                raise MarketError("插件 description 不能为空")
            icon = None
            if prefix + "icon.png" in names:
                icon = archive.read(prefix + "icon.png")
                if len(icon) > MAX_ICON_BYTES or not icon.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise MarketError("插件 icon.png 必须是小于 2 MB 的 PNG")
        return ValidatedPackage(
            manifest=manifest,
            icon=icon,
            sha256=hashlib.sha256(data).hexdigest().upper(),
            size=len(data),
        )

    # ------------------------------------------------------------- upload queue
    def queue_upload(self, data: bytes, filename: str, uploader: str, notes: str) -> dict:
        package = self._validate_zip(data, filename)
        uploader = _clean_text(uploader, 80)
        notes = _clean_text(notes, 500)
        with self._lock:
            if len(self.pending()) >= MAX_PENDING:
                raise MarketError("审核队列已满，请先处理已有上传")
            upload_id = uuid.uuid4().hex
            safe_filename = Path(filename or "").name
            if not SAFE_FILE_RE.fullmatch(safe_filename):
                safe_filename = f"{package.manifest['id']}.zip"
            package_path = self.pending_dir / f"{upload_id}.zip"
            package_path.write_bytes(data)
            meta = {
                "upload_id": upload_id,
                "filename": safe_filename,
                "uploader": uploader,
                "notes": notes,
                "submitted_at": _utc_now(),
                "manifest": package.manifest,
                "sha256": package.sha256,
                "size": package.size,
            }
            _json_write(self.pending_dir / f"{upload_id}.json", meta)
            return meta

    # ------------------------------------------------------------- review
    def _append_review_log(self, action: str, meta: dict, reviewer: str) -> None:
        manifest = meta.get("manifest") or {}
        entry = {
            "time": _utc_now(),
            "action": action,
            "upload_id": meta.get("upload_id", ""),
            "plugin_id": manifest.get("id", ""),
            "name": manifest.get("name", ""),
            "version": manifest.get("version", ""),
            "reviewer": reviewer,
        }
        logs = _json_read(self.review_log_path, [])
        if not isinstance(logs, list):
            logs = []
        logs.insert(0, entry)
        _json_write(self.review_log_path, logs[:100])

    def approve(self, upload_id: str, reviewer: str) -> dict:
        with self._lock:
            meta = self.get_pending(upload_id)
            package_path = self.pending_dir / f"{upload_id}.zip"
            data = package_path.read_bytes()
            package = self._validate_zip(data, str(meta.get("filename") or "plugin.zip"))
            manifest = package.manifest
            plugin_id = manifest["id"]
            entries = self.plugins()
            existing = next((item for item in entries if item.get("id") == plugin_id), None)
            target_name = str(meta.get("filename") or f"{plugin_id}.zip")
            if not SAFE_FILE_RE.fullmatch(target_name) or not target_name.lower().endswith(".zip"):
                target_name = f"{plugin_id}.zip"
            target_path = self.downloads_dir / target_name
            # 没有冲突时沿用上传文件名；与别的插件冲突时退回 id.zip。
            if target_path.exists() and not existing:
                target_name = f"{plugin_id}.zip"
                target_path = self.downloads_dir / target_name
            temp_path = target_path.with_name(f".{target_name}.{uuid.uuid4().hex}.tmp")
            temp_path.write_bytes(data)
            temp_path.replace(target_path)
            icon_url = str((existing or {}).get("icon_url") or "")
            if package.icon:
                icon_name = f"{plugin_id}.png"
                icon_path = self.icons_dir / icon_name
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", icon_name):
                    icon_name = f"plugin-{uuid.uuid4().hex[:12]}.png"
                    icon_path = self.icons_dir / icon_name
                icon_path.write_bytes(package.icon)
                icon_url = f"icons/{icon_name}"
            entry = {
                **manifest,
                "download_url": f"downloads/{target_name}",
                "sha256": package.sha256,
                "size": package.size,
            }
            if icon_url:
                entry["icon_url"] = icon_url
            entries = [item for item in entries if item.get("id") != plugin_id]
            entries.append(entry)
            _json_write(self.index_path, {"plugins": entries})
            # 只有确认新包已写入清单后才删除待审核文件。
            package_path.unlink(missing_ok=True)
            (self.pending_dir / f"{upload_id}.json").unlink(missing_ok=True)
            self._append_review_log("approve", meta, reviewer)
            if existing and existing.get("download_url") != entry["download_url"]:
                old_name = Path(str(existing.get("download_url") or "")).name
                if old_name and old_name != target_name:
                    (self.downloads_dir / old_name).unlink(missing_ok=True)
            return entry

    def reject(self, upload_id: str, reviewer: str) -> None:
        with self._lock:
            meta = self.get_pending(upload_id)
            (self.pending_dir / f"{upload_id}.zip").unlink(missing_ok=True)
            (self.pending_dir / f"{upload_id}.json").unlink(missing_ok=True)
            self._append_review_log("reject", meta, reviewer)

