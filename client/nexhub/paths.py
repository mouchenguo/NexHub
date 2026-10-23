"""应用路径与版本信息。

所有可写数据统一放在 ``%APPDATA%\\NexHub``；测试时可通过
``NEXHUB_DATA_DIR`` 环境变量重定向，避免污染真实配置。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "NexHub"
VERSION = "0.1.2"
CLIENT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CLIENT_ROOT.parent


def app_data_dir() -> Path:
    """返回客户端数据目录（自动创建）。"""
    override = os.environ.get("NEXHUB_DATA_DIR")
    if override:
        base = Path(override).expanduser()
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) / APP_NAME if appdata else Path.home() / ".nexhub"
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_path() -> Path:
    return app_data_dir() / "config.json"


def demo_dir() -> Path:
    path = app_data_dir() / "demo"
    path.mkdir(parents=True, exist_ok=True)
    return path


def demo_store_path() -> Path:
    return app_data_dir() / "demo_store.json"


def user_plugins_dir() -> Path:
    """用户自定义插件目录（客户端可写，重启/重新加载后生效）。"""
    path = app_data_dir() / "plugins"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads" / APP_NAME
    return downloads


def resource_path(name: str) -> Path:
    """返回内置资源目录。

    源码运行时是 ``client/nexhub/resources/<name>``；PyInstaller onefile
    运行时从 ``sys._MEIPASS/nexhub/resources/<name>`` 读取。NPC 程序通过
    ``--add-data "nexhub\\resources;nexhub\\resources"`` 一起打包。
    """
    bundle_root = Path(getattr(sys, "_MEIPASS", CLIENT_ROOT))
    candidates = [
        bundle_root / "nexhub" / "resources" / name,
        CLIENT_ROOT / "nexhub" / "resources" / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
