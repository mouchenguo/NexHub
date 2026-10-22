"""共享业务逻辑：创建、查询、取消、删除与下载任务。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..api_client import ApiClient, ApiError
from ..config import AppConfig
from ..embedded_server import EmbeddedServer, register_share, unregister_share
from ..file_utils import OSS_LIMIT_BYTES, collect_manifest
from .demo_store import DemoStore
from .download_service import DownloadJob, DownloadService
from .nps_service import temp_share_base

ProgressCallback = Callable[[int, str], None]


class ShareService:
    def __init__(
        self,
        config: AppConfig,
        api: ApiClient,
        demo: DemoStore,
        embedded: EmbeddedServer,
        downloads: DownloadService,
    ):
        self.config = config
        self.api = api
        self.demo = demo
        self.embedded = embedded
        self.downloads = downloads

    @property
    def online(self) -> bool:
        return not self.config.demo_mode

    # ------------------------------------------------------------------
    def list_my_shares(self) -> list[dict]:
        return self.api.list_shares("mine") if self.online else self.demo.list_shares("mine")

    def list_public_shares(self) -> list[dict]:
        return self.api.list_shares("public") if self.online else self.demo.list_shares("public")

    def manifest(self, share: dict) -> dict:
        if self.online:
            return self.api.share_manifest(int(share["id"]))
        return self.demo.manifest_for(int(share["id"]))

    # ------------------------------------------------------------------
    def create_share(
        self,
        source: str,
        storage_type: str,
        description: str = "",
        progress: ProgressCallback | None = None,
    ) -> dict:
        """创建共享。耗时操作（扫描、上传）应在 QThread 中调用。

        - ``temp``：启动内嵌服务并注册 token，关闭软件或取消共享即失效。
        - ``oss``：文件经 ECS 后端流式中转上传，客户端不持有 OSS 密钥。
        """
        self._report(progress, 2, "正在扫描文件...")
        manifest = collect_manifest(source)
        files = manifest["files"]
        total = int(manifest["total_size"])
        if not files:
            raise ValueError("该路径下没有可共享的文件")
        if storage_type == "oss" and total >= OSS_LIMIT_BYTES:
            raise ValueError("文件总大小已达到 5GB，仅支持临时穿透共享")
        storage_type = "oss" if storage_type == "oss" else "temp"

        public_base = temp_share_base(self.config, self.embedded.base_url)
        payload = {
            "name": manifest["name"],
            "description": description,
            "storage_type": storage_type,
            "total_size": total,
            "file_count": len(files),
            "files": files,
            "local_url": public_base if storage_type == "temp" else "",
        }

        if not self.online:
            self._report(progress, 30, "正在生成本地演示共享...")
            share = self.demo.create_share(
                name=manifest["name"],
                storage_type=storage_type,
                manifest=files,
                local_root=manifest["root"],
                description=description,
            )
            if storage_type == "temp":
                self._start_local_share(share, manifest, files)
            self._report(progress, 100, "共享已发布（离线体验）")
            share["demo_note"] = "离线体验模式，链接仅在当前设备可用"
            return share

        self._report(progress, 12, "正在向 ECS 发布共享...")
        share = self.api.create_share(payload)
        if storage_type == "temp":
            self._start_local_share(share, manifest, files)
            self._report(progress, 100, "临时共享已发布")
        else:
            self._upload_to_oss(share, manifest, progress, source)
        return share

    def _start_local_share(self, share: dict, manifest: dict, files: list[dict]) -> None:
        try:
            self.embedded.start()
        except RuntimeError as exc:
            raise RuntimeError(f"内嵌文件服务启动失败：{exc}") from exc
        register_share(share["token"], manifest["name"], manifest["root"], files)

    def _upload_to_oss(
        self,
        share: dict,
        manifest: dict,
        progress: ProgressCallback | None,
        source: str,
    ) -> None:
        files = manifest["files"]
        root = Path(source).resolve()
        if root.is_file():
            root = root.parent
        total = len(files)
        for index, item in enumerate(files, start=1):
            local_path = root / item["path"]
            self._report(
                progress,
                20 + int(index / max(1, total) * 78),
                f"正在经后端上传 OSS（{index}/{total}）：{item['path']}",
            )
            try:
                self.api.upload_oss_file(int(share["id"]), item["path"], str(local_path))
            except ApiError as exc:
                self._report(progress, 0, "OSS 上传失败")
                raise RuntimeError(
                    f"文件 {item['path']} 上传失败：{exc.message}。"
                    "共享已创建但处于待上传状态，请检查后端 OSS 配置。"
                ) from exc
        self._report(progress, 100, "OSS 缓存完成")

    # ------------------------------------------------------------------
    def cancel_share(self, share: dict) -> dict:
        result = (
            self.api.cancel_share(int(share["id"]))
            if self.online
            else self.demo.cancel_share(int(share["id"]))
        )
        if share.get("storage_type") == "temp":
            unregister_share(str(share.get("token", "")))
        return result

    def delete_share(self, share: dict) -> dict:
        result = (
            self.api.delete_share(int(share["id"]))
            if self.online
            else self.demo.delete_share(int(share["id"]))
        )
        unregister_share(str(share.get("token", "")))
        return result

    def public_link(self, share: dict) -> str:
        if share.get("public_url"):
            return str(share["public_url"])
        if self.online:
            return f"{self.config.server_url.rstrip('/')}/s/{share.get('token', '')}"
        return ""

    def build_download_jobs(
        self, share: dict, selected_files: list[dict]
    ) -> tuple[list[DownloadJob], list[str]]:
        return self.downloads.share_jobs(share, selected_files)

    # ------------------------------------------------------------------
    @staticmethod
    def _report(callback: ProgressCallback | None, percent: int, message: str) -> None:
        if callback:
            callback(max(0, min(100, percent)), message)


def selected_size(files: list[dict]) -> int:
    return sum(int(item.get("size") or 0) for item in files)
