"""域名定向加速插件：白名单域名经本地 mihomo 代理，其他流量直连。"""
from __future__ import annotations

import base64
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import requests
from PyQt6.QtCore import QTimer, Qt
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
    PlainTextEdit,
    ProgressBar,
    SpinBox,
    StrongBodyLabel,
    SwitchButton,
    TableWidget,
    TitleLabel,
)

from nexhub.widgets.common import make_button, make_card, section_header, status_pill
from nexhub.workers import FunctionWorker

LOGGER = logging.getLogger("nexhub.plugin.domain_proxy")
DEFAULT_PORT = 7890
PROXY_NAME = "NEXHUB-PROXY"


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


def parse_subscription_payload(text: str) -> list[dict]:
    raw = (text or "").strip()
    decoded = _b64_text(raw) if "://" not in raw else raw
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


def build_mihomo_config(data: dict) -> str:
    nodes = data.get("nodes") or []
    selected = data.get("selected") or (nodes[0]["name"] if nodes else "")
    whitelist = [item for item in data.get("whitelist") or [] if item.get("enabled", True)]
    config: dict = {
        "mixed-port": int(data.get("port") or DEFAULT_PORT),
        "bind-address": "127.0.0.1",
        "allow-lan": False,
        "mode": "rule",
        "log-level": "warning",
        "ipv6": False,
        "tun": {"enable": False},
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
        rules = []
        for item in whitelist:
            kind = str(item.get("type") or "DOMAIN-SUFFIX").upper()
            domain = str(item.get("domain") or "").strip()
            if kind in ("DOMAIN", "DOMAIN-SUFFIX") and domain:
                rules.append(f"{kind},{domain},{PROXY_NAME}")
        rules.append("MATCH,DIRECT")
        config["rules"] = rules
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
            "whitelist": [],
            "nodes": [],
            "selected": "",
        }
        try:
            loaded = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                default.update(loaded)
        except (OSError, json.JSONDecodeError):
            pass
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
        header, actions = section_header("域名定向加速", "仅白名单域名走本地代理，其他流量直连")
        self.status_pill = status_pill("未运行", FIF.PAUSE)
        actions.addWidget(self.status_pill)
        status_layout.addWidget(header)
        switch_row = QHBoxLayout()
        self.switch = SwitchButton()
        self.switch.setChecked(bool(self.data.get("enabled")))
        self.switch.checkedChanged.connect(self._toggle_service)
        switch_row.addWidget(BodyLabel("启用本地代理服务"))
        switch_row.addWidget(self.switch)
        switch_row.addStretch(1)
        switch_row.addWidget(CaptionLabel(f"本地端口 127.0.0.1:{self.data.get('port') or DEFAULT_PORT}"))
        status_layout.addLayout(switch_row)
        self.service_hint = CaptionLabel("仅监听本机回环地址；不会修改系统代理，也不会启用 TUN。")
        self.service_hint.setWordWrap(True)
        status_layout.addWidget(self.service_hint)
        layout.addWidget(status_card)

        whitelist_card, whitelist_layout = make_card(20, 10)
        white_header, white_actions = section_header("域名白名单", "支持 DOMAIN / DOMAIN-SUFFIX")
        white_actions.addWidget(make_button("添加规则", primary=True, icon=FIF.ADD, on_click=self._add_whitelist))
        white_actions.addWidget(make_button("编辑选中", transparent=True, icon=FIF.EDIT, on_click=self._edit_whitelist))
        white_actions.addWidget(make_button("删除选中", transparent=True, icon=FIF.DELETE, on_click=self._delete_whitelist))
        whitelist_layout.addWidget(white_header)
        self.whitelist_table = TableWidget()
        self.whitelist_table.setBorderVisible(False)
        self.whitelist_table.setBorderRadius(10)
        self.whitelist_table.setColumnCount(3)
        self.whitelist_table.setHorizontalHeaderLabels(["类型", "域名", "启用"])
        self.whitelist_table.verticalHeader().setVisible(False)
        self.whitelist_table.setShowGrid(False)
        self.whitelist_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.whitelist_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.whitelist_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.whitelist_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.whitelist_table.setMinimumHeight(160)
        whitelist_layout.addWidget(self.whitelist_table)
        layout.addWidget(whitelist_card)


        upstream_card, upstream_layout = make_card(20, 10)
        up_header, up_actions = section_header("上游线路", "支持订阅链接导入、手动节点与延迟测速")
        up_actions.addWidget(make_button("添加节点", icon=FIF.ADD, on_click=self._add_node))
        up_actions.addWidget(make_button("测速选线", icon=FIF.SYNC, on_click=self._test_nodes))
        upstream_layout.addWidget(up_header)
        sub_row = QHBoxLayout()
        self.subscription_edit = LineEdit()
        self.subscription_edit.setPlaceholderText("粘贴订阅链接")
        sub_row.addWidget(self.subscription_edit, 1)
        sub_row.addWidget(make_button("解析导入", primary=True, on_click=self._import_subscription))
        upstream_layout.addLayout(sub_row)
        self.nodes_table = TableWidget()
        self.nodes_table.setBorderVisible(False)
        self.nodes_table.setBorderRadius(10)
        self.nodes_table.setColumnCount(5)
        self.nodes_table.setHorizontalHeaderLabels(["名称", "类型", "服务器", "端口", "延迟"])
        self.nodes_table.verticalHeader().setVisible(False)
        self.nodes_table.setShowGrid(False)
        self.nodes_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.nodes_table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.nodes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.nodes_table.setMinimumHeight(180)
        self.nodes_table.itemSelectionChanged.connect(self._select_node)
        upstream_layout.addWidget(self.nodes_table)
        node_actions = QHBoxLayout()
        node_actions.addStretch(1)
        node_actions.addWidget(make_button("删除选中节点", transparent=True, icon=FIF.DELETE, on_click=self._delete_node))
        upstream_layout.addLayout(node_actions)
        layout.addWidget(upstream_card)

        preview_card, preview_layout = make_card(20, 10)
        preview_header, preview_actions = section_header("配置预览", "严格白名单模式，未命中规则一律 DIRECT")
        preview_actions.addWidget(make_button("刷新预览", transparent=True, icon=FIF.SYNC, on_click=self._update_preview))
        preview_layout.addWidget(preview_header)
        self.preview = PlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(230)
        preview_layout.addWidget(self.preview)
        layout.addWidget(preview_card)
        layout.addStretch(1)

    def _set_status(self, text: str, running: bool) -> None:
        self.status_pill.setText(text)
        self.status_pill.setIcon(FIF.PLAY if running else FIF.PAUSE)

    def _load_tables(self) -> None:
        self.whitelist_table.setRowCount(0)
        for item in self.data.get("whitelist") or []:
            row = self.whitelist_table.rowCount()
            self.whitelist_table.insertRow(row)
            self.whitelist_table.setItem(row, 0, QTableWidgetItem(str(item.get("type") or "DOMAIN-SUFFIX")))
            self.whitelist_table.setItem(row, 1, QTableWidgetItem(str(item.get("domain") or "")))
            check = CheckBox()
            check.setChecked(bool(item.get("enabled", True)))
            check.stateChanged.connect(lambda state, index=row: self._toggle_whitelist(index, state))
            self.whitelist_table.setCellWidget(row, 2, check)
        self.nodes_table.setRowCount(0)
        for node in self.data.get("nodes") or []:
            row = self.nodes_table.rowCount()
            self.nodes_table.insertRow(row)
            values = [node.get("name", ""), node.get("type", ""), node.get("server", ""), node.get("port", ""), node.get("latency", "")]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.nodes_table.setItem(row, column, item)
        selected = str(self.data.get("selected") or "")
        for row in range(self.nodes_table.rowCount()):
            if self.nodes_table.item(row, 0).text() == selected:
                self.nodes_table.selectRow(row)
                break

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
            selected = str(nodes[row].get("name") or "")
            if selected != self.data.get("selected"):
                self.data["selected"] = selected
                self._save_data()
                self._update_preview()

    def _import_subscription(self) -> None:
        url = self.subscription_edit.text().strip()
        if not url:
            InfoBar.warning("缺少链接", "请先粘贴订阅链接", parent=self, position=InfoBarPosition.TOP)
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self._fetch_subscription, url, parent=self)
        self._worker.succeeded.connect(self._on_subscription)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    @staticmethod
    def _fetch_subscription(url: str) -> list[dict]:
        response = requests.get(
            url,
            timeout=20,
            headers={"User-Agent": "NexHub-Plugin/1.0"},
        )
        response.raise_for_status()
        nodes = parse_subscription_payload(response.text)
        if not nodes:
            raise ValueError("链接中没有解析到支持的节点")
        return nodes

    def _on_subscription(self, nodes: list[dict]) -> None:
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
        self._save_data()
        self._load_tables()
        self._update_preview()
        InfoBar.success("导入完成", f"已导入 {len(added)} 个节点", parent=self, position=InfoBarPosition.TOP_RIGHT)

    def _test_nodes(self) -> None:
        nodes = self.data.get("nodes") or []
        if not nodes:
            InfoBar.warning("没有节点", "请先导入或添加节点", parent=self, position=InfoBarPosition.TOP)
            return
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self._test_latencies, list(nodes), parent=self)
        self._worker.succeeded.connect(self._on_latencies)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    @staticmethod
    def _test_latencies(nodes: list[dict]) -> list[dict]:
        results = []
        for node in nodes:
            item = dict(node)
            started = time.perf_counter()
            try:
                with socket.create_connection(
                    (str(item.get("server")), int(item.get("port") or 0)), timeout=2
                ):
                    item["latency"] = int((time.perf_counter() - started) * 1000)
            except OSError:
                item["latency"] = -1
            results.append(item)
        return results

    def _on_latencies(self, nodes: list[dict]) -> None:
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
        InfoBar.error("操作失败", message, parent=self, position=InfoBarPosition.TOP)


    def _update_preview(self) -> None:
        text = build_mihomo_config(self.data)
        self.preview.setPlainText(text)
        if self._proc and self._proc.poll() is None:
            self.service_hint.setText("配置已更新；重新启用代理服务后生效。仅本机回环监听，不修改系统代理。")
        else:
            self.service_hint.setText("仅监听本机回环地址；不会修改系统代理，也不会启用 TUN。")

    def _toggle_service(self, checked: bool) -> None:
        if checked:
            self._start_service()
        else:
            self._stop_service(update_switch=False)

    def _start_service(self) -> None:
        if self._proc and self._proc.poll() is None:
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
        self.config_path.unlink(missing_ok=True)
        self.data["enabled"] = False
        self._save_data()
        self._set_status("未运行", False)
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
        self.setWindowTitle("编辑白名单规则" if rule else "添加白名单规则")
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(8)
        layout.addWidget(TitleLabel("编辑白名单规则" if rule else "添加白名单规则"))
        self.type_combo = ComboBox()
        self.type_combo.addItems(["DOMAIN-SUFFIX", "DOMAIN"])
        self.domain_edit = LineEdit()
        self.domain_edit.setPlaceholderText("例如 example.com")
        if rule:
            self.type_combo.setCurrentText(str(rule.get("type") or "DOMAIN-SUFFIX"))
            self.domain_edit.setText(str(rule.get("domain") or ""))
        layout.addWidget(self.type_combo)
        layout.addWidget(self.domain_edit)
        footer = QHBoxLayout()
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(make_button("保存", primary=True, on_click=self._accept))
        layout.addLayout(footer)

    def _accept(self) -> None:
        domain = self.domain_edit.text().strip().lower()
        if not domain or "://" in domain or "/" in domain or " " in domain:
            InfoBar.warning("域名无效", "请输入不带协议和路径的域名", parent=self, position=InfoBarPosition.TOP)
            return
        self.rule = {
            "type": self.type_combo.currentText(),
            "domain": domain,
            "enabled": True,
        }
        self.accept()


class ProxyController:
    """供 NexHub 主界面卡片直接控制代理和选择节点。"""

    def __init__(self, context, window: ProxyWindow):
        self.context = context
        self.window = window

    def state(self) -> dict:
        nodes = self.window.data.get("nodes") or []
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
        }

    def _sync_switch(self, checked: bool) -> None:
        self.window.switch.blockSignals(True)
        self.window.switch.setChecked(bool(checked))
        self.window.switch.blockSignals(False)

    def invoke(self, action: str, payload: dict) -> dict:
        if action == "enabled":
            enabled = bool(payload.get("value"))
            if enabled:
                self.window._start_service()
            else:
                self.window._stop_service(update_switch=False)
            self._sync_switch(enabled and bool(self.window._proc))
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

