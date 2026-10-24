"""Clash-Verge内置版插件：规则分流、订阅导入与系统代理接管。"""
from __future__ import annotations

import base64
import copy
import concurrent.futures
import json
import logging
import os
import random
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

import requests

try:  # PyYAML 随客户端运行环境提供；旧客户端如果没有则退化为 URI 订阅
    import yaml
except ImportError:  # pragma: no cover - 仅在精简运行时触发
    yaml = None

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ProgressBar,
    SegmentedWidget,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TitleLabel,
)

from nexhub.file_utils import human_size
from nexhub.widgets.common import make_button, make_card, section_header
from nexhub.workers import FunctionWorker

LOGGER = logging.getLogger("nexhub.plugin.domain_proxy")
DEFAULT_PORT = 7890
PROXY_NAME = "NEXHUB-PROXY"
MODE_LABELS = {"rule": "规则", "global": "全局", "custom": "自定义"}
SUPPORTED_RULE_TYPES = {
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "GEOIP",
    "IP-CIDR",
    "IP-CIDR6",
    "PROCESS-NAME",
    "MATCH",
}
DEFAULT_RULES = [
    ("DOMAIN-SUFFIX", "google.com"),
    ("DOMAIN-SUFFIX", "googleapis.com"),
    ("DOMAIN-SUFFIX", "gstatic.com"),
    ("DOMAIN-SUFFIX", "youtube.com"),
    ("DOMAIN-SUFFIX", "ytimg.com"),
    ("DOMAIN-SUFFIX", "googlevideo.com"),
    ("DOMAIN-SUFFIX", "github.com"),
    ("DOMAIN-SUFFIX", "githubusercontent.com"),
    ("DOMAIN-SUFFIX", "github.io"),
    ("DOMAIN-SUFFIX", "twitter.com"),
    ("DOMAIN-SUFFIX", "x.com"),
    ("DOMAIN-SUFFIX", "telegram.org"),
    ("DOMAIN-SUFFIX", "t.me"),
    ("DOMAIN-SUFFIX", "openai.com"),
    ("DOMAIN-SUFFIX", "chatgpt.com"),
    ("DOMAIN-SUFFIX", "anthropic.com"),
    ("DOMAIN-SUFFIX", "claude.ai"),
    ("DOMAIN-SUFFIX", "facebook.com"),
    ("DOMAIN-SUFFIX", "instagram.com"),
    ("DOMAIN-SUFFIX", "whatsapp.com"),
    ("DOMAIN-SUFFIX", "netflix.com"),
    ("DOMAIN-SUFFIX", "nflxvideo.net"),
    ("DOMAIN-SUFFIX", "discord.com"),
    ("DOMAIN-SUFFIX", "discordapp.com"),
    ("DOMAIN-SUFFIX", "reddit.com"),
    ("DOMAIN-SUFFIX", "wikipedia.org"),
    ("DOMAIN-SUFFIX", "medium.com"),
    ("DOMAIN-SUFFIX", "npmjs.com"),
    ("DOMAIN-SUFFIX", "pypi.org"),
    ("DOMAIN-SUFFIX", "docker.com"),
    ("DOMAIN-SUFFIX", "cloudflare.com"),
    ("DOMAIN-SUFFIX", "cloudflare-dns.com"),
    ("DOMAIN-SUFFIX", "huggingface.co"),
    ("DOMAIN-SUFFIX", "notion.so"),
    ("DOMAIN-SUFFIX", "figma.com"),
    ("DOMAIN-SUFFIX", "spotify.com"),
    ("DOMAIN-SUFFIX", "twitch.tv"),
    ("DOMAIN-SUFFIX", "v2ex.com"),
    ("DOMAIN-SUFFIX", "npmjs.org"),
    ("DOMAIN-SUFFIX", "microsoft.com"),
    ("DOMAIN-SUFFIX", "windowsupdate.com"),
    ("DOMAIN-SUFFIX", "apple.com"),
    ("DOMAIN-SUFFIX", "icloud.com"),
    ("DOMAIN-SUFFIX", "steampowered.com"),
    ("DOMAIN-SUFFIX", "epicgames.com"),
]
DEFAULT_RULE_ITEMS = [
    {"type": kind, "value": value, "action": "PROXY", "enabled": True}
    for kind, value in DEFAULT_RULES
] + [
    {"type": "GEOIP", "value": "CN", "action": "DIRECT", "enabled": True},
    {"type": "MATCH", "value": "", "action": "PROXY", "enabled": True},
]
TEST_URL = "http://www.gstatic.com/generate_204"
TEST_TIMEOUT_MS = 10000
TEST_CONCURRENCY = 10

RULE_TYPES = [
    "DOMAIN",
    "DOMAIN-SUFFIX",
    "DOMAIN-KEYWORD",
    "GEOIP",
    "IP-CIDR",
    "IP-CIDR6",
    "PROCESS-NAME",
    "MATCH",
]
RULE_ACTIONS = ["PROXY", "DIRECT", "REJECT"]


def latency_display(value) -> tuple[str, QColor]:
    if value is None or value == "":
        return "未测", QColor("#8b949e")
    try:
        latency = int(value)
    except (TypeError, ValueError):
        return "未测", QColor("#8b949e")
    if latency < 0:
        return "超时", QColor("#f85149")
    if latency < 250:
        return f"{latency} ms", QColor("#3fb950")
    if latency < 400:
        return f"{latency} ms", QColor("#58a6ff")
    return f"{latency} ms", QColor("#d29922")


SYSTEM_PROXY_KEY = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"


def _read_windows_proxy() -> dict:
    """读取当前 Windows 系统代理设置；非 Windows 返回空字典。"""
    if os.name != "nt":
        return {}
    import winreg

    values = {"enable": 0, "server": "", "override": "", "auto_config": ""}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SYSTEM_PROXY_KEY) as key:
            for name, field in (
                ("ProxyEnable", "enable"),
                ("ProxyServer", "server"),
                ("ProxyOverride", "override"),
                ("AutoConfigURL", "auto_config"),
            ):
                try:
                    values[field] = winreg.QueryValueEx(key, name)[0]
                except FileNotFoundError:
                    pass
    except OSError:
        return {}
    try:
        values["enable"] = int(values.get("enable") or 0)
    except (TypeError, ValueError):
        values["enable"] = 0
    return values


def _write_windows_proxy(enable: bool, server: str = "", auto_config: str = "") -> None:
    """写入 Windows 系统代理；ProxyOverride 保持不变。"""
    if os.name != "nt":
        return
    import ctypes
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, SYSTEM_PROXY_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
        winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, server)
        winreg.SetValueEx(key, "AutoConfigURL", 0, winreg.REG_SZ, auto_config)
    # 通知 WinINet / Chromium / Edge 立即重新读取代理设置。
    internet_option_settings_changed = 39
    internet_option_refresh = 37
    for option in (internet_option_settings_changed, internet_option_refresh):
        try:
            ctypes.windll.wininet.InternetSetOptionW(0, option, 0, 0)
        except (AttributeError, OSError):
            pass


def _windows_proxy_enabled() -> tuple[bool, str]:
    values = _read_windows_proxy()
    if not values or int(values.get("enable") or 0) != 1:
        return False, ""
    return True, str(values.get("server") or values.get("auto_config") or "").strip()


def clash_verge_conflict() -> str:
    """检测会抢占系统流量的 Clash Verge 代理。

    NexHub 现在由单一“系统代理”开关控制：开关打开时使用 7890 接管
    Windows 代理，关闭/退出时恢复。如果 Clash Verge 已经接管系统代理，
    继续启动 NexHub 会导致流量链路互相干扰。
    """
    enabled, server = _windows_proxy_enabled()
    if enabled:
        suffix = f"（{server}）" if server else ""
        return f"检测到 Windows 系统代理已开启{suffix}，请先关闭 Clash Verge 系统代理"
    return ""


def clash_verge_port_open() -> bool:
    """仅用于提示 Clash 内核仍在运行，不单独阻止 NexHub。"""
    try:
        with socket.create_connection(("127.0.0.1", 7897), timeout=0.25):
            return True
    except OSError:
        return False


def _b64_text(value: str) -> str:
    text = "".join(value.strip().split())
    text += "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return value


def _name_from_fragment(fragment: str, fallback: str) -> str:
    return unquote(fragment).strip() or fallback


def _parse_vmess(uri: str, index: int) -> dict | None:
    payload = _b64_text(uri[8:])
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    server = str(data.get("add") or "").strip()
    port = int(data.get("port") or 0)
    uuid = str(data.get("id") or "").strip()
    if not server or not port or not uuid:
        return None
    name = str(data.get("ps") or f"vmess-{index}")
    node = {
        "name": name,
        "type": "vmess",
        "server": server,
        "port": port,
        "uuid": uuid,
        "alterId": int(data.get("aid") or 0),
        "cipher": "auto",
        "udp": True,
    }
    network = str(data.get("net") or "").strip()
    if network:
        node["network"] = network
    if str(data.get("tls") or "").lower() in ("tls", "true", "1"):
        node["tls"] = True
        if data.get("sni"):
            node["servername"] = str(data["sni"])
    return node


def _parse_trojan(uri: str, index: int) -> dict | None:
    parsed = urlsplit(uri)
    if not parsed.hostname or not parsed.port:
        return None
    query = parse_qs(parsed.query)
    name = _name_from_fragment(parsed.fragment, f"trojan-{index}")
    return {
        "name": name,
        "type": "trojan",
        "server": parsed.hostname,
        "port": int(parsed.port),
        "password": unquote(parsed.username or ""),
        "sni": (query.get("sni") or [parsed.hostname])[0],
        "skip-cert-verify": True,
        "udp": True,
    }


def _parse_ss(uri: str, index: int) -> dict | None:
    raw = uri[5:]
    fragment = ""
    if "#" in raw:
        raw, fragment = raw.split("#", 1)
    raw = raw.split("?", 1)[0]
    if "@" not in raw:
        raw = _b64_text(raw)
    if "@" not in raw:
        return None
    credentials, address = raw.rsplit("@", 1)
    if ":" not in credentials or ":" not in address:
        return None
    method, password = credentials.split(":", 1)
    host, port_text = address.rsplit(":", 1)
    if not port_text.isdigit():
        return None
    return {
        "name": _name_from_fragment(fragment, f"ss-{index}"),
        "type": "ss",
        "server": host.strip("[]"),
        "port": int(port_text),
        "cipher": unquote(method),
        "password": unquote(password),
        "udp": True,
    }


def _parse_vless(uri: str, index: int) -> dict | None:
    parsed = urlsplit(uri)
    if not parsed.hostname or not parsed.port or not parsed.username:
        return None
    query = parse_qs(parsed.query)
    node = {
        "name": _name_from_fragment(parsed.fragment, f"vless-{index}"),
        "type": "vless",
        "server": parsed.hostname,
        "port": int(parsed.port),
        "uuid": unquote(parsed.username),
        "udp": True,
    }
    security = (query.get("security") or [""])[0]
    if security in ("tls", "reality"):
        node["tls"] = True
        node["servername"] = (query.get("sni") or [parsed.hostname])[0]
    if security == "reality":
        node["reality-opts"] = {
            "public-key": (query.get("pbk") or [""])[0],
            "short-id": (query.get("sid") or [""])[0],
        }
    network = (query.get("type") or [""])[0]
    if network:
        node["network"] = network
    return node


CLASH_GROUP_TYPES = {
    "select",
    "url-test",
    "fallback",
    "load-balance",
    "relay",
    "direct",
    "reject",
}


def _clash_document(text: str) -> dict:
    if yaml is None:
        return {}
    try:
        payload = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - 非 YAML 内容交给 URI 解析器
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_clash_yaml(text: str) -> list[dict]:
    """解析 Clash/Mihomo 订阅 YAML 中的 proxies。

    很多机场返回的是 YAML（可能包含 hysteria2/anytls），而不是
    vmess:// 这类逐行 URI。这里直接把代理字典交给 mihomo 配置生成器。
    """
    payload = _clash_document(text)
    if not payload:
        return []
    proxies = payload.get("proxies")
    if not isinstance(proxies, list):
        return []
    nodes: list[dict] = []
    for index, raw in enumerate(proxies, start=1):
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("type") or "").strip().lower()
        if not kind or kind in CLASH_GROUP_TYPES:
            continue
        item = {
            key: value
            for key, value in raw.items()
            if key not in ("latency", "dialer-proxy")
        }
        if not item.get("server"):
            continue
        item["type"] = kind
        item["name"] = str(item.get("name") or f"node-{index}")
        nodes.append(item)
    return nodes


def _parse_clash_rules(text: str) -> list[dict]:
    """把订阅 YAML 的 rules 转成插件可执行的规则。

    机场规则经常包含 RULE-SET / GEOSITE 等需要额外资源文件的类型；
    插件没有这些资源，只保留 mihomo 能直接执行的域名/IP/进程规则。
    """
    payload = _clash_document(text)
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list):
        return []
    rules: list[dict] = []
    for raw in raw_rules:
        line = str(raw or "").strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        kind = parts[0].upper()
        if kind not in SUPPORTED_RULE_TYPES:
            continue
        if kind == "MATCH":
            value, action = "", parts[1] if len(parts) > 1 else "DIRECT"
        else:
            if len(parts) < 3:
                continue
            value, action = parts[1], parts[2]
        action = _rule_action(action)
        rules.append({"type": kind, "value": value, "action": action, "enabled": True})
    return rules


def _clash_verge_rule_candidates() -> list[Path]:
    """Clash Verge 的生成配置和 profile 目录；按最近修改排序。"""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return []
    root = Path(appdata) / "io.github.clash-verge-rev.clash-verge-rev"
    candidates: list[Path] = []
    for path in (root / "clash-verge.yaml", root / "config.yaml"):
        if path.is_file():
            candidates.append(path)
    profiles = root / "profiles"
    if profiles.is_dir():
        candidates.extend(
            path
            for path in profiles.glob("*.yaml")
            if path.is_file() and path.stat().st_size > 1024
        )
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def load_clash_verge_rules() -> list[dict]:
    """从本机 Clash Verge 配置读取完整规则列表。"""
    best: list[dict] = []
    for path in _clash_verge_rule_candidates():
        try:
            rules = _parse_clash_rules(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if len(rules) > len(best):
            best = rules
    return best



    """解析 Clash 订阅常见的 Subscription-Userinfo 响应头。"""
    raw = str(
        headers.get("Subscription-Userinfo")
        or headers.get("subscription-userinfo")
        or ""
    ).strip()
    fields: dict[str, int] = {}
    for part in raw.replace(",", ";").split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        try:
            fields[key.strip().lower()] = int(float(value.strip()))
        except ValueError:
            continue
    total = max(int(fields.get("total") or 0), 0)
    upload = max(int(fields.get("upload") or 0), 0)
    download = max(int(fields.get("download") or 0), 0)
    expire = max(int(fields.get("expire") or 0), 0)
    remaining = max(total - upload - download, 0) if total else 0
    days = max((expire - int(time.time())) // 86400, 0) if expire else None
    return {
        "upload": upload,
        "download": download,
        "total": total,
        "expire": expire,
        "remaining": remaining,
        "days": days,
        "updated_at": int(time.time()),
    }


def subscription_text(subscription: dict | None) -> str:
    if not isinstance(subscription, dict):
        return "订阅流量：未获取"
    total = int(subscription.get("total") or 0)
    if total <= 0:
        return "订阅流量：未获取"
    upload = int(subscription.get("upload") or 0)
    download = int(subscription.get("download") or 0)
    remaining = int(subscription.get("remaining") or max(total - upload - download, 0))
    text = f"剩余流量 {human_size(remaining)} / {human_size(total)}"
    days = subscription.get("days")
    if days is not None:
        text += f" · 剩余 {int(days)} 天"
    return text


def parse_subscription_payload(text: str) -> list[dict]:
    raw = (text or "").strip()
    decoded = _b64_text(raw) if "://" not in raw else raw
    yaml_nodes = _parse_clash_yaml(decoded)
    if yaml_nodes:
        return yaml_nodes
    if "://" not in decoded:
        return []
    nodes: list[dict] = []
    for index, line in enumerate(decoded.splitlines(), start=1):
        line = line.strip()
        if line.startswith("vmess://"):
            node = _parse_vmess(line, index)
        elif line.startswith("trojan://"):
            node = _parse_trojan(line, index)
        elif line.startswith("ss://"):
            node = _parse_ss(line, index)
        elif line.startswith("vless://"):
            node = _parse_vless(line, index)
        else:
            node = None
        if node:
            nodes.append(node)
    return nodes


def _yaml_scalar(value) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_lines(value, indent: int = 0) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, dict):
                lines.append(f"{pad}{key}:")
                lines.extend(_yaml_lines(item, indent + 2))
            elif isinstance(item, list):
                if not item:
                    lines.append(f"{pad}{key}: []")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(_yaml_lines(item, indent + 2))
            else:
                lines.append(f"{pad}{key}: {_yaml_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                sub = _yaml_lines(item, indent + 2)
                if sub:
                    lines.append(f"{pad}- {sub[0].lstrip()}")
                    lines.extend(sub[1:])
                else:
                    lines.append(f"{pad}- {{}}")
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    return lines


def _rule_action(value) -> str:
    action = str(value or "PROXY").strip().upper()
    if action in ("DIRECT", "REJECT", "REJECT-DROP"):
        return action
    return "PROXY"


def _rule_line(item: dict) -> str:
    kind = str(item.get("type") or "DOMAIN-SUFFIX").strip().upper()
    if kind not in SUPPORTED_RULE_TYPES:
        return ""
    value = str(item.get("value") or item.get("domain") or "").strip()
    action = _rule_action(item.get("action"))
    if kind == "MATCH":
        return f"MATCH,{action}"
    if not value:
        return ""
    return f"{kind},{value},{action}"


def build_mihomo_config(data: dict) -> str:
    nodes = data.get("nodes") or []
    selected = data.get("selected") or (nodes[0]["name"] if nodes else "")
    whitelist = [item for item in data.get("whitelist") or [] if item.get("enabled", True)]
    mode = str(data.get("mode") or "rule").strip().lower()
    config: dict = {
        "mixed-port": int(data.get("port") or DEFAULT_PORT),
        "bind-address": "127.0.0.1",
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "tun": {"enable": False},
        "external-controller": "127.0.0.1:7899",
    }
    dns = data.get("dns")
    if isinstance(dns, dict) and dns:
        dns_config = copy.deepcopy(dns)
        # 与 Clash Verge 共存时不能占用同一个 DNS 监听端口。
        dns_config.pop("listen", None)
        # 订阅里的 fallback-filter.geoip 会要求 mihomo 下载 MMDB；
        # 插件目录没有随包提供该文件，下载失败会直接导致内核启动失败。
        dns_config.pop("fallback-filter", None)
        dns_config["enable"] = True
        config["dns"] = dns_config
    else:
        config["dns"] = {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "fake-ip-range": "198.18.0.1/16",
            "default-nameserver": ["system", "223.5.5.5", "119.29.29.29"],
            "nameserver": [
                "https://223.5.5.5/dns-query",
                "https://doh.pub/dns-query",
                "https://dns.alidns.com/dns-query",
            ],
            "fallback": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
            ],
        }
    proxies = []
    names = []
    for node in nodes:
        item = {key: value for key, value in node.items() if key != "latency"}
        proxies.append(item)
        names.append(str(item.get("name") or ""))
    config["proxies"] = proxies
    if proxies:
        selected = selected if selected in names else names[0]
        ordered = [selected] + [name for name in names if name != selected]
        config["proxy-groups"] = [
            {"name": PROXY_NAME, "type": "select", "proxies": ordered},
        ]
        rules: list[str] = []
        if mode == "global":
            rules.append(f"MATCH,{PROXY_NAME}")
        elif mode == "custom":
            for item in whitelist:
                kind = str(item.get("type") or "DOMAIN-SUFFIX").upper()
                domain = str(item.get("domain") or "").strip()
                if kind in ("DOMAIN", "DOMAIN-SUFFIX") and domain:
                    rules.append(f"{kind},{domain},{PROXY_NAME}")
            rules.append("MATCH,DIRECT")
        else:
            for item in data.get("rules") or []:
                if not item.get("enabled", True):
                    continue
                line = _rule_line(item)
                if line and line != "MATCH,DIRECT":
                    rules.append(line.replace(",PROXY", f",{PROXY_NAME}"))
                elif line == "MATCH,DIRECT":
                    rules.append(line)
            if not any(line.startswith("MATCH,") for line in rules):
                rules.append("MATCH,DIRECT")
        config["rules"] = rules or ["MATCH,DIRECT"]
    else:
        config["rules"] = ["MATCH,DIRECT"]
    return "\n".join(_yaml_lines(config)) + "\n"


class NodeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        from qfluentwidgets import FluentStyleSheet

        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("添加上游节点")
        self.setFixedWidth(430)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("添加上游节点"))
        self.type_combo = ComboBox()
        self.type_combo.addItems(["vmess", "vless", "trojan", "ss"])
        self.name_edit = LineEdit()
        self.name_edit.setPlaceholderText("节点名称")
        self.server_edit = LineEdit()
        self.server_edit.setPlaceholderText("服务器地址")
        self.port_spin = SpinBox()
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(443)
        self.secret_edit = LineEdit()
        self.secret_edit.setPlaceholderText("密码或 UUID")
        self.method_edit = LineEdit()
        self.method_edit.setPlaceholderText("加密方式（ss 可选）")
        for widget in (
            self.type_combo,
            self.name_edit,
            self.server_edit,
            self.port_spin,
            self.secret_edit,
            self.method_edit,
        ):
            layout.addWidget(widget)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(make_button("添加", primary=True, on_click=self._accept))
        layout.addLayout(footer)

    def _accept(self) -> None:
        server = self.server_edit.text().strip()
        name = self.name_edit.text().strip()
        if not server or not name:
            InfoBar.warning("信息不完整", "请填写节点名称和服务器地址", parent=self, position=InfoBarPosition.TOP)
            return
        kind = self.type_combo.currentText()
        node = {
            "name": name,
            "type": kind,
            "server": server,
            "port": self.port_spin.value(),
            "udp": True,
        }
        secret = self.secret_edit.text().strip()
        if kind in ("vmess", "vless"):
            node["uuid"] = secret
        else:
            node["password"] = secret
        if kind == "ss":
            node["cipher"] = self.method_edit.text().strip() or "aes-128-gcm"
        if kind in ("trojan", "vless"):
            node["sni"] = server
        self.node = node
        self.accept()



class ProxyWindow(QWidget):
    def __init__(self, context, parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self.settings_path = context.data_dir / "settings.json"
        self.config_path = context.data_dir / "config.yaml"
        self.log_path = context.data_dir / "mihomo.log"
        self.data = self._load_data()
        self._restore_stale_takeover()
        self._proc: subprocess.Popen | None = None
        self._log_handle = None
        self._worker: FunctionWorker | None = None
        self._restart_count = 0
        self._monitor = QTimer(self)
        self._monitor.setInterval(2000)
        self._monitor.timeout.connect(self._watch_process)
        self._build()
        self._load_tables()
        self._update_preview()
        self.context.on_cleanup(self._stop_service)
        if self.data.get("enabled"):
            QTimer.singleShot(500, self._start_service)

    def _load_data(self) -> dict:
        default = {
            "enabled": False,
            "port": DEFAULT_PORT,
            "mode": "rule",
            "rules_version": 2,
            "rules_source": "builtin",
            "whitelist": [],
            "rules": copy.deepcopy(DEFAULT_RULE_ITEMS),
            "nodes": [],
            "selected": "",
            "subscription": {},
            "dns": {},
        }
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                old_default = [
                    {"type": "GEOIP", "value": "CN", "action": "DIRECT", "enabled": True},
                    {"type": "MATCH", "value": "", "action": "DIRECT", "enabled": True},
                ]
                if "rules_version" not in loaded and loaded.get("rules") == old_default:
                    loaded.pop("rules", None)
                default.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
        current_rules = default.get("rules") or []
        source = str(default.get("rules_source") or "builtin")
        if source != "subscription" and len(current_rules) <= len(DEFAULT_RULE_ITEMS):
            clash_rules = load_clash_verge_rules()
            if len(clash_rules) > len(current_rules):
                default["rules"] = clash_rules
                default["rules_source"] = "clash_verge"
        return default

    def _save_data(self) -> None:
        self.settings_path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        status_card, status_layout = make_card(20, 12)
        header, actions = section_header("Clash-Verge内置版", "真正的规则分流 · 节点选择和测速都在当前窗口完成")
        self.status_chip = CaptionLabel("已停止")
        self.status_chip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_chip.setFixedHeight(24)
        self.status_chip.setMaximumWidth(110)
        self.status_chip.setStyleSheet(
            "CaptionLabel { color: #f85149; background-color: rgba(248, 81, 73, .10); "
            "border: 1px solid rgba(248, 81, 73, .25); border-radius: 12px; padding: 2px 10px; }"
        )
        actions.addWidget(self.status_chip)
        status_layout.addWidget(header)
        switch_row = QHBoxLayout()
        self.switch = SwitchButton()
        self.switch.setChecked(bool(self.data.get("enabled")))
        self.switch.checkedChanged.connect(self._toggle_service)
        switch_row.addWidget(BodyLabel("系统代理"))
        switch_row.addWidget(self.switch)
        switch_row.addStretch(1)
        switch_row.addWidget(CaptionLabel(f"本地端口 127.0.0.1:{self.data.get('port') or DEFAULT_PORT}"))
        status_layout.addLayout(switch_row)
        mode_row = QHBoxLayout()
        mode_row.addWidget(BodyLabel("代理模式"))
        self.mode_segment = SegmentedWidget()
        for value in ("rule", "global", "custom"):
            self.mode_segment.addItem(value, MODE_LABELS[value])
        self.mode_segment.setCurrentItem(str(self.data.get("mode") or "rule"))
        self.mode_segment.currentItemChanged.connect(self._change_mode)
        mode_row.addWidget(self.mode_segment)
        mode_row.addStretch(1)
        status_layout.addLayout(mode_row)
        self.service_hint = CaptionLabel("")
        self.service_hint.setWordWrap(True)
        status_layout.addWidget(self.service_hint)
        self.subscription_label = CaptionLabel(subscription_text(self.data.get("subscription")))
        self.subscription_label.setWordWrap(True)
        status_layout.addWidget(self.subscription_label)
        self.clash_hint = CaptionLabel("")
        self.clash_hint.setWordWrap(True)
        status_layout.addWidget(self.clash_hint)
        layout.addWidget(status_card)

        whitelist_card, whitelist_layout = make_card(20, 10)
        white_header, white_actions = section_header("自定义网站", "只填网站域名，自动按 DOMAIN-SUFFIX 走代理")
        white_actions.addWidget(make_button("添加网站", primary=True, icon=FIF.ADD, on_click=self._add_whitelist))
        white_actions.addWidget(make_button("编辑选中", transparent=True, icon=FIF.EDIT, on_click=self._edit_whitelist))
        white_actions.addWidget(make_button("删除选中", transparent=True, icon=FIF.DELETE, on_click=self._delete_whitelist))
        whitelist_layout.addWidget(white_header)
        self.whitelist_table = TableWidget()
        self.whitelist_table.setBorderVisible(False)
        self.whitelist_table.setBorderRadius(10)
        self.whitelist_table.setColumnCount(3)
        self.whitelist_table.setHorizontalHeaderLabels(["匹配", "网站", "启用"])
        self.whitelist_table.verticalHeader().setVisible(False)
        self.whitelist_table.setShowGrid(False)
        self.whitelist_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.whitelist_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.whitelist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.whitelist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.whitelist_table.setMinimumHeight(160)
        whitelist_layout.addWidget(self.whitelist_table)
        self.whitelist_card = whitelist_card
        layout.addWidget(whitelist_card)

        rule_card, rule_layout = make_card(20, 10)
        rule_header, rule_actions = section_header(
            "规则模式", "参考 Clash 规则：DOMAIN / GEOIP / IP-CIDR 等，按顺序匹配"
        )
        rule_actions.addWidget(make_button("添加网站", primary=True, icon=FIF.ADD, on_click=self._add_site_rule))
        rule_actions.addWidget(make_button("同步 Clash-Verge", transparent=True, icon=FIF.SYNC, on_click=self._sync_clash_rules))
        rule_actions.addWidget(make_button("高级规则", transparent=True, icon=FIF.EDIT, on_click=self._add_rule))
        rule_actions.addWidget(make_button("编辑选中", transparent=True, icon=FIF.EDIT, on_click=self._edit_rule))
        rule_actions.addWidget(make_button("删除选中", transparent=True, icon=FIF.DELETE, on_click=self._delete_rule))
        rule_layout.addWidget(rule_header)
        self.rules_info = CaptionLabel("")
        self.rules_info.setWordWrap(True)
        rule_layout.addWidget(self.rules_info)
        self.rules_table = TableWidget()
        self.rules_table.setBorderVisible(False)
        self.rules_table.setBorderRadius(10)
        self.rules_table.setColumnCount(4)
        self.rules_table.setHorizontalHeaderLabels(["类型", "内容", "动作", "启用"])
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setShowGrid(False)
        self.rules_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.rules_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.rules_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.rules_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.rules_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.rules_table.setMinimumHeight(180)
        rule_layout.addWidget(self.rules_table)
        self.rule_card = rule_card
        layout.addWidget(rule_card)

        upstream_card, upstream_layout = make_card(20, 10)
        up_header, up_actions = section_header("节点列表", "点击节点切换；测速两次取最小值，结果按绿 / 蓝 / 黄 / 红显示")
        up_actions.addWidget(make_button("添加节点", icon=FIF.ADD, on_click=self._add_node))
        self.test_button = make_button("测速", icon=FIF.SYNC, on_click=self._test_nodes)
        up_actions.addWidget(self.test_button)
        upstream_layout.addWidget(up_header)
        sub_row = QHBoxLayout()
        self.subscription_edit = LineEdit()
        self.subscription_edit.setPlaceholderText("粘贴 Clash / Mihomo 订阅链接")
        sub_row.addWidget(self.subscription_edit, 1)
        sub_row.addWidget(make_button("解析导入", primary=True, on_click=self._import_subscription))
        upstream_layout.addLayout(sub_row)
        self.current_node_label = CaptionLabel("当前节点：未选择")
        self.current_node_label.setWordWrap(True)
        upstream_layout.addWidget(self.current_node_label)
        self.nodes_table = TableWidget()
        self.nodes_table.setBorderVisible(False)
        self.nodes_table.setBorderRadius(10)
        self.nodes_table.setColumnCount(3)
        self.nodes_table.setHorizontalHeaderLabels(["节点", "协议", "延迟"])
        self.nodes_table.verticalHeader().setVisible(False)
        self.nodes_table.setShowGrid(False)
        self.nodes_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.nodes_table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.nodes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.nodes_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.nodes_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.nodes_table.setMinimumHeight(280)
        self.nodes_table.itemSelectionChanged.connect(self._select_node)
        upstream_layout.addWidget(self.nodes_table)
        node_actions = QHBoxLayout()
        node_actions.addStretch(1)
        node_actions.addWidget(make_button("删除选中节点", transparent=True, icon=FIF.DELETE, on_click=self._delete_node))
        upstream_layout.addLayout(node_actions)
        layout.addWidget(upstream_card)
        layout.addStretch(1)
        self._sync_mode_ui()
        self._set_status("已停止", False)
        self._refresh_clash_hint()

    def _set_status(self, text: str, running: bool) -> None:
        self.status_chip.setText(text)
        color = "#3fb950" if running else "#f85149"
        background = "rgba(63, 185, 80, .10)" if running else "rgba(248, 81, 73, .10)"
        border = "rgba(63, 185, 80, .25)" if running else "rgba(248, 81, 73, .25)"
        self.status_chip.setStyleSheet(
            f"CaptionLabel {{ color: {color}; background-color: {background}; "
            f"border: 1px solid {border}; border-radius: 12px; padding: 2px 10px; }}"
        )

    def _sync_mode_ui(self) -> None:
        mode = str(self.data.get("mode") or "rule")
        if hasattr(self, "whitelist_card"):
            self.whitelist_card.setVisible(mode == "custom")
        if hasattr(self, "rule_card"):
            self.rule_card.setVisible(mode == "rule")
        if hasattr(self, "mode_segment"):
            if self.mode_segment.currentRouteKey() != mode:
                self.mode_segment.blockSignals(True)
                self.mode_segment.setCurrentItem(mode)
                self.mode_segment.blockSignals(False)

    def _change_mode(self, mode: str) -> None:
        if mode not in MODE_LABELS or mode == self.data.get("mode"):
            self._sync_mode_ui()
            return
        was_running = bool(self._proc and self._proc.poll() is None)
        self.data["mode"] = mode
        self._save_data()
        self._sync_mode_ui()
        self._update_preview()
        if was_running:
            self._stop_service(update_switch=False)
            self._start_service()

    def _update_subscription_label(self) -> None:
        if hasattr(self, "subscription_label"):
            self.subscription_label.setText(subscription_text(self.data.get("subscription")))

    def _refresh_clash_hint(self) -> None:
        if not hasattr(self, "clash_hint"):
            return
        current = _read_windows_proxy()
        own_proxy = (
            bool(self.data.get("system_proxy_active"))
            and int(current.get("enable") or 0) == 1
            and str(current.get("server") or "").strip() == self._proxy_server()
        )
        conflict = clash_verge_conflict()
        if own_proxy:
            self.clash_hint.setText(
                f"NexHub 已接管 Windows 系统代理（{self._proxy_server()}）；"
                "关闭代理或退出 NexHub 会恢复原设置。"
            )
            self.clash_hint.setStyleSheet("color: #3fb950;")
        elif conflict:
            self.clash_hint.setText(f"⚠ {conflict}")
            self.clash_hint.setStyleSheet("color: #f85149;")
        elif clash_verge_port_open():
            self.clash_hint.setText(
                "检测到 Clash Verge 内核仍在运行；系统代理未开启时不阻止 NexHub，"
                "请把目标软件代理指向 127.0.0.1:7890。"
            )
            self.clash_hint.setStyleSheet("color: #d29922;")
        else:
            self.clash_hint.setText(
                "未检测到 Clash Verge 系统代理；打开开关后 NexHub 会接管系统代理。"
            )
            self.clash_hint.setStyleSheet("")

    def _proxy_server(self) -> str:
        return f"127.0.0.1:{int(self.data.get('port') or DEFAULT_PORT)}"

    def _own_proxy_enabled(self) -> bool:
        current = _read_windows_proxy()
        return (
            bool(self.data.get("system_proxy_active"))
            and int(current.get("enable") or 0) == 1
            and str(current.get("server") or "").strip() == self._proxy_server()
        )

    def _restore_stale_takeover(self) -> None:
        """上次进程被强制结束时，恢复被 NexHub 接管的系统代理。"""
        if os.name != "nt" or not self.data.get("system_proxy_active"):
            return
        current = _read_windows_proxy()
        address = str(self.data.get("system_proxy_address") or self._proxy_server())
        if int(current.get("enable") or 0) == 1 and str(current.get("server") or "").strip() == address:
            backup = self.data.get("system_proxy_backup") or {}
            try:
                _write_windows_proxy(
                    bool(backup.get("enable")),
                    str(backup.get("server") or ""),
                    str(backup.get("auto_config") or ""),
                )
            except OSError:
                LOGGER.exception("restore stale system proxy failed")
        self.data["system_proxy_active"] = False
        self.data.pop("system_proxy_backup", None)
        self.data.pop("system_proxy_address", None)
        self._save_data()

    def _apply_takeover(self) -> bool:
        if os.name != "nt":
            return True
        current = _read_windows_proxy()
        if not current:
            return False
        address = self._proxy_server()
        if int(current.get("enable") or 0) == 1:
            existing = str(current.get("server") or "").strip()
            auto_config = str(current.get("auto_config") or "").strip()
            if not (existing == address or (not existing and not auto_config)):
                return False
        if not self.data.get("system_proxy_active"):
            self.data["system_proxy_backup"] = {
                "enable": int(current.get("enable") or 0),
                "server": str(current.get("server") or ""),
                "auto_config": str(current.get("auto_config") or ""),
            }
        self.data["system_proxy_active"] = True
        self.data["system_proxy_address"] = address
        self._save_data()
        try:
            _write_windows_proxy(True, address, "")
        except OSError:
            LOGGER.exception("set system proxy failed")
            self.data["system_proxy_active"] = False
            self.data.pop("system_proxy_backup", None)
            self.data.pop("system_proxy_address", None)
            self._save_data()
            return False
        return True

    def _restore_takeover(self) -> None:
        if not self.data.get("system_proxy_active"):
            return
        address = str(self.data.get("system_proxy_address") or self._proxy_server())
        current = _read_windows_proxy()
        if int(current.get("enable") or 0) == 1 and str(current.get("server") or "").strip() == address:
            backup = self.data.get("system_proxy_backup") or {}
            try:
                _write_windows_proxy(
                    bool(backup.get("enable")),
                    str(backup.get("server") or ""),
                    str(backup.get("auto_config") or ""),
                )
            except OSError:
                LOGGER.exception("restore system proxy failed")
        self.data["system_proxy_active"] = False
        self.data.pop("system_proxy_backup", None)
        self.data.pop("system_proxy_address", None)
        self._save_data()

    def _load_tables(self) -> None:
        self.whitelist_table.setRowCount(0)
        for item in self.data.get("whitelist") or []:
            row = self.whitelist_table.rowCount()
            self.whitelist_table.insertRow(row)
            self.whitelist_table.setItem(row, 0, QTableWidgetItem("后缀" if str(item.get("type") or "DOMAIN-SUFFIX") == "DOMAIN-SUFFIX" else "精确"))
            self.whitelist_table.setItem(row, 1, QTableWidgetItem(str(item.get("domain") or "")))
            check = CheckBox()
            check.setChecked(bool(item.get("enabled", True)))
            check.stateChanged.connect(lambda state, index=row: self._toggle_whitelist(index, state))
            self.whitelist_table.setCellWidget(row, 2, check)
        self.nodes_table.setRowCount(0)
        for node in self.data.get("nodes") or []:
            row = self.nodes_table.rowCount()
            self.nodes_table.insertRow(row)
            values = [node.get("name", ""), node.get("type", ""), node.get("latency", None)]
            for column, value in enumerate(values):
                if column == 2:
                    text, color = latency_display(value)
                    item = QTableWidgetItem(text)
                    item.setForeground(color)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                else:
                    item = QTableWidgetItem(str(value))
                self.nodes_table.setItem(row, column, item)
            self.nodes_table.item(row, 0).setToolTip(
                str(node.get("server") or "") + ":" + str(node.get("port") or "")
            )
        selected = str(self.data.get("selected") or "")
        if hasattr(self, "current_node_label"):
            self.current_node_label.setText(f"当前节点：{selected or '未选择'}")
        for row in range(self.nodes_table.rowCount()):
            if self.nodes_table.item(row, 0).text() == selected:
                self.nodes_table.selectRow(row)
                break
        self.rules_table.setRowCount(0)
        for rule in self.data.get("rules") or []:
            row = self.rules_table.rowCount()
            self.rules_table.insertRow(row)
            self.rules_table.setItem(row, 0, QTableWidgetItem(str(rule.get("type") or "")))
            self.rules_table.setItem(row, 1, QTableWidgetItem(str(rule.get("value") or "")))
            self.rules_table.setItem(row, 2, QTableWidgetItem(str(rule.get("action") or "PROXY")))
            check = CheckBox()
            check.setChecked(bool(rule.get("enabled", True)))
            check.stateChanged.connect(lambda state, index=row: self._toggle_rule(index, state))
            self.rules_table.setCellWidget(row, 3, check)
        if hasattr(self, "rules_info"):
            source = {
                "subscription": "订阅",
                "clash_verge": "Clash Verge",
            }.get(str(self.data.get("rules_source") or "builtin"), "内置")
            self.rules_info.setText(
                f"共 {len(self.data.get('rules') or [])} 条规则 · 来源：{source} · "
                "可在“规则”模式下编辑，或切换“自定义”只填网站域名。"
            )

    def _toggle_whitelist(self, index: int, state: int) -> None:
        rows = self.data.get("whitelist") or []
        if 0 <= index < len(rows):
            rows[index]["enabled"] = bool(state)
            self._save_data()
            self._update_preview()

    def _add_whitelist(self) -> None:
        dialog = WhitelistDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.data.setdefault("whitelist", []).append(dialog.rule)
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _edit_whitelist(self) -> None:
        row = self.whitelist_table.currentRow()
        rows = self.data.get("whitelist") or []
        if not 0 <= row < len(rows):
            return
        dialog = WhitelistDialog(self, rows[row])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dict(rows[row])
        rule.update(dialog.rule)
        rows[row] = rule
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _delete_whitelist(self) -> None:
        row = self.whitelist_table.currentRow()
        rows = self.data.get("whitelist") or []
        if 0 <= row < len(rows):
            rows.pop(row)
            self._save_data()
            self._load_tables()
            self._update_preview()

    def _toggle_rule(self, index: int, state: int) -> None:
        rows = self.data.get("rules") or []
        if 0 <= index < len(rows):
            rows[index]["enabled"] = bool(state)
            self._save_data()
            self._update_preview()

    def _sync_clash_rules(self) -> None:
        rules = load_clash_verge_rules()
        if not rules:
            InfoBar.warning(
                "未找到 Clash Verge 规则",
                "未找到 clash-verge.yaml 或 profiles 中的规则文件。",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.data["rules"] = rules
        self.data["rules_source"] = "clash_verge"
        self._save_data()
        self._load_tables()
        self._update_preview()
        InfoBar.success(
            "规则已同步",
            f"从 Clash Verge 导入 {len(rules)} 条规则",
            parent=self,
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _insert_rule(self, rule: dict) -> None:
        rules = self.data.setdefault("rules", [])
        for index, item in enumerate(rules):
            if str(item.get("type") or "").upper() == "MATCH":
                rules.insert(index, rule)
                return
        rules.append(rule)

    def _add_site_rule(self) -> None:
        dialog = WhitelistDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        domain = str(dialog.rule.get("domain") or "")
        self._insert_rule(
            {"type": "DOMAIN-SUFFIX", "value": domain, "action": "PROXY", "enabled": True}
        )
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _add_rule(self) -> None:
        dialog = RuleDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._insert_rule(dialog.rule)
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _edit_rule(self) -> None:
        row = self.rules_table.currentRow()
        rows = self.data.get("rules") or []
        if not 0 <= row < len(rows):
            return
        dialog = RuleDialog(self, rows[row])
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        rule = dict(rows[row])
        rule.update(dialog.rule)
        rows[row] = rule
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _delete_rule(self) -> None:
        row = self.rules_table.currentRow()
        rows = self.data.get("rules") or []
        if 0 <= row < len(rows):
            rows.pop(row)
            self._save_data()
            self._load_tables()
            self._update_preview()

    def _add_node(self) -> None:
        dialog = NodeDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.data.setdefault("nodes", []).append(dialog.node)
        self.data["selected"] = dialog.node["name"]
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _delete_node(self) -> None:
        row = self.nodes_table.currentRow()
        nodes = self.data.get("nodes") or []
        if 0 <= row < len(nodes):
            removed = nodes.pop(row)
            if self.data.get("selected") == removed.get("name"):
                self.data["selected"] = nodes[0]["name"] if nodes else ""
            self._save_data()
            self._load_tables()
            self._update_preview()


    def _select_node(self) -> None:
        row = self.nodes_table.currentRow()
        nodes = self.data.get("nodes") or []
        if 0 <= row < len(nodes):
            self._apply_selected_node(str(nodes[row].get("name") or ""))

    def _apply_selected_node(self, selected: str) -> bool:
        if not selected:
            return False
        if selected == self.data.get("selected"):
            return True
        was_running = bool(self._proc and self._proc.poll() is None)
        self.data["selected"] = selected
        self._save_data()
        self._load_tables()
        self._update_preview()
        if was_running:
            self._stop_service(update_switch=False)
            self._start_service()
        return True

    def _import_subscription(self) -> None:
        url = self.subscription_edit.text().strip()
        if not url:
            InfoBar.warning("缺少链接", "请先粘贴订阅链接", parent=self, position=InfoBarPosition.TOP)
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self._fetch_subscription_bundle, url, parent=self)
        self._worker.succeeded.connect(self._on_subscription)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    @staticmethod
    def _fetch_subscription_bundle(url: str) -> dict:
        response = requests.get(
            url,
            timeout=20,
            headers={
                # 机场按 UA 返回不同节点集合；mihomo/ClashMeta 才是完整
                # Clash 配置。ClashMeta/1.0 会被部分机场当作旧客户端，
                # 只返回一个简化节点。
                "User-Agent": "mihomo/1.18.0",
                "Accept": "*/*",
            },
        )
        if response.status_code >= 400:
            body = " ".join((response.text or "").split())[:240]
            raise ValueError(
                f"订阅接口返回 HTTP {response.status_code}：{body or '无响应内容'}"
            )
        nodes = parse_subscription_payload(response.text)
        if not nodes:
            raise ValueError(
                "链接中没有解析到支持的节点；请确认它是 Clash/Mihomo 订阅，"
                "而不是客户端 API 或登录页面"
            )
        payload = _clash_document(response.text)
        return {
            "nodes": nodes,
            "subscription": subscription_from_headers(response.headers),
            "dns": payload.get("dns") if isinstance(payload.get("dns"), dict) else {},
            "rules": _parse_clash_rules(response.text),
        }

    @staticmethod
    def _fetch_subscription(url: str) -> list[dict]:
        return ProxyWindow._fetch_subscription_bundle(url)["nodes"]

    def _on_subscription(self, payload: dict | list[dict]) -> None:
        dns = None
        imported_rules: list[dict] = []
        if isinstance(payload, dict):
            nodes = payload.get("nodes") or []
            subscription = payload.get("subscription")
            dns = payload.get("dns")
            imported_rules = payload.get("rules") or []
        else:
            nodes = payload
            subscription = None
        existing = {str(item.get("name") or "") for item in self.data.get("nodes") or []}
        added = []
        for node in nodes:
            if node.get("name") in existing:
                node["name"] = f"{node['name']}-{len(existing) + 1}"
            existing.add(node["name"])
            added.append(node)
        self.data.setdefault("nodes", []).extend(added)
        if not self.data.get("selected") and added:
            self.data["selected"] = added[0]["name"]
        if isinstance(subscription, dict):
            self.data["subscription"] = subscription
        if isinstance(dns, dict) and dns:
            self.data["dns"] = dns
        if imported_rules:
            self.data["rules"] = imported_rules
            self.data["rules_source"] = "subscription"
        self._save_data()
        self._load_tables()
        self._update_subscription_label()
        self._update_preview()
        message = f"已导入 {len(added)} 个节点"
        if imported_rules:
            message += f"、{len(imported_rules)} 条规则"
        InfoBar.success("导入完成", message, parent=self, position=InfoBarPosition.TOP_RIGHT)

    def _test_nodes(self) -> None:
        nodes = self.data.get("nodes") or []
        if not nodes:
            InfoBar.warning("没有节点", "请先导入或添加节点", parent=self, position=InfoBarPosition.TOP)
            return
        if self._worker and self._worker.isRunning():
            return
        if not self._proc or self._proc.poll() is not None:
            self._start_service()
        if not self._proc or self._proc.poll() is not None:
            return
        self.test_button.setEnabled(False)
        self.test_button.setText("测速中...")
        self._worker = FunctionWorker(
            self._test_latencies, list(nodes), "127.0.0.1:7899", parent=self
        )
        self._worker.succeeded.connect(self._on_latencies)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    @staticmethod
    def _test_latencies(nodes: list[dict], controller: str = "127.0.0.1:7899") -> list[dict]:
        """按 Clash Verge 的方式通过 mihomo API 批量测速。

        Clash Verge 默认使用 10 秒超时、最多 10 路并发。这里沿用它的
        批量并发和延迟颜色规则，并对每个节点连续测两次取最小值，
        避免 cp.cloudflare.com/gstatic 单次抖动导致结果偏高。
        """
        time.sleep(0.8)

        def test_one(node: dict) -> dict:
            item = dict(node)
            name = str(item.get("name") or "")
            samples: list[int] = []
            for _ in range(2):
                try:
                    time.sleep(random.random() * 0.2)
                    response = requests.get(
                        f"http://{controller}/proxies/{quote(name, safe='')}/delay",
                        params={"url": TEST_URL, "timeout": TEST_TIMEOUT_MS},
                        timeout=12,
                    )
                    if response.ok:
                        value = response.json().get("delay")
                        if isinstance(value, (int, float)):
                            measured = int(value)
                            if 0 < measured < 100000:
                                samples.append(measured)
                except Exception:  # noqa: BLE001 - 单节点失败不能中断全部测速
                    pass
            item["latency"] = min(samples) if samples else -1
            return item

        workers = min(TEST_CONCURRENCY, max(1, len(nodes)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(test_one, nodes))

    def _on_latencies(self, nodes: list[dict]) -> None:
        if hasattr(self, "test_button"):
            self.test_button.setEnabled(True)
            self.test_button.setText("测速")
        self.data["nodes"] = nodes
        viable = [item for item in nodes if int(item.get("latency") or -1) >= 0]
        if viable:
            self.data["selected"] = min(viable, key=lambda item: int(item["latency"]))["name"]
            InfoBar.success(
                "测速完成",
                f"已选择最低延迟节点：{self.data['selected']}",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
            )
        else:
            InfoBar.warning("测速完成", "所有节点均无法连接", parent=self, position=InfoBarPosition.TOP)
        self._save_data()
        self._load_tables()
        self._update_preview()

    def _on_worker_failed(self, message: str) -> None:
        if hasattr(self, "test_button"):
            self.test_button.setEnabled(True)
            self.test_button.setText("测速")
        InfoBar.error("操作失败", message, parent=self, position=InfoBarPosition.TOP)


    def _update_preview(self) -> None:
        mode = str(self.data.get("mode") or "rule")
        mode_hint = {
            "global": "全局模式：所有流量走代理。",
            "custom": "自定义模式：仅自定义网站走代理，其他流量 DIRECT。",
            "rule": "规则模式：按下方 Clash 规则顺序匹配，未命中时 DIRECT。",
        }.get(mode, "规则模式：按下方 Clash 规则顺序匹配，未命中时 DIRECT。")
        hint = (
            mode_hint
            + f" 开关打开后会接管 Windows 系统代理 {self._proxy_server()}；"
            "关闭开关或退出 NexHub 时自动恢复原系统代理。"
        )
        if self._proc and self._proc.poll() is None:
            hint = "配置已更新；重新启用系统代理后生效。" + hint
        self.service_hint.setText(hint)
        self._refresh_clash_hint()

    def _toggle_service(self, checked: bool) -> None:
        if checked:
            self._start_service()
        else:
            self._stop_service(update_switch=False)

    def _ensure_geodata(self) -> None:
        """把随插件打包的 Country.mmdb 复制到 mihomo 数据目录。"""
        source = self.context.plugin_dir / "Country.mmdb"
        target = self.context.data_dir / "Country.mmdb"
        if not source.is_file():
            return
        try:
            if not target.is_file() or target.stat().st_size != source.stat().st_size:
                shutil.copy2(source, target)
        except OSError:
            LOGGER.exception("copy Country.mmdb failed")

    def _start_service(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        conflict = clash_verge_conflict()
        if conflict and not self._own_proxy_enabled():
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            self.data["enabled"] = False
            self._save_data()
            self._set_status("已停止", False)
            self._refresh_clash_hint()
            InfoBar.error(
                "检测到 Clash Verge 正在代理",
                f"{conflict}。NexHub 不能与 Clash 系统代理同时使用。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        if not self.data.get("nodes"):
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            self.data["enabled"] = False
            self._save_data()
            InfoBar.warning("未配置节点", "请先导入或添加上游节点", parent=self, position=InfoBarPosition.TOP)
            return
        exe = self.context.plugin_dir / "mihomo.exe"
        if not exe.is_file():
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            InfoBar.error("缺少内核", "插件目录中缺少 mihomo.exe", parent=self, position=InfoBarPosition.TOP)
            return
        self._ensure_geodata()
        try:
            self.config_path.write_text(build_mihomo_config(self.data), encoding="utf-8")
            self._log_handle = self.log_path.open("a", encoding="utf-8")
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            self._proc = subprocess.Popen(
                [str(exe), "-f", str(self.config_path), "-d", str(self.context.data_dir)],
                cwd=str(self.context.plugin_dir),
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except Exception as exc:  # noqa: BLE001
            self._proc = None
            if self._log_handle:
                self._log_handle.close()
                self._log_handle = None
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
            InfoBar.error("启动失败", str(exc), parent=self, position=InfoBarPosition.TOP)
            return
        self.data["enabled"] = True
        self._save_data()
        self._set_status("运行中", True)
        if not self._apply_takeover():
            self._stop_service(update_switch=True)
            InfoBar.error(
                "系统代理未接管",
                "Windows 系统代理已被其他软件占用；请先关闭 Clash Verge 的系统代理。",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )
            return
        self._monitor.start()
        self.context.log("mihomo started pid=%s" % self._proc.pid)
        QTimer.singleShot(800, self._check_started)

    def _check_started(self) -> None:
        if self._proc is None:
            return
        code = self._proc.poll()
        if code is not None:
            self._report_crash(code)

    def _watch_process(self) -> None:
        if not self.data.get("enabled"):
            return
        if self._proc is None or self._proc.poll() is None:
            return
        self._report_crash(self._proc.returncode or -1)

    def _report_crash(self, code: int) -> None:
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        if self._restart_count < 3:
            self._restart_count += 1
            InfoBar.warning(
                "代理内核已退出",
                f"正在自动重启（{self._restart_count}/3）",
                parent=self,
                position=InfoBarPosition.TOP_RIGHT,
                duration=4000,
            )
            self._proc = None
            QTimer.singleShot(1500, self._start_service)
        else:
            self._proc = None
            self._stop_service(update_switch=True)
            InfoBar.error(
                "代理服务已停止",
                "mihomo 连续异常退出，请检查配置或日志",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=6000,
            )

    def _stop_service(self, update_switch: bool = True) -> None:
        self._monitor.stop()
        self._restart_count = 0
        process, self._proc = self._proc, None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._log_handle:
            self._log_handle.close()
            self._log_handle = None
        self._restore_takeover()
        self.config_path.unlink(missing_ok=True)
        self.data["enabled"] = False
        self._save_data()
        self._set_status("已停止", False)
        if update_switch and hasattr(self, "switch"):
            self.switch.blockSignals(True)
            self.switch.setChecked(False)
            self.switch.blockSignals(False)
        self.context.log("mihomo stopped")



class WhitelistDialog(QDialog):
    def __init__(self, parent=None, rule: dict | None = None):
        super().__init__(parent)
        from qfluentwidgets import FluentStyleSheet

        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("编辑自定义网站" if rule else "添加自定义网站")
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("编辑自定义网站" if rule else "添加自定义网站"))
        layout.addWidget(CaptionLabel("只填网站域名，例如 chatgpt.com；自动按 DOMAIN-SUFFIX 匹配。"))
        self.domain_edit = LineEdit()
        self.domain_edit.setPlaceholderText("例如 chatgpt.com")
        if rule:
            self.domain_edit.setText(str(rule.get("domain") or ""))
        layout.addWidget(self.domain_edit)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(make_button("保存", primary=True, on_click=self._accept))
        layout.addLayout(footer)

    def _accept(self) -> None:
        domain = self.domain_edit.text().strip().lower()
        if domain.startswith(("http://", "https://")):
            domain = domain.split("://", 1)[1]
        domain = domain.split("/", 1)[0].split(":", 1)[0].strip(".")
        if not domain or " " in domain or "." not in domain:
            InfoBar.warning("网站无效", "请输入网站域名，例如 chatgpt.com", parent=self, position=InfoBarPosition.TOP)
            return
        self.rule = {
            "type": "DOMAIN-SUFFIX",
            "domain": domain,
            "enabled": True,
        }
        self.accept()


class RuleDialog(QDialog):
    def __init__(self, parent=None, rule: dict | None = None):
        super().__init__(parent)
        from qfluentwidgets import FluentStyleSheet

        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("编辑代理规则" if rule else "添加代理规则")
        self.setFixedWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("编辑代理规则" if rule else "添加代理规则"))
        self.type_combo = ComboBox()
        self.type_combo.addItems(RULE_TYPES)
        self.value_edit = LineEdit()
        self.value_edit.setPlaceholderText("域名 / IP / 进程名；MATCH 留空")
        self.action_combo = ComboBox()
        self.action_combo.addItems(RULE_ACTIONS)
        if rule:
            self.type_combo.setCurrentText(str(rule.get("type") or "DOMAIN-SUFFIX"))
            self.value_edit.setText(str(rule.get("value") or ""))
            self.action_combo.setCurrentText(str(rule.get("action") or "PROXY"))
        layout.addWidget(self.type_combo)
        layout.addWidget(self.value_edit)
        layout.addWidget(self.action_combo)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(make_button("保存", primary=True, on_click=self._accept))
        layout.addLayout(footer)

    def _accept(self) -> None:
        kind = self.type_combo.currentText()
        value = self.value_edit.text().strip()
        if kind != "MATCH" and not value:
            InfoBar.warning("内容为空", "请填写规则内容", parent=self, position=InfoBarPosition.TOP)
            return
        self.rule = {
            "type": kind,
            "value": value,
            "action": self.action_combo.currentText(),
        }
        self.accept()


class ProxyController:
    """供 NexHub 主界面卡片直接控制代理和选择节点。"""

    def __init__(self, context, window: ProxyWindow):
        self.context = context
        self.window = window

    def state(self) -> dict:
        nodes = self.window.data.get("nodes") or []
        own_proxy = self.window._own_proxy_enabled()
        return {
            "enabled": bool(self.window._proc and self.window._proc.poll() is None),
            "node": str(self.window.data.get("selected") or ""),
            "nodes": [
                {
                    "name": str(item.get("name") or ""),
                    "latency": item.get("latency"),
                    "type": str(item.get("type") or ""),
                }
                for item in nodes
                if item.get("name")
            ],
            "port": int(self.window.data.get("port") or DEFAULT_PORT),
            "mode": str(self.window.data.get("mode") or "rule"),
            "system_proxy_active": own_proxy,
            "system_proxy": self.window._proxy_server() if own_proxy else "",
            "subscription": dict(self.window.data.get("subscription") or {}),
            "rules_count": len(self.window.data.get("rules") or []),
            "rules_source": str(self.window.data.get("rules_source") or "builtin"),
            "clash_conflict": "" if own_proxy else clash_verge_conflict(),
            "clash_port_open": clash_verge_port_open(),
        }

    def _sync_switch(self, checked: bool) -> None:
        self.window.switch.blockSignals(True)
        self.window.switch.setChecked(bool(checked))
        self.window.switch.blockSignals(False)

    def invoke(self, action: str, payload: dict) -> dict:
        if action == "enabled":
            enabled = bool(payload.get("value"))
            if enabled:
                nodes = self.window.data.get("nodes") or []
                if not self.window.data.get("selected") and nodes:
                    self.window.data["selected"] = str(nodes[0].get("name") or "")
                    self.window._save_data()
                    self.window._load_tables()
                self.window._start_service()
            else:
                self.window._stop_service(update_switch=False)
            self._sync_switch(enabled and bool(self.window._proc))
            return {"state": self.state()}
        if action == "mode":
            self.window._change_mode(str(payload.get("value") or "rule"))
            return {"state": self.state()}
        if action == "node":
            name = str(payload.get("value") or "").strip()
            nodes = self.window.data.get("nodes") or []
            if name and not any(str(item.get("name") or "") == name for item in nodes):
                raise ValueError("节点不存在")
            if name:
                was_running = bool(self.window._proc and self.window._proc.poll() is None)
                self.window.data["selected"] = name
                self.window._save_data()
                self.window._load_tables()
                self.window._update_preview()
                if was_running:
                    self.window._stop_service(update_switch=False)
                    self.window._start_service()
                    self._sync_switch(bool(self.window._proc))
            return {"state": self.state()}
        raise ValueError(f"不支持的操作：{action}")


def create_controller(context, window):
    return ProxyController(context, window)


def create_window(context):
    return ProxyWindow(context)


def fetch_paid_subscription(endpoint: str = "") -> list[dict]:
    """底层预留：由主程序或后续后端调用，不提供界面入口。"""
    source = endpoint or os.environ.get("NEXHUB_PLUGIN_SUBSCRIPTION_URL", "")
    if not source:
        return []
    return ProxyWindow._fetch_subscription(source)
