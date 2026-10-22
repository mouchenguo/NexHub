"""工具箱插件注册中心。

新增插件只需继承 ``ToolPlugin`` 并注册到管理器，无需修改主程序。
后续可扩展 Outlook 集成、短信代收等插件。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QWidget

from ..paths import user_plugins_dir

if TYPE_CHECKING:
    from ..context import AppContext


class ToolPlugin(ABC):
    plugin_id: str = ""
    name: str = "未命名插件"
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    glyph: str = "✦"
    accent: str = "accent"

    @abstractmethod
    def create_widget(self, ctx: "AppContext", parent: QWidget | None = None) -> QWidget:
        """创建插件主界面。"""


class PluginManager:
    def __init__(self, disabled_ids: set[str] | None = None) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        self._dynamic_ids: set[str] = set()
        self._disabled_ids: set[str] = set(disabled_ids or ())
        self.errors: list[str] = []

    def register(self, plugin: ToolPlugin) -> None:
        if not plugin.plugin_id:
            raise ValueError("插件必须提供 plugin_id")
        self._plugins[plugin.plugin_id] = plugin

    def all(self) -> list[ToolPlugin]:
        return [
            plugin
            for plugin_id, plugin in self._plugins.items()
            if plugin_id not in self._disabled_ids
        ]

    def all_plugins(self) -> list[ToolPlugin]:
        """管理弹窗使用：包含已关闭加载的插件。"""
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> ToolPlugin | None:
        return self._plugins.get(plugin_id)

    def is_custom(self, plugin_id: str) -> bool:
        return plugin_id in self._dynamic_ids

    def set_enabled_ids(self, enabled_ids: set[str]) -> None:
        self._disabled_ids = set(self._plugins) - set(enabled_ids)

    @property
    def disabled_ids(self) -> set[str]:
        return set(self._disabled_ids)

    def reload_custom(self) -> list[str]:
        """重新扫描用户插件目录，替换上一次加载的自定义插件。"""
        for plugin_id in self._dynamic_ids:
            self._plugins.pop(plugin_id, None)
        self._dynamic_ids.clear()
        self.errors = []
        directory = user_plugins_dir()
        for path in sorted(directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            module_name = f"nexhub_user_plugin_{path.stem}_{time.time_ns()}"
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                if spec is None or spec.loader is None:
                    raise ImportError("无法创建插件加载器")
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
                found = False
                for value in vars(module).values():
                    if not (
                        inspect.isclass(value)
                        and issubclass(value, ToolPlugin)
                        and value is not ToolPlugin
                    ):
                        continue
                    plugin = value()
                    if not plugin.plugin_id:
                        raise ValueError("插件缺少 plugin_id")
                    if plugin.plugin_id in self._plugins:
                        raise ValueError(
                            f"插件 ID 与现有插件冲突：{plugin.plugin_id}"
                        )
                    self.register(plugin)
                    self._dynamic_ids.add(plugin.plugin_id)
                    found = True
                if not found:
                    raise ValueError("文件中没有找到 ToolPlugin 子类")
            except Exception as exc:  # noqa: BLE001 - 插件错误不能影响主程序
                self.errors.append(f"{path.name}: {exc}")
                sys.modules.pop(module_name, None)
        return list(self.errors)
