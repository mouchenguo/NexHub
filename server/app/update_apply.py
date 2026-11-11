"""服务端一键更新：下载部署包、校验、备份并原地安装。

只在管理员点击「一键更新」时执行，流程：

1. 依次尝试镜像（默认 47 下载站）和 GitHub Releases 的
   ``NexHub-Server-Deploy.zip``；
2. 用 Release / ``latest.json`` 里的 SHA-256 校验，失败自动换下一个源；
3. 解出内层 ``NexHub-Server-Bundle.zip``，比对包内 ``app/config.py`` 的版本号；
4. 备份 ``app/``、``run.py``、``requirements.txt`` 到 ``data/backups/server-<版本>-<时间>``；
5. 替换 ``app/`` 与启动文件；``.env``、``data/``、``downloads/`` 不受影响；
6. 写入 ``data/update_pending.json``，由后台提示管理员重启服务端使其生效。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests

from .config import settings
from .update_service import SERVER_PACKAGE_NAME, ServerUpdateInfo

CHUNK = 512 * 1024
DOWNLOAD_TIMEOUT = (20, 300)
PENDING_FILE = "update_pending.json"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")
VERSION_PATTERN = re.compile(r'version\s*:\s*str\s*=\s*"([^"]+)"')


class UpdateError(RuntimeError):
    """一键更新过程中的可读错误。"""


def pending_path() -> Path:
    return settings.data_dir / PENDING_FILE


def read_pending() -> dict:
    try:
        data = json.loads(pending_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def clear_pending() -> None:
    try:
        pending_path().unlink()
    except OSError:
        pass


def write_pending(record: dict) -> Path:
    path = pending_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _download_one(url: str, dest: Path) -> str:
    """流式下载到 ``dest``，返回大写的 SHA-256。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    total = 0
    with requests.get(
        url,
        stream=True,
        timeout=DOWNLOAD_TIMEOUT,
        headers={
            "User-Agent": "NexHub-Server-Updater",
            "Accept": "application/octet-stream",
        },
    ) as response:
        response.raise_for_status()
        with dest.open("wb") as handle:
            for chunk in response.iter_content(CHUNK):
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                total += len(chunk)
    if total == 0:
        raise UpdateError("下载内容为空")
    return digest.hexdigest().upper()


def download_package(
    urls: list[str],
    dest: Path,
    expected_sha256: str = "",
    log: Callable[[str], None] | None = None,
) -> tuple[Path, str]:
    """按顺序尝试下载地址，返回 ``(文件, 成功地址)``。"""
    log = log or (lambda message: None)
    errors: list[str] = []
    for url in urls:
        log(f"下载 {url}")
        try:
            digest = _download_one(url, dest)
        except Exception as exc:  # 网络类异常统一降级到下一个源
            errors.append(f"{url} → {exc}")
            log(f"失败：{exc}")
            continue
        if expected_sha256 and digest != expected_sha256.strip().upper():
            errors.append(f"{url} → SHA-256 不匹配")
            log("SHA-256 不匹配，换下一个源")
            try:
                dest.unlink()
            except OSError:
                pass
            continue
        return dest, url
    raise UpdateError("下载部署包失败：" + "；".join(errors or ["没有可用的下载地址"]))


def _safe_extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as package:
        for info in package.infolist():
            target = (dest / info.filename).resolve()
            if target != root and root not in target.parents:
                raise UpdateError(f"部署包含有非法路径：{info.filename}")
        package.extractall(dest)


def _bundle_dir(stage: Path) -> Path:
    """部署包是「外层 zip + 内层 bundle」，统一返回程序包根目录。"""
    nested = stage / SERVER_PACKAGE_NAME
    candidates = [nested] if nested.is_file() else sorted(stage.glob("*.zip"))
    for archive in candidates:
        target = stage / "bundle"
        shutil.rmtree(target, ignore_errors=True)
        _safe_extract(archive, target)
        if (target / "run.py").is_file() and (target / "app" / "main.py").is_file():
            return target
    if (stage / "run.py").is_file() and (stage / "app" / "main.py").is_file():
        return stage
    raise UpdateError("部署包里找不到 run.py 与 app/main.py")


def _read_bundle_version(bundle: Path) -> str:
    config = bundle / "app" / "config.py"
    try:
        text = config.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = VERSION_PATTERN.search(text)
    return match.group(1).strip().lstrip("v") if match else ""


def _backup_current(root: Path, version: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = settings.data_dir / "backups" / f"server-{version or 'unknown'}-{stamp}"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("app", "run.py", "requirements.txt", ".env.example"):
        source = root / name
        if source.is_dir():
            shutil.copytree(source, target / name, ignore=IGNORE)
        elif source.is_file():
            shutil.copy2(source, target / name)
    return target


def _replace_tree(root: Path, bundle: Path) -> None:
    """先把新 ``app/`` 落到 ``.app-staged``，再原子换入，失败时回滚。"""
    staged = root / ".app-staged"
    previous = root / ".app-previous"
    shutil.rmtree(staged, ignore_errors=True)
    shutil.rmtree(previous, ignore_errors=True)
    shutil.copytree(bundle / "app", staged, ignore=IGNORE)

    had_app = (root / "app").is_dir()
    if had_app:
        os.replace(root / "app", previous)
    try:
        os.replace(staged, root / "app")
    except OSError:
        if had_app and previous.is_dir() and not (root / "app").exists():
            os.replace(previous, root / "app")
        raise
    shutil.rmtree(previous, ignore_errors=True)

    for name in ("run.py", "requirements.txt", ".env.example"):
        source = bundle / name
        if source.is_file():
            shutil.copy2(source, root / name)


def _sync_requirements(root: Path, changed: bool) -> str:
    if not changed:
        return "unchanged"
    if not settings.update_install_requirements:
        return "skipped"
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-q",
                "--disable-pip-version-check",
                "-r",
                str(root / "requirements.txt"),
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"failed: {exc}"
    return "ok" if result.returncode == 0 else "failed"


def _candidate_urls(info: ServerUpdateInfo, mirror_url: str) -> list[str]:
    urls: list[str] = []
    if mirror_url:
        urls.append(f"{mirror_url.rstrip('/')}/{SERVER_PACKAGE_NAME}")
    if info.server_download_url:
        urls.append(info.server_download_url)
    unique: list[str] = []
    for url in urls:
        if url and url not in unique:
            unique.append(url)
    return unique


def apply_server_update(
    info: ServerUpdateInfo,
    mirror_url: str = "",
    base_dir: Path | None = None,
    force: bool = False,
    log: Callable[[str], None] | None = None,
) -> dict:
    """下载并安装服务端新版本，返回安装结果（含备份目录）。"""
    log = log or (lambda message: None)
    root = Path(base_dir) if base_dir else settings.base_dir
    if not (root / "run.py").is_file() or not (root / "app" / "main.py").is_file():
        raise UpdateError(f"{root} 不是 NexHub 服务端目录")
    if not info.update_available and not force:
        raise UpdateError(f"当前已是最新版本 v{info.current_version}")

    urls = _candidate_urls(info, mirror_url)
    if not urls:
        raise UpdateError("没有可用的下载地址")

    work_parent = settings.upload_temp_dir
    work_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="update-", dir=str(work_parent)))
    try:
        package, source = download_package(
            urls, work / SERVER_PACKAGE_NAME, info.server_sha256, log
        )
        log(f"校验通过：{sha256_file(package)}")
        stage = work / "package"
        _safe_extract(package, stage)
        bundle = _bundle_dir(stage)
        version = _read_bundle_version(bundle) or info.latest_version
        if not force and version != info.latest_version:
            raise UpdateError(
                f"下载包版本 v{version} 与目标版本 v{info.latest_version} 不一致，请稍后重试"
            )

        old_requirements = root / "requirements.txt"
        old_text = old_requirements.read_text(encoding="utf-8") if old_requirements.is_file() else ""
        new_requirements = bundle / "requirements.txt"
        new_text = new_requirements.read_text(encoding="utf-8") if new_requirements.is_file() else ""
        requirements_changed = new_text != old_text

        backup = _backup_current(root, info.current_version)
        log(f"已备份到 {backup}")
        _replace_tree(root, bundle)
        pip_state = _sync_requirements(root, requirements_changed)

        record = {
            "version": version,
            "previous_version": info.current_version,
            "applied_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "backup": str(backup),
            "source": source,
            "requirements": pip_state,
        }
        write_pending(record)
        log("安装完成，等待重启")
        return record
    finally:
        shutil.rmtree(work, ignore_errors=True)
