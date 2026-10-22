"""阿里云 OSS 代理层。

客户端永远不接触 AccessKey / Secret：
- 配置齐全时使用 oss2 上传/读取 OSS；
- 未配置或 oss2 不可用时自动落到本地磁盘（便于开发与演示）。
"""
from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Iterator

from .config import settings
from .common import normalize_rel_path

try:
    import oss2  # type: ignore
except ImportError:  # pragma: no cover
    oss2 = None


class OssService:
    def __init__(self) -> None:
        self.remote = bool(
            oss2
            and settings.oss_endpoint
            and settings.oss_bucket
            and settings.oss_access_key_id
            and settings.oss_access_key_secret
        )
        self.bucket = None
        if self.remote:
            auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
            self.bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)
        self.local_root = settings.local_oss_dir
        self.local_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def object_key(self, share_token: str, relative_path: str) -> str:
        relative = normalize_rel_path(relative_path)
        return f"{settings.oss_prefix}/{share_token}/{relative}"

    def put_file(self, share_token: str, relative_path: str, source: Path) -> tuple[str, int]:
        key = self.object_key(share_token, relative_path)
        size = source.stat().st_size
        with self._lock:
            if self.remote:
                oss2.resumable_upload(
                    self.bucket,
                    key,
                    str(source),
                    multipart_threshold=32 * 1024 * 1024,
                    part_size=8 * 1024 * 1024,
                    num_threads=4,
                )
            else:
                target = self.local_root / key
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        return key, size

    def open_iter(self, key: str, chunk_size: int = 512 * 1024) -> Iterator[bytes]:
        if self.remote:
            result = self.bucket.get_object(key)

            def remote_iter():
                try:
                    while True:
                        chunk = result.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    try:
                        result.close()
                    except Exception:
                        pass

            return remote_iter()

        target = self.local_root / key

        def local_iter():
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        return local_iter()

    def local_path(self, key: str) -> Path | None:
        if self.remote:
            return None
        target = self.local_root / key
        return target if target.is_file() else None

    def exists(self, key: str) -> bool:
        if self.remote:
            try:
                return bool(self.bucket.object_exists(key))
            except Exception:
                return False
        return (self.local_root / key).is_file()

    def delete_prefix(self, share_token: str) -> int:
        prefix = f"{settings.oss_prefix}/{share_token}/"
        removed = 0
        with self._lock:
            if self.remote:
                try:
                    continuation = ""
                    while True:
                        result = self.bucket.list_objects_v2(
                            prefix=prefix, max_keys=500, continuation_token=continuation
                        )
                        keys = [obj.key for obj in result.object_list]
                        if keys:
                            self.bucket.batch_delete_objects(keys)
                            removed += len(keys)
                        if not result.is_truncated:
                            break
                        continuation = result.next_continuation_token
                except Exception:
                    pass
            else:
                target = self.local_root / prefix
                if target.exists():
                    removed = sum(1 for path in target.rglob("*") if path.is_file())
                    shutil.rmtree(target, ignore_errors=True)
        return removed

    def status(self) -> dict:
        return {
            "mode": "阿里云 OSS" if self.remote else "本地回退存储（未配置 OSS）",
            "remote": self.remote,
            "endpoint": settings.oss_endpoint if self.remote else "",
            "bucket": settings.oss_bucket if self.remote else "",
            "access_key_configured": bool(
                settings.oss_access_key_id and settings.oss_access_key_secret
            ),
            "prefix": settings.oss_prefix,
            "local_dir": str(self.local_root) if not self.remote else "",
        }


oss_service = OssService()
