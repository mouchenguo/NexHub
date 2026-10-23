"""读取域名定向加速插件暴露的本地回环代理；不触碰系统代理设置。"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from ..paths import app_data_dir


def active_local_proxy() -> str:
    settings_path = app_data_dir() / "plugin_data" / "domain_proxy" / "settings.json"
    try:
        data = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict) or not data.get("enabled"):
        return ""
    try:
        port = int(data.get("port") or 7890)
    except (TypeError, ValueError):
        return ""
    if not 1 <= port <= 65535:
        return ""
    return f"http://127.0.0.1:{port}"


def local_proxy_session(proxy_url: str | None = None) -> requests.Session:
    """仅给主动选择使用插件代理的主程序请求创建独立 Session。"""
    url = proxy_url or active_local_proxy()
    session = requests.Session()
    if url:
        session.proxies.update({"http": url, "https": url})
    return session
