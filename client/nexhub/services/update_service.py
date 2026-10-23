"""客户端更新检查、下载与自动安装。"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import requests

from ..config import AppConfig
from ..paths import VERSION


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    update_available: bool
    download_url: str
    page_url: str
    manifest_url: str
    notes: list[str]
    published_at: str
    sha256: str


def update_manifest_url(config: AppConfig) -> str:
    base = config.server_url.rstrip("/")
    return f"{base}/downloads/latest.json"


def version_key(value: str) -> tuple:
    """比较 v0.1.0-beta / 0.1.2-beta 这类版本号。"""
    text = (value or "").strip().lower().lstrip("v")
    if "-" in text:
        numeric, pre = text.split("-", 1)
    else:
        numeric, pre = text, ""
    numbers = []
    for part in re.split(r"[.\s]+", numeric):
        match = re.match(r"^(\d+)", part)
        if match:
            numbers.append(int(match.group(1)))
    while len(numbers) < 3:
        numbers.append(0)
    pre = pre.strip()
    if not pre:
        pre_key = (1, 0)
    elif pre.startswith("rc"):
        pre_key = (0, 3)
    elif pre.startswith("beta"):
        pre_key = (0, 2)
    elif pre.startswith("alpha"):
        pre_key = (0, 1)
    else:
        pre_key = (0, 0)
    return tuple(numbers[:3]) + pre_key


def check_for_updates(config: AppConfig, timeout: int = 8) -> UpdateInfo:
    manifest_url = update_manifest_url(config)
    response = requests.get(manifest_url, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    latest_version = str(data.get("version", "")).strip().lstrip("v")
    if not latest_version:
        raise ValueError("更新文件缺少 version 字段")
    raw_download = str(data.get("download_url") or "").strip()
    raw_page = str(data.get("page_url") or "./").strip()
    download_url = urljoin(manifest_url, raw_download) if raw_download else ""
    page_url = urljoin(manifest_url, raw_page)
    notes = data.get("notes") or []
    if not isinstance(notes, list):
        notes = [str(notes)]
    return UpdateInfo(
        current_version=VERSION,
        latest_version=latest_version,
        update_available=version_key(latest_version) > version_key(VERSION),
        download_url=download_url,
        page_url=page_url,
        manifest_url=manifest_url,
        notes=[str(item) for item in notes],
        published_at=str(data.get("published_at") or ""),
        sha256=str(data.get("sha256") or ""),
    )


def auto_update_supported() -> bool:
    """自动替换 exe 仅在 Windows 打包运行时可用。"""
    return sys.platform == "win32" and bool(getattr(sys, "frozen", False))


def download_update(info: UpdateInfo, target_dir: Path | None = None) -> Path:
    """下载更新并校验 SHA-256，返回本地 exe 路径。"""
    if not info.download_url:
        raise ValueError("更新清单缺少下载地址")
    expected = (info.sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("更新清单缺少有效的 SHA-256，已取消自动安装")
    folder = (target_dir or Path(tempfile.gettempdir()) / "NexHubUpdates").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    raw_name = Path(unquote(urlsplit(info.download_url).path)).name
    filename = raw_name if raw_name.lower().endswith(".exe") else f"NexHub-v{info.latest_version}.exe"
    target = folder / filename
    part = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    try:
        with requests.get(info.download_url, stream=True, timeout=(15, 300)) as response:
            response.raise_for_status()
            with part.open("wb") as handle:
                for chunk in response.iter_content(1024 * 256):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise ValueError(f"更新文件 SHA-256 校验失败：{actual[:12]}…")
        os.replace(part, target)
        return target
    except Exception:
        part.unlink(missing_ok=True)
        raise


def build_updater_script(source: Path, target: Path, script_path: Path) -> Path:
    """生成等待当前进程退出后替换 exe 并重启的批处理。"""
    if source.resolve() == target.resolve():
        raise ValueError("更新文件与当前程序路径相同")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "@echo off\r\n"
        "setlocal\r\n"
        'set "SOURCE=%NEXHUB_UPDATE_SOURCE%"\r\n'
        'set "TARGET=%NEXHUB_UPDATE_TARGET%"\r\n'
        "for /L %%i in (1,1,90) do (\r\n"
        '  copy /Y "%SOURCE%" "%TARGET%" >nul 2>&1\r\n'
        "  if not errorlevel 1 goto launch\r\n"
        '  "%SystemRoot%\\System32\\ping.exe" -n 2 127.0.0.1 >nul\r\n'
        ")\r\n"
        "exit /b 1\r\n"
        ":launch\r\n"
        'del "%SOURCE%" >nul 2>&1\r\n'
        'start "" "%TARGET%"\r\n'
        'del "%~f0" >nul 2>&1\r\n'
    )
    script_path.write_text(content, encoding="ascii", newline="")
    return script_path


def install_update(downloaded: Path, current_exe: Path | None = None) -> Path:
    """启动独立更新器，替换当前 exe 后重新启动客户端。"""
    if not auto_update_supported():
        raise RuntimeError("源码运行模式不支持自动安装，请使用下载页更新")
    source = downloaded.resolve()
    target = (current_exe or Path(sys.executable)).resolve()
    if not source.is_file() or source.suffix.lower() != ".exe":
        raise ValueError("更新文件无效")
    if not target.is_file():
        raise ValueError("找不到当前客户端程序")
    folder = Path(tempfile.gettempdir()) / "NexHubUpdate"
    script = build_updater_script(source, target, folder / "install-update.cmd")
    env = os.environ.copy()
    env["NEXHUB_UPDATE_SOURCE"] = str(source)
    env["NEXHUB_UPDATE_TARGET"] = str(target)
    flags = 0
    if os.name == "nt":
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    subprocess.Popen(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(script)],
        cwd=str(target.parent),
        env=env,
        close_fds=True,
        creationflags=flags,
    )
    return script
