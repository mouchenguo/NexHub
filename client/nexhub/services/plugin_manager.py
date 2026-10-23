"""外部插件目录注册中心（manifest 插件，进程由 PluginProcessManager 隔离启动）。"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

from ..paths import user_plugins_dir

if TYPE_CHECKING:
    from ..context import AppContext

PLUGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
REQUIRED_MANIFEST_FIELDS = ("id", "name", "version", "description", "requirements")


class ToolPlugin(ABC):
    """旧版插件类型，仅保留用于源码兼容；工具箱不再在主进程 import 插件。"""

    plugin_id: str = ""
    name: str = "未命名插件"
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    glyph: str = "✦"
    accent: str = "accent"

    @abstractmethod
    def create_widget(self, ctx: "AppContext", parent: QWidget | None = None) -> QWidget:
        """旧版内嵌插件界面；新插件请使用 create_window(context)。"""


@dataclass
class PluginRecord:
    plugin_id: str
    name: str
    version: str
    description: str
    requirements: str
    folder: Path
    manifest: dict = field(default_factory=dict)
    category: str = "其他"
    glyph: str = "✦"
    icon_path: Path | None = None

    @property
    def id(self) -> str:
        return self.plugin_id

    @property
    def entry(self) -> Path:
        return self.folder / "plugin.py"

    @classmethod
    def from_folder(cls, folder: Path) -> "PluginRecord":
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("缺少 manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"manifest.json 无法解析：{exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError("manifest.json 必须是对象")
        missing = [key for key in REQUIRED_MANIFEST_FIELDS if key not in manifest]
        if missing:
            raise ValueError(f"manifest.json 缺少字段：{', '.join(missing)}")
        plugin_id = str(manifest.get("id") or "").strip()
        if not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
            raise ValueError(f"插件 ID 非法：{plugin_id!r}")
        name = str(manifest.get("name") or "").strip()
        version = str(manifest.get("version") or "").strip()
        if not name or not version:
            raise ValueError("插件 name/version 不能为空")
        if not (folder / "plugin.py").is_file():
            raise ValueError("缺少 plugin.py")
        icon = folder / "icon.png"
        return cls(
            plugin_id=plugin_id,
            name=name,
            version=version,
            description=str(manifest.get("description") or "").strip(),
            requirements=str(manifest.get("requirements") or "").strip(),
            folder=folder.resolve(),
            manifest=manifest,
            category=str(manifest.get("category") or "其他").strip() or "其他",
            glyph=str(manifest.get("glyph") or "✦").strip() or "✦",
            icon_path=icon if icon.is_file() else None,
        )


class PluginManager:
    """扫描 ``%APPDATA%/NexHub/plugins/<id>/manifest.json``，不 import 插件代码。"""

    def __init__(self, disabled_ids: set[str] | None = None) -> None:
        self._plugins: dict[str, PluginRecord] = {}
        self._disabled_ids: set[str] = set(disabled_ids or ())
        self.errors: list[str] = []

    def reload_custom(self) -> list[str]:
        self._plugins.clear()
        self.errors = []
        folder = user_plugins_dir()
        for path in sorted(folder.iterdir() if folder.exists() else []):
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                record = PluginRecord.from_folder(path)
                if record.plugin_id in self._plugins:
                    raise ValueError(f"插件 ID 冲突：{record.plugin_id}")
                self._plugins[record.plugin_id] = record
            except Exception as exc:  # noqa: BLE001 - 单个插件错误不影响主程序
                self.errors.append(f"{path.name}: {exc}")
        return list(self.errors)

    def register(self, plugin: PluginRecord) -> None:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"插件 ID 冲突：{plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin

    def all(self) -> list[PluginRecord]:
        return [
            plugin
            for plugin_id, plugin in self._plugins.items()
            if plugin_id not in self._disabled_ids
        ]

    def all_plugins(self) -> list[PluginRecord]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> PluginRecord | None:
        return self._plugins.get(plugin_id)

    def is_custom(self, _plugin_id: str) -> bool:
        return True

    def set_enabled_ids(self, enabled_ids: set[str]) -> None:
        self._disabled_ids = set(self._plugins) - set(enabled_ids)

    @property
    def disabled_ids(self) -> set[str]:
        return set(self._disabled_ids)
