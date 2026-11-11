"""内存优化外部插件：独立进程 + 可选窗口（工作空间页内展示为主）。"""
from __future__ import annotations

import logging
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    ProgressBar,
    TitleLabel,
)

from nexhub.file_utils import human_size
from nexhub.services.memory_optimizer import MemoryOptimizer
from nexhub.widgets.common import make_button, make_card, section_header
from nexhub.workers import FunctionWorker


class Runtime:
    """窗口与控制器共享的“上次优化结果”，供工作空间页展示。"""

    def __init__(self) -> None:
        self.last_freed = 0
        self.last_at = ""
        self.optimized = 0
        self.skipped = 0
        self.elapsed = 0.0

    def update(self, result: dict) -> None:
        try:
            self.last_freed = int(result.get("freed") or 0)
            self.optimized = int(result.get("optimized") or 0)
            self.skipped = int(result.get("skipped") or 0)
            self.elapsed = float(result.get("elapsed") or 0.0)
        except (TypeError, ValueError):
            pass
        self.last_at = time.strftime("%H:%M:%S")


class MemoryWindow(QWidget):
    def __init__(self, context, runtime: Runtime | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.context = context
        self.runtime = runtime or Runtime()
        self.optimizer = MemoryOptimizer()
        self._worker: FunctionWorker | None = None
        self._build()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_stats)
        self.timer.start(5000)
        self.refresh_stats()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        card, card_layout = make_card(20, 12)
        header, actions = section_header("内存优化", "回收进程工作集，缓解 Windows 内存压力")
        actions.addWidget(CaptionLabel("外部插件 · 独立进程"))
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
        row = QHBoxLayout()
        self.optimize_button = make_button(
            "立即优化", primary=True, icon=FIF.BROOM, on_click=self._optimize
        )
        row.addWidget(self.optimize_button)
        row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh_stats)
        )
        row.addStretch(1)
        card_layout.addLayout(row)
        self.result = BodyLabel("")
        self.result.setWordWrap(True)
        self.result.hide()
        card_layout.addWidget(self.result)
        layout.addWidget(card)
        layout.addStretch(1)

    @staticmethod
    def _metric(title: str) -> tuple[TitleLabel, QVBoxLayout]:
        box = QVBoxLayout()
        box.setSpacing(3)
        box.addWidget(CaptionLabel(title))
        value = TitleLabel("--")
        box.addWidget(value)
        return value, box

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
            f"可用内存 {human_size(stats['available'])} · "
            f"{'占用偏高，建议优化' if stats['percent'] > 80 else '处于正常范围'}"
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
        self.runtime.update(result)
        self.refresh_stats()
        self.result.setText(
            f"优化完成：处理 {result['optimized']} 个进程，跳过 {result['skipped']} 个，"
            f"预计释放 {human_size(result['freed'])}，耗时 {result['elapsed']:.2f}s。"
        )
        self.result.show()
        self.context.log("memory optimize completed")

    def _on_failed(self, message: str) -> None:
        self.result.setText(f"优化失败：{message}")
        self.result.show()
        logging.getLogger(__name__).warning(message)

    def _reset_button(self) -> None:
        self.optimize_button.setEnabled(True)
        self.optimize_button.setText("立即优化")


class MemoryController:
    """供 NexHub 工作空间页直接调用的轻量控制器。"""

    def __init__(self, context, window: MemoryWindow):
        self.context = context
        self.window = window
        self.optimizer = window.optimizer
        self.runtime = window.runtime

    def state(self) -> dict:
        try:
            stats = self.optimizer.stats()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}
        return {
            "stats": stats,
            "percent": int(stats.get("percent") or 0),
            "used": int(stats.get("used") or 0),
            "total": int(stats.get("total") or 0),
            "available": int(stats.get("available") or 0),
            "process_count": int(stats.get("process_count") or 0),
            "last_freed": int(self.runtime.last_freed or 0),
            "last_optimized_at": self.runtime.last_at,
            "last_optimized": int(self.runtime.optimized or 0),
            "last_skipped": int(self.runtime.skipped or 0),
        }

    def invoke(self, action: str, payload: dict) -> dict:
        if action != "optimize":
            raise ValueError(f"不支持的操作：{action}")
        result = self.optimizer.optimize()
        self.runtime.update(result)
        self.window.refresh_stats()
        self.context.log("memory optimize completed from NexHub quick action")
        return {"result": result, "state": self.state()}


def create_controller(context, window):
    return MemoryController(context, window)


def create_window(context):
    return MemoryWindow(context)
