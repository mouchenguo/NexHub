"""内存优化插件（Fluent 版本）。"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    ProgressBar,
    TitleLabel,
)

from ..file_utils import human_size
from ..services.memory_optimizer import MemoryOptimizer
from ..services.plugin_manager import ToolPlugin
from ..widgets.common import make_button, make_card, section_header
from ..workers import FunctionWorker


class MemoryPluginWidget(QWidget):
    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.optimizer = MemoryOptimizer()
        self._worker: FunctionWorker | None = None
        self._build()
        self.refresh_stats()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        card, card_layout = make_card(20, 12)
        header, actions = section_header("内存优化", "回收进程工作集，缓解 Windows 内存压力")
        actions.addWidget(CaptionLabel("v1.0 · 内置"))
        card_layout.addWidget(header)

        metrics = QHBoxLayout()
        metrics.setSpacing(28)
        self.used_value, used_box = self._metric("已用内存")
        self.percent_value, percent_box = self._metric("内存占用")
        self.process_value, process_box = self._metric("运行进程")
        metrics.addLayout(used_box, 1)
        metrics.addLayout(percent_box, 1)
        metrics.addLayout(process_box, 1)
        card_layout.addLayout(metrics)

        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        card_layout.addWidget(self.progress)

        self.detail = CaptionLabel("正在读取系统内存状态...")
        card_layout.addWidget(self.detail)

        actions_row = QHBoxLayout()
        self.optimize_button = make_button(
            "立即优化", primary=True, icon=FIF.BROOM, on_click=self._optimize
        )
        actions_row.addWidget(self.optimize_button)
        actions_row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh_stats)
        )
        actions_row.addStretch(1)
        card_layout.addLayout(actions_row)

        self.result = BodyLabel("")
        self.result.setWordWrap(True)
        self.result.hide()
        card_layout.addWidget(self.result)

        hint = CaptionLabel(
            "提示：部分系统进程需要管理员权限；优化会触发内存换页，频繁执行可能影响性能。"
        )
        hint.setWordWrap(True)
        card_layout.addWidget(hint)
        layout.addWidget(card)

        info_card, info_layout = make_card(20, 8)
        info_header, _ = section_header("插件化扩展")
        info_layout.addWidget(info_header)
        info = BodyLabel(
            "点击工具箱顶部的「添加插件」可生成 ToolPlugin 模板，保存到用户插件目录后会立即加载。"
            "插件可通过 ctx 访问配置、API、共享和 RDP 服务，无需改动主程序。"
        )
        info.setWordWrap(True)
        info_layout.addWidget(info)
        layout.addWidget(info_card)
        layout.addStretch(1)

    @staticmethod
    def _metric(title: str) -> tuple[TitleLabel, QVBoxLayout]:
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(CaptionLabel(title))
        value = TitleLabel("--")
        box.addWidget(value)
        return value, box

    # ------------------------------------------------------------------
    def refresh_stats(self) -> None:
        try:
            stats = self.optimizer.stats()
        except Exception as exc:  # noqa: BLE001
            self.detail.setText(f"无法读取内存状态：{exc}")
            return
        self.used_value.setText(f"{human_size(stats['used'])} / {human_size(stats['total'])}")
        self.percent_value.setText(f"{stats['percent']}%")
        self.process_value.setText(str(stats["process_count"]))
        self.progress.setValue(int(stats["percent"]))
        self.detail.setText(
            f"可用内存 {human_size(stats['available'])} · 内存占用"
            f"{'偏高，建议优化' if stats['percent'] > 80 else '处于正常范围'}"
        )

    def _optimize(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self.optimize_button.setEnabled(False)
        self.optimize_button.setText("优化中...")
        self.result.hide()
        self._worker = FunctionWorker(self.optimizer.optimize, parent=self)
        self._worker.succeeded.connect(self._on_optimized)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._reset_button)
        self._worker.start()

    def _on_optimized(self, result: dict) -> None:
        self.refresh_stats()
        self.result.setText(
            f"优化完成：处理 {result['optimized']} 个进程，跳过 {result['skipped']} 个，"
            f"预计释放 {human_size(result['freed'])}，耗时 {result['elapsed']:.2f}s。"
        )
        self.result.show()
        self.ctx.notify("内存优化完成", "success")

    def _on_failed(self, message: str) -> None:
        self.result.setText(f"优化失败：{message}")
        self.result.show()
        self.ctx.notify(f"优化失败：{message}", "error")

    def _reset_button(self) -> None:
        self.optimize_button.setEnabled(True)
        self.optimize_button.setText("立即优化")


class MemoryOptimizerPlugin(ToolPlugin):
    plugin_id = "memory_optimizer"
    name = "内存优化"
    description = "安全回收进程工作集，释放闲置内存，适合长期开机的工作站。"
    version = "1.0.0"
    author = "NexHub"
    glyph = "◍"
    accent = "cyan"

    def create_widget(self, ctx, parent: QWidget | None = None) -> QWidget:
        return MemoryPluginWidget(ctx, parent)
