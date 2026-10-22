"""同账号在线设备心跳与在线列表。"""
from __future__ import annotations

import logging
import socket
import threading
import uuid

from ..api_client import ApiClient, ApiError
from ..config import AppConfig

log = logging.getLogger(__name__)
_HEARTBEAT_SECONDS = 20


class DeviceService:
    def __init__(self, config: AppConfig, api: ApiClient):
        self.config = config
        self.api = api
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False

    def _ensure_identity(self) -> None:
        changed = False
        if not self.config.device_id:
            self.config.device_id = uuid.uuid4().hex
            changed = True
        if not self.config.device_name:
            self.config.device_name = socket.gethostname() or "Windows PC"
            changed = True
        if changed:
            self.config.save()

    def start(self) -> None:
        if self._started or self.config.demo_mode or not self.config.token:
            return
        self._ensure_identity()
        self._started = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop,
            name="nexhub-device-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        try:
            if self.config.device_id:
                self.api.device_offline(self.config.device_id)
        except ApiError as exc:
            log.warning("设备离线通知失败：%s", exc)
        self._started = False

    def list_online(self, include_self: bool = False) -> list[dict]:
        if self.config.demo_mode:
            return []
        if include_self:
            return self.api.list_online_devices()
        return self.api.list_online_devices(self.config.device_id)

    def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            payload = {
                "device_id": self.config.device_id,
                "name": self.config.device_name,
                # 同账号设备连接时使用该隧道地址；NPC 配置发生变化后下一轮自动带上。
                "tunnel_address": (self.config.nps.host_domain or "").strip().rstrip("/"),
            }
            try:
                self.api.device_heartbeat(payload)
                log.debug("设备心跳上报：%s", self.config.device_name)
            except ApiError as exc:
                log.warning("设备心跳上报失败：%s", exc)
            self._stop.wait(_HEARTBEAT_SECONDS)
