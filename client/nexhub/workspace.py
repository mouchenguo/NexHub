"""工作空间注册表：页面可见性 + 插件加载状态。

刻意做成**纯 Python**（不 import PyQt），以便脱离 GUI 单测：
- 内置页面：固定项（home/settings）常显，其余可隐藏；
- 仪表盘：仅管理员可见，管理员可隐藏；
- 插件：``installed``（包在磁盘）/ ``loaded``（侧栏有选项卡）/ ``running``
  （宿主进程在跑，仍由 ``active_plugins`` 表示）三种状态完全解耦。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

PLUGIN_PAGE_PREFIX = "plugin:"

# 固定项：永远显示，配置里即使写了也会被强制忽略。
FIXED_PAGE_IDS: tuple[str, ...] = ("home", "settings")

# 侧栏顺序：插件选项卡插在“插件广场”之后、“仪表盘/设置”之前。
PAGE_ORDER_BEFORE_PLUGINS: tuple[str, ...] = (
    "home",
    "my_shares",
    "square",
    "rdp",
    "plugin_market",
)
PAGE_ORDER_AFTER_PLUGINS: tuple[str, ...] = ("dashboard", "settings")

# 固定在侧栏底部（头像之上）的页面。
BOTTOM_PAGE_IDS: tuple[str, ...] = ("settings",)


@dataclass(frozen=True)
class PageSpec:
    page_id: str
    title: str
    icon: str = "APPLICATION"  # qfluentwidgets FluentIcon 成员名
    subtitle: str = ""
    fixed: bool = False
    admin_only: bool = False


PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec("home", "主页", "HOME", "功能导航与工作空间入口", fixed=True),
    PageSpec("my_shares", "我的共享", "SHARE", "管理本人发布的所有共享"),
    PageSpec("square", "共享广场", "GLOBE", "浏览其他用户发布的公开共享"),
    PageSpec("rdp", "RDP 主机", "PROJECTOR", "远程桌面连接与内网主机"),
    PageSpec("plugin_market", "插件广场", "SHOPPING_CART", "浏览、安装与更新插件"),
    PageSpec(
        "dashboard",
        "仪表盘",
        "SPEED_HIGH",
        "管理员专属的全局用量与日志",
        admin_only=True,
    ),
    PageSpec("settings", "设置", "SETTING", "服务器、主题与传输设置", fixed=True),
)

PAGE_SPECS_BY_ID: dict[str, PageSpec] = {spec.page_id: spec for spec in PAGE_SPECS}
PAGE_IDS: tuple[str, ...] = tuple(spec.page_id for spec in PAGE_SPECS)


def plugin_page_id(plugin_id: str) -> str:
    return f"{PLUGIN_PAGE_PREFIX}{plugin_id}"


def is_plugin_page(page_id: str) -> bool:
    return str(page_id or "").startswith(PLUGIN_PAGE_PREFIX)


def plugin_id_from_page(page_id: str) -> str:
    text = str(page_id or "")
    return text[len(PLUGIN_PAGE_PREFIX):] if text.startswith(PLUGIN_PAGE_PREFIX) else ""


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


class WorkspaceRegistry:
    """读写 ``AppConfig.workspace_*``，并回答“这一页现在该不该显示”。"""

    def __init__(self, config: Any, *, is_admin: bool = False) -> None:
        self.config = config
        self.is_admin = bool(is_admin)

    # ---------------------------------------------------------------- 基础
    @property
    def hidden_pages(self) -> list[str]:
        return list(self.config.workspace_hidden_pages)

    def spec(self, page_id: str) -> PageSpec | None:
        return PAGE_SPECS_BY_ID.get(page_id)

    def can_hide(self, page_id: str) -> bool:
        spec = self.spec(page_id)
        return bool(spec and not spec.fixed)

    def is_hidden(self, page_id: str) -> bool:
        return page_id in self.config.workspace_hidden_pages

    def set_hidden(self, page_id: str, hidden: bool) -> bool:
        """返回是否有实际变化；固定项/未知页面一律忽略。"""
        if not self.can_hide(page_id):
            return False
        hidden_ids = list(self.config.workspace_hidden_pages)
        if hidden:
            if page_id in hidden_ids:
                return False
            hidden_ids.append(page_id)
        else:
            if page_id not in hidden_ids:
                return False
            hidden_ids = [item for item in hidden_ids if item != page_id]
        self.config.workspace_hidden_pages = dedupe(hidden_ids)
        return True

    def set_admin(self, is_admin: bool) -> bool:
        value = bool(is_admin)
        changed = value != self.is_admin
        self.is_admin = value
        return changed

    # ---------------------------------------------------------------- 可见性
    def visible_builtin_ids(self) -> list[str]:
        hidden = set(self.config.workspace_hidden_pages)
        result: list[str] = []
        for spec in PAGE_SPECS:
            if spec.admin_only and not self.is_admin:
                continue
            if spec.fixed or spec.page_id not in hidden:
                result.append(spec.page_id)
        return result

    def page_ids(self) -> list[str]:
        """侧栏完整顺序（不含“添加选项卡”这个无页面条目）。"""
        visible = set(self.visible_builtin_ids())
        result = [pid for pid in PAGE_ORDER_BEFORE_PLUGINS if pid in visible]
        result.extend(plugin_page_id(pid) for pid in self.loaded_plugin_ids())
        result.extend(pid for pid in PAGE_ORDER_AFTER_PLUGINS if pid in visible)
        return result

    def is_visible(self, page_id: str) -> bool:
        return page_id in self.page_ids()

    # ---------------------------------------------------------------- 插件加载
    def loaded_plugin_ids(self) -> list[str]:
        return list(self.config.workspace_loaded_plugins)

    def is_plugin_loaded(self, plugin_id: str) -> bool:
        return plugin_id in self.config.workspace_loaded_plugins

    def load_plugin(self, plugin_id: str) -> bool:
        plugin_id = str(plugin_id or "").strip()
        if not plugin_id or self.is_plugin_loaded(plugin_id):
            return False
        self.config.workspace_loaded_plugins = dedupe(
            [*self.config.workspace_loaded_plugins, plugin_id]
        )
        self.config.workspace_tab_order = dedupe(
            [*self.config.workspace_tab_order, plugin_id]
        )
        return True

    def unload_plugin(self, plugin_id: str) -> bool:
        plugin_id = str(plugin_id or "").strip()
        if not self.is_plugin_loaded(plugin_id):
            return False
        self.config.workspace_loaded_plugins = [
            item for item in self.config.workspace_loaded_plugins if item != plugin_id
        ]
        self.config.active_plugins = [
            item for item in self.config.active_plugins if item != plugin_id
        ]
        return True

    # ---------------------------------------------------------------- 清洗/迁移
    def normalize(self, installed_ids: Iterable[str]) -> bool:
        """清洗配置；老配置首次升级时把“已安装且未禁用”的插件加载进来。

        返回是否有改动，调用方据此决定是否持久化。
        """
        installed = {str(item or "").strip() for item in installed_ids}
        installed.discard("")
        before = self._snapshot()

        # 1) 隐藏页：只允许已知的非固定页（home/settings 强制常显）
        self.config.workspace_hidden_pages = dedupe(
            pid
            for pid in self.config.workspace_hidden_pages
            if pid in PAGE_IDS and pid not in FIXED_PAGE_IDS
        )

        # 2) 已加载插件：只保留仍安装的；老配置走一次性迁移
        loaded = [
            pid for pid in self.config.workspace_loaded_plugins if pid in installed
        ]
        if getattr(self.config, "workspace_bootstrap", False):
            legacy_disabled = {
                str(item) for item in (self.config.disabled_plugins or [])
            }
            loaded = [pid for pid in sorted(installed) if pid not in legacy_disabled]
            self.config.workspace_bootstrap = False
        self.config.workspace_loaded_plugins = dedupe(loaded)

        # 3) 顺序预留字段：只保留已知页面与仍安装的插件
        known = set(PAGE_IDS) | set(installed)
        self.config.workspace_tab_order = dedupe(
            pid for pid in self.config.workspace_tab_order if pid in known
        )

        # 4) 运行标记不得指向“未加载”的插件
        loaded_set = set(self.config.workspace_loaded_plugins)
        self.config.active_plugins = dedupe(
            pid for pid in self.config.active_plugins if pid in loaded_set
        )

        return before != self._snapshot()

    def _snapshot(self) -> tuple:
        return (
            tuple(self.config.workspace_hidden_pages),
            tuple(self.config.workspace_loaded_plugins),
            tuple(self.config.workspace_tab_order),
            tuple(self.config.active_plugins),
        )

