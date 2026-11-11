"""工作空间管理弹窗：控制内置页面显示与插件加载状态。

语义：
- 固定项（主页 / 设置）永远显示，不可取消；
- 内置功能勾选控制侧栏入口，取消只隐藏、不销毁页面实例；
- 插件区区分「加载到工作空间 / 从工作空间移除 / 卸载」，按钮立即生效。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon as FIF,
    FluentStyleSheet,
    StrongBodyLabel,
    TitleLabel,
)

from ..workspace import FIXED_PAGE_IDS, PAGE_SPECS, PAGE_SPECS_BY_ID
from ..widgets.common import ask_confirm, clear_layout, icon_button, make_button


class WorkspaceManagerDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._checks: dict[str, CheckBox] = {}
        self.setWindowTitle("NexHub · 工作空间管理")
        self.setMinimumSize(640, 580)
        FluentStyleSheet.DIALOG.apply(self)
        self._build()

    # ------------------------------------------------------------------ 构建
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(TitleLabel("工作空间管理"))
        hint = CaptionLabel(
            "固定项始终显示；“添加选项卡”入口永远可用。"
            "取消勾选只隐藏侧栏入口，页面对象与输入状态会保留。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(8)
        self.list_layout.addStretch(1)
        scroll.setWidget(container)
        layout.addWidget(scroll, 1)

        self._add_fixed_section()
        self._add_builtin_section()
        self._add_plugin_section()

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(make_button("取消", on_click=self.reject))
        buttons.addWidget(
            make_button("保存", primary=True, icon=None, on_click=self._save)
        )
        layout.addLayout(buttons)

    def _insert(self, widget: QWidget) -> None:
        self.list_layout.insertWidget(self.list_layout.count() - 1, widget)

    def _add_fixed_section(self) -> None:
        self._insert(StrongBodyLabel("固定项"))
        for page_id in FIXED_PAGE_IDS:
            spec = PAGE_SPECS_BY_ID.get(page_id)
            if spec is None:
                continue
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            check = CheckBox(spec.title)
            check.setChecked(True)
            check.setEnabled(False)
            row_layout.addWidget(check)
            row_layout.addStretch(1)
            row_layout.addWidget(CaptionLabel("已固定，不可取消"))
            self._insert(row)

    def _add_builtin_section(self) -> None:
        self._insert(StrongBodyLabel("内置功能"))
        for spec in PAGE_SPECS:
            if spec.fixed or (spec.admin_only and not self.ctx.is_admin):
                continue
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            check = CheckBox(spec.title)
            check.setChecked(not self.ctx.workspace.is_hidden(spec.page_id))
            row_layout.addWidget(check)
            row_layout.addStretch(1)
            subtitle = spec.subtitle or ""
            if spec.admin_only:
                subtitle = f"{subtitle} · 仅管理员" if subtitle else "仅管理员"
            if subtitle:
                row_layout.addWidget(CaptionLabel(subtitle))
            self._checks[spec.page_id] = check
            self._insert(row)

    def _add_plugin_section(self) -> None:
        self._insert(StrongBodyLabel("插件"))
        hint = CaptionLabel("勾选后加入工作空间并在侧栏显示选项卡；卸载请点右侧按钮。")
        hint.setWordWrap(True)
        self._insert(hint)
        self._plugin_checks: dict[str, CheckBox] = {}
        self.plugin_host = QWidget()
        self.plugin_layout = QVBoxLayout(self.plugin_host)
        self.plugin_layout.setContentsMargins(0, 0, 0, 0)
        self.plugin_layout.setSpacing(8)
        self._insert(self.plugin_host)
        self._render_plugins()

    # ------------------------------------------------------------------ 插件区
    def _render_plugins(self) -> None:
        if not hasattr(self, "plugin_layout"):
            return
        clear_layout(self.plugin_layout)
        self._plugin_checks.clear()
        records = self.ctx.plugins.all_plugins()
        if not records:
            hint = CaptionLabel("还没有已安装插件，可在「插件广场」安装。")
            hint.setWordWrap(True)
            self.plugin_layout.addWidget(hint)
            return
        for record in records:
            self.plugin_layout.addWidget(self._plugin_row(record))

    def _plugin_row(self, record) -> QWidget:
        loaded = self.ctx.workspace.is_plugin_loaded(record.plugin_id)
        running = self.ctx.plugin_processes.is_running(record.plugin_id)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        check = CheckBox(f"{record.name} v{record.version}")
        check.setChecked(loaded)
        self._plugin_checks[record.plugin_id] = check
        layout.addWidget(check)
        layout.addStretch(1)

        if loaded:
            state = "已加载 · 运行中" if running else "已加载 · 已停止"
        else:
            state = "已安装 · 未加载"
        layout.addWidget(CaptionLabel(state))
        layout.addWidget(
            icon_button(
                FIF.DELETE,
                tooltip=f"卸载插件「{record.name}」",
                on_click=lambda _checked=False, pid=record.plugin_id, name=record.name: (
                    self._uninstall(pid, name)
                ),
            )
        )
        row.setToolTip(record.description)
        return row

    def _uninstall(self, plugin_id: str, name: str) -> None:
        if not ask_confirm(
            self,
            "卸载插件",
            f"将删除「{name}」的插件包与插件数据，此操作不可撤销。",
        ):
            return
        try:
            self.ctx.uninstall_plugin(plugin_id)
        except Exception as exc:  # noqa: BLE001
            self.ctx.notify(f"卸载失败：{exc}", "error")
        self._render_plugins()

    # ------------------------------------------------------------------ 保存
    def _save(self) -> None:
        changed = False
        for page_id, check in self._checks.items():
            if self.ctx.workspace.set_hidden(page_id, not check.isChecked()):
                changed = True

        loaded_now = set(self.ctx.workspace.loaded_plugin_ids())
        wanted = {
            plugin_id
            for plugin_id, check in self._plugin_checks.items()
            if check.isChecked()
        }
        for plugin_id in sorted(loaded_now - wanted):
            self.ctx.unload_plugin_workspace(plugin_id)
            changed = True
        for plugin_id in sorted(wanted - loaded_now):
            try:
                self.ctx.load_plugin_workspace(plugin_id, start=True)
                changed = True
            except Exception as exc:  # noqa: BLE001
                self.ctx.notify(f"加载插件失败：{exc}", "error")

        self.ctx.save_workspace()
        if changed:
            self.ctx.notify("工作空间选项卡已更新", "success")
        self.accept()
