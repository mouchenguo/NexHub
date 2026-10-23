"""下载任务构造：把共享 / 远程主机文件转换为可执行下载任务。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..api_client import ApiClient
from ..compat import CLIENT_API_VERSION
from ..config import AppConfig
from ..paths import VERSION
from .demo_store import DemoStore


@dataclass
class DownloadJob:
    relative_path: str
    size: int
    url: str = ""
    local_source: str = ""
    headers: dict[str, str] = field(default_factory=dict)


class DownloadService:
    def __init__(self, config: AppConfig, api: ApiClient, demo: DemoStore):
        self.config = config
        self.api = api
        self.demo = demo

    @property
    def online(self) -> bool:
        return not self.config.demo_mode

    def share_jobs(self, share: dict, files: list[dict]) -> tuple[list[DownloadJob], list[str]]:
        jobs: list[DownloadJob] = []
        skipped: list[str] = []
        headers = {
            "User-Agent": f"NexHub-Client/{VERSION}",
            "X-NexHub-Client-Version": VERSION,
            "X-NexHub-Client-Api-Version": str(CLIENT_API_VERSION),
        }
        if self.config.token:
            headers["Authorization"] = f"Bearer {self.config.token}"
        for item in files:
            path = item.get("path", "")
            try:
                if self.online:
                    jobs.append(
                        DownloadJob(
                            relative_path=path,
                            size=int(item.get("size") or 0),
                            url=self.api.share_download_url(int(share["id"]), path),
                            headers=headers,
                        )
                    )
                else:
                    jobs.append(
                        DownloadJob(
                            relative_path=path,
                            size=int(item.get("size") or 0),
                            local_source=self.demo.local_share_file(int(share["id"]), path),
                        )
                    )
            except (OSError, ValueError):
                skipped.append(path)
        return jobs, skipped

