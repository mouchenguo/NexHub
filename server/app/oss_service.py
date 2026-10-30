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


NETWORK_PUBLIC = "public"
NETWORK_INTERNAL = "internal"
NETWORK_LABELS = {NETWORK_PUBLIC: "公网", NETWORK_INTERNAL: "内网"}


def _normalize_endpoint(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    return value.rstrip("/")


def derive_oss_endpoints(endpoint: str) -> tuple[str, str]:
    """从 OSS_ENDPOINT 推导 (公网, 内网) endpoint。

    阿里云标准 endpoint 通过 ``-internal`` 区分 VPC 内网和公网；
    非标准/自定义 endpoint 无法推导时，两边返回同一个值。
    """
    value = _normalize_endpoint(endpoint)
    if not value:
        return "", ""
    if "-internal." in value:
        return value.replace("-internal.", ".", 1), value
    if ".aliyuncs.com" in value:
        return value, value.replace(".aliyuncs.com", "-internal.aliyuncs.com", 1)
    return value, value


class OssService:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        derived_public, derived_internal = derive_oss_endpoints(settings.oss_endpoint)
        self.public_endpoint = _normalize_endpoint(settings.oss_endpoint_public) or derived_public
        self.internal_endpoint = _normalize_endpoint(settings.oss_endpoint_internal) or derived_internal
        self.network = self._detect_network(settings.oss_endpoint)
        self.endpoint = self._endpoint_for(self.network)
        self.auth = None
        self.bucket = None
        self.remote = False
        self._build_remote()
        self.local_root = settings.local_oss_dir
        self.local_root.mkdir(parents=True, exist_ok=True)

    def _detect_network(self, endpoint: str) -> str:
        configured = str(settings.oss_network or "").strip().lower()
        if configured in NETWORK_LABELS:
            return configured
        value = _normalize_endpoint(endpoint)
        if "-internal." in value or value.endswith("-internal.aliyuncs.com"):
            return NETWORK_INTERNAL
        return NETWORK_PUBLIC

    def _endpoint_for(self, network: str) -> str:
        return self.internal_endpoint if network == NETWORK_INTERNAL else self.public_endpoint

    def _build_remote(self) -> None:
        endpoint = self._endpoint_for(self.network)
        self.remote = bool(
            oss2
            and endpoint
            and settings.oss_bucket
            and settings.oss_access_key_id
            and settings.oss_access_key_secret
        )
        if self.remote:
            self.auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
            self.bucket = oss2.Bucket(self.auth, endpoint, settings.oss_bucket)
            self.endpoint = endpoint
        else:
            self.auth = None
            self.bucket = None

    def switch_network(self, network: str) -> str:
        """切换 OSS 使用的公网/内网 endpoint，不迁移对象。"""
        mode = str(network or "").strip().lower()
        if mode not in NETWORK_LABELS:
            raise ValueError("OSS 网络类型必须是 public 或 internal")
        endpoint = self._endpoint_for(mode)
        if not endpoint:
            raise ValueError("当前没有配置可用的 OSS endpoint")
        if not (oss2 and settings.oss_bucket and settings.oss_access_key_id and settings.oss_access_key_secret):
            raise ValueError("OSS 未完整配置，无法切换网络")
        with self._lock:
            auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
            bucket = oss2.Bucket(auth, endpoint, settings.oss_bucket)
            self.auth = auth
            self.bucket = bucket
            self.network = mode
            self.endpoint = endpoint
            self.remote = True
        return endpoint

    def restore_network(self) -> None:
        """服务启动后恢复管理后台保存的网络选择。"""
        try:
            from . import db

            configured = db.get_setting("oss_network", "").strip().lower()
            if configured in NETWORK_LABELS:
                self.switch_network(configured)
        except Exception:
            # 数据库尚未初始化或 OSS 未配置时保持启动默认值。
            pass


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
            "endpoint": self.endpoint if self.remote else "",
            "network": self.network,
            "network_label": NETWORK_LABELS.get(self.network, self.network),
            "public_endpoint": self.public_endpoint,
            "internal_endpoint": self.internal_endpoint,
            "switchable": bool(
                self.remote
                and self.public_endpoint
                and self.internal_endpoint
                and self.public_endpoint != self.internal_endpoint
            ),
            "bucket": settings.oss_bucket if self.remote else "",
            "access_key_configured": bool(
                settings.oss_access_key_id and settings.oss_access_key_secret
            ),
            "prefix": settings.oss_prefix,
            "local_dir": str(self.local_root) if not self.remote else "",
        }


oss_service = OssService()
