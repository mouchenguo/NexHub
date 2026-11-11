"""工作空间注册表与配置迁移的纯逻辑测试（不需要 QApplication）。

运行：
    python client/tests/test_workspace.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

CLIENT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CLIENT_ROOT))
# 只解析配置，不写盘；重定向数据目录避免碰真实 %APPDATA%\NexHub
os.environ.setdefault("NEXHUB_DATA_DIR", str(CLIENT_ROOT.parent / ".tmp" / "workspace_test_data"))

from nexhub.config import AppConfig  # noqa: E402
from nexhub.workspace import (  # noqa: E402
    FIXED_PAGE_IDS,
    WorkspaceRegistry,
    plugin_page_id,
)

fails: list[str] = []


def check(condition: bool, message: str) -> None:
    print(("PASS: " if condition else "FAIL: ") + message)
    if not condition:
        fails.append(message)


def make_config(raw: dict) -> AppConfig:
    return AppConfig.from_dict(raw)


# ---------------------------------------------------------------- 1) 首次迁移
legacy = make_config({"disabled_plugins": ["beta"], "active_plugins": ["alpha"]})
registry = WorkspaceRegistry(legacy, is_admin=False)
changed = registry.normalize(["alpha", "beta", "gamma"])
check(changed, "老配置首次 normalize 返回已变更")
check(
    registry.loaded_plugin_ids() == ["alpha", "gamma"],
    f"已安装且未禁用的插件迁移为已加载：{registry.loaded_plugin_ids()}",
)
check(
    legacy.active_plugins == ["alpha"],
    "运行标记仅保留已加载插件",
)
check(
    not getattr(legacy, "workspace_bootstrap", True),
    "迁移标记在 normalize 后被清除",
)
check(
    "workspace_bootstrap" not in legacy.to_dict(),
    "迁移标记不会写入配置文件",
)

# ---------------------------------------------------------------- 2) 再次启动不再迁移
saved = make_config({"workspace_loaded_plugins": []})
saved_registry = WorkspaceRegistry(saved, is_admin=False)
saved_registry.normalize(["alpha", "beta"])
check(
    saved_registry.loaded_plugin_ids() == [],
    "已存在的空加载列表不会被迁移逻辑重置",
)

# ---------------------------------------------------------------- 3) 固定项不可隐藏
cfg = make_config({})
reg = WorkspaceRegistry(cfg, is_admin=False)
for page_id in FIXED_PAGE_IDS:
    check(not reg.set_hidden(page_id, True), f"固定项 {page_id} 不可隐藏")
cfg.workspace_hidden_pages = ["home", "settings", "square"]
reg.normalize([])
check(
    cfg.workspace_hidden_pages == ["square"],
    f"normalize 强制剔除固定项：{cfg.workspace_hidden_pages}",
)

# ---------------------------------------------------------------- 4) 管理员可见性
user_view = WorkspaceRegistry(make_config({}), is_admin=False)
check("dashboard" not in user_view.page_ids(), "普通用户看不到仪表盘")
admin_view = WorkspaceRegistry(make_config({}), is_admin=True)
check("dashboard" in admin_view.page_ids(), "管理员默认看到仪表盘")
admin_view.set_hidden("dashboard", True)
check("dashboard" not in admin_view.page_ids(), "管理员可以隐藏仪表盘")

# ---------------------------------------------------------------- 5) 插件页顺序
order_cfg = make_config({"workspace_loaded_plugins": ["p1", "p2"]})
order_reg = WorkspaceRegistry(order_cfg, is_admin=True)
order_reg.normalize(["p1", "p2"])
pages = order_reg.page_ids()
check(
    pages.index(plugin_page_id("p1")) > pages.index("plugin_market")
    and pages.index(plugin_page_id("p2")) < pages.index("dashboard")
    and pages[-1] == "settings",
    f"插件选项卡位于插件广场之后、仪表盘/设置之前：{pages}",
)

# ---------------------------------------------------------------- 6) 悬空插件清理
stale = make_config({"workspace_loaded_plugins": ["ghost"], "active_plugins": ["ghost"]})
stale_reg = WorkspaceRegistry(stale, is_admin=False)
stale_reg.normalize(["real"])
check(stale_reg.loaded_plugin_ids() == [], "插件目录被删除后加载状态被清理")
check(stale.active_plugins == [], "悬空插件的运行标记被清理")

# ---------------------------------------------------------------- 7) 移除/加载语义
ops_cfg = make_config({})
ops = WorkspaceRegistry(ops_cfg, is_admin=False)
check(ops.load_plugin("x"), "加载插件返回已变更")
check(ops.is_plugin_loaded("x") and plugin_page_id("x") in ops.page_ids(), "加载后出现选项卡")
ops_cfg.active_plugins = ["x"]
check(ops.unload_plugin("x"), "移除插件返回已变更")
check(
    not ops.is_plugin_loaded("x") and "x" not in ops_cfg.active_plugins,
    "移除后同时清理加载状态与运行标记",
)
check(not ops.load_plugin(""), "空插件 ID 不会写入配置")

# ---------------------------------------------------------------- 结果
print()
if fails:
    print(f"FAILED {len(fails)} CHECK(S):")
    for item in fails:
        print(" -", item)
    raise SystemExit(1)
print("ALL WORKSPACE CHECKS PASSED")
