"""后台线程模块。

所有耗时操作（目录扫描、OSS 上传、文件下载、进程轮询）都放入 QThread，
通过信号回到 GUI 线程，禁止阻塞界面。
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import requests
from PyQt6.QtCore import QThread, pyqtSignal

from .file_utils import ensure_unique_path, safe_join
from .services.download_service import DownloadJob
from .services.share_service import ShareService


class FunctionWorker(QThread):
    """通用后台任务：执行任意可调用对象并返回结果。"""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001 - 需要把错误展示给 GUI
            self.failed.emit(str(exc) or exc.__class__.__name__)


class ShareCreateWorker(QThread):
    """创建共享：扫描目录 + OSS 上传，全部在后台完成。"""

    progress_changed = pyqtSignal(int, str)
    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        service: ShareService,
        source: str,
        storage_type: str,
        description: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.service = service
        self.source = source
        self.storage_type = storage_type
        self.description = description

    def run(self) -> None:
        try:
            result = self.service.create_share(
                self.source,
                self.storage_type,
                self.description,
                progress=lambda percent, message: self.progress_changed.emit(percent, message),
            )
            self.succeeded.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc) or exc.__class__.__name__)


class DownloadWorker(QThread):
    """流式下载/复制多个文件，支持进度与取消。"""

    progress_changed = pyqtSignal(int, int, str)  # done bytes, total bytes, current file
    file_completed = pyqtSignal(str)
    log = pyqtSignal(str)
    completed = pyqtSignal(object)  # list[str] errors

    CHUNK_SIZE = 1024 * 256

    def __init__(self, jobs: list[DownloadJob], target_dir: str, parent=None):
        super().__init__(parent)
        self.jobs = jobs
        self.target_dir = Path(target_dir)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        total = max(1, sum(max(0, job.size) for job in self.jobs))
        done = 0
        errors: list[str] = []
        session = requests.Session()
        try:
            self.target_dir.mkdir(parents=True, exist_ok=True)
            for job in self.jobs:
                if self._cancelled:
                    self.log.emit("下载已取消")
                    break
                try:
                    target = safe_join(self.target_dir, job.relative_path)
                    target = ensure_unique_path(target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    part = target.with_suffix(target.suffix + ".part")
                    self.progress_changed.emit(done, total, job.relative_path)

                    if job.local_source:
                        done += self._copy_local(job, part, done, total)
                    else:
                        done += self._stream_http(session, job, part, done, total)

                    if self._cancelled:
                        part.unlink(missing_ok=True)
                        break
                    part.replace(target)
                    self.file_completed.emit(str(target))
                    if job.size == 0:
                        done += 0
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{job.relative_path}: {exc}")
                    self.log.emit(f"下载失败：{job.relative_path} — {exc}")
            if not self._cancelled:
                self.progress_changed.emit(total, total, "")
        finally:
            session.close()
            self.completed.emit(errors)

    def _copy_local(self, job: DownloadJob, part: Path, done: int, total: int) -> int:
        copied = 0
        with open(job.local_source, "rb") as source, part.open("wb") as target:
            while True:
                if self._cancelled:
                    break
                chunk = source.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                target.write(chunk)
                copied += len(chunk)
                self.progress_changed.emit(done + copied, total, job.relative_path)
        return copied

    def _stream_http(
        self, session: requests.Session, job: DownloadJob, part: Path, done: int, total: int
    ) -> int:
        copied = 0
        with session.get(
            job.url, headers=job.headers, stream=True, timeout=(10, 300)
        ) as response:
            response.raise_for_status()
            with part.open("wb") as target:
                for chunk in response.iter_content(self.CHUNK_SIZE):
                    if self._cancelled:
                        break
                    if not chunk:
                        continue
                    target.write(chunk)
                    copied += len(chunk)
                    self.progress_changed.emit(done + copied, total, job.relative_path)
        return copied
