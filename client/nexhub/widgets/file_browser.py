"""共享 / 远程主机文件浏览器（Fluent 组件）。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ProgressBar,
    StrongBodyLabel,
    TitleLabel,
    TreeWidget,
)

from ..file_utils import human_size, manifest_tree
from ..workers import DownloadWorker
from .common import make_button, status_pill

PATH_ROLE = Qt.ItemDataRole.UserRole
SIZE_ROLE = Qt.ItemDataRole.UserRole + 1
DIR_ROLE = Qt.ItemDataRole.UserRole + 2


class FileBrowserDialog(QDialog):
    download_completed = pyqtSignal()

    def __init__(
        self,
        ctx,
        *,
        title: str,
        subtitle: str,
        manifest: dict,
        job_factory: Callable[[list[dict]], tuple[list, list[str]]],
        storage_label: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.ctx = ctx
        self.job_factory = job_factory
        self.manifest = manifest
        self._updating = False
        self._worker: DownloadWorker | None = None
        self._title = title
        self._subtitle = subtitle
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle(f"NexHub · {title}")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.resize(900, 700)
        self._build(storage_label)

    # ------------------------------------------------------------------
    def _build(self, storage_label: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 18, 24, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        header.setSpacing(10)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(TitleLabel(self._title))
        text_box.addWidget(BodyLabel(self._subtitle))
        header.addLayout(text_box)
        header.addStretch(1)
        if storage_label:
            header.addWidget(status_pill(storage_label, FIF.CLOUD))
        header.addWidget(
            status_pill(f"{self.manifest.get('file_count', 0)} 个文件", FIF.FOLDER)
        )
        header.addWidget(
            status_pill(f"共 {human_size(self.manifest.get('total_size', 0))}", FIF.INFO)
        )
        layout.addLayout(header)

        self.tree = TreeWidget()
        self.tree.setHeaderLabels(["名称", "大小"])
        self.tree.setColumnWidth(0, 620)
        self.tree.setIndentation(18)
        self.tree.setBorderVisible(False)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        selection_row = QHBoxLayout()
        selection_row.setSpacing(6)
        self.select_all_button = make_button("全选", icon=FIF.ACCEPT)
        self.select_all_button.clicked.connect(lambda: self._set_all(True))
        self.clear_button = make_button("取消全选", icon=FIF.CANCEL)
        self.clear_button.clicked.connect(lambda: self._set_all(False))
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_button)
        selection_row.addStretch(1)
        self.selection_label = CaptionLabel("已选择 0 个文件 · 0 B")
        selection_row.addWidget(self.selection_label)
        layout.addLayout(selection_row)

        target_row = QHBoxLayout()
        target_row.setSpacing(8)
        target_row.addWidget(StrongBodyLabel("保存到"))
        self.target_edit = LineEdit()
        self.target_edit.setText(str(self.ctx.config.download_path))
        target_row.addWidget(self.target_edit, 1)
        target_row.addWidget(
            make_button("浏览...", icon=FIF.FOLDER, on_click=self._choose_folder)
        )
        layout.addLayout(target_row)

        self.status = CaptionLabel("勾选需要下载的文件，可自定义保存位置。")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        layout.addWidget(self.progress)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self.close_button = make_button("关闭", on_click=self.reject)
        self.download_button = make_button(
            "下载选中文件", primary=True, icon=FIF.DOWNLOAD, on_click=self._start_download
        )
        footer.addWidget(self.close_button)
        footer.addWidget(self.download_button)
        layout.addLayout(footer)
        self._populate()

    # ------------------------------------------------------------------
    def _populate(self) -> None:
        self.tree.clear()
        root = manifest_tree(self.manifest.get("files", []))
        root_name = self.manifest.get("name") or self._title
        root_item = QTreeWidgetItem([root_name, human_size(root.get("size", 0))])
        root_item.setData(0, DIR_ROLE, True)
        root_item.setFlags(
            root_item.flags()
            | Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsAutoTristate
        )
        self.tree.addTopLevelItem(root_item)
        self._add_children(root_item, root)
        root_item.setExpanded(True)
        self.tree.headerItem().setTextAlignment(
            1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._update_summary()

    def _add_children(self, parent_item: QTreeWidgetItem, node: dict) -> None:
        for name in sorted(node.get("children", {})):
            child = node["children"][name]
            item = QTreeWidgetItem([name, human_size(child.get("size", 0))])
            item.setData(0, DIR_ROLE, True)
            item.setToolTip(0, name)
            item.setFlags(
                item.flags()
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsAutoTristate
            )
            parent_item.addChild(item)
            self._add_children(item, child)
            item.setExpanded(True)
        for file in sorted(node.get("files", []), key=lambda f: f["name"].lower()):
            item = QTreeWidgetItem([file["name"], human_size(file.get("size", 0))])
            item.setData(0, PATH_ROLE, file["path"])
            item.setData(0, SIZE_ROLE, int(file.get("size") or 0))
            item.setData(0, DIR_ROLE, False)
            item.setToolTip(0, file["path"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            parent_item.addChild(item)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        self._updating = True
        try:
            if item.data(0, DIR_ROLE):
                state = item.checkState(0)
                for child in self._iter_children(item):
                    if child.checkState(0) != state:
                        child.setCheckState(0, state)
            self._refresh_parent_states(item)
        finally:
            self._updating = False
        self._update_summary()

    @staticmethod
    def _iter_children(item: QTreeWidgetItem):
        for index in range(item.childCount()):
            child = item.child(index)
            yield child
            yield from FileBrowserDialog._iter_children(child)

    def _refresh_parent_states(self, item: QTreeWidgetItem) -> None:
        parent = item.parent()
        while parent:
            checked = 0
            partial = 0
            for index in range(parent.childCount()):
                state = parent.child(index).checkState(0)
                if state == Qt.CheckState.Checked:
                    checked += 1
                elif state == Qt.CheckState.PartiallyChecked:
                    partial += 1
            if checked == parent.childCount():
                parent.setCheckState(0, Qt.CheckState.Checked)
            elif checked == 0 and partial == 0:
                parent.setCheckState(0, Qt.CheckState.Unchecked)
            else:
                parent.setCheckState(0, Qt.CheckState.PartiallyChecked)
            parent = parent.parent()

    def _set_all(self, checked: bool) -> None:
        root = self.tree.topLevelItem(0)
        if not root:
            return
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self._updating = True
        try:
            root.setCheckState(0, state)
            for item in self._iter_children(root):
                item.setCheckState(0, state)
        finally:
            self._updating = False
        self._update_summary()

    def _selected_files(self) -> list[dict]:
        files: list[dict] = []
        root = self.tree.topLevelItem(0)
        if not root:
            return files
        for item in self._iter_children(root):
            if item.data(0, DIR_ROLE):
                continue
            if item.checkState(0) == Qt.CheckState.Checked:
                files.append(
                    {
                        "path": item.data(0, PATH_ROLE),
                        "size": int(item.data(0, SIZE_ROLE) or 0),
                    }
                )
        return files

    def _update_summary(self) -> None:
        files = self._selected_files()
        size = sum(item["size"] for item in files)
        self.selection_label.setText(f"已选择 {len(files)} 个文件 · {human_size(size)}")
        if self._worker and self._worker.isRunning():
            return
        self.download_button.setEnabled(bool(files))

    def _choose_folder(self) -> None:
        current = self.target_edit.text().strip() or str(self.ctx.config.download_path)
        folder = QFileDialog.getExistingDirectory(self, "选择下载保存目录", current)
        if folder:
            self.target_edit.setText(folder)

    # ------------------------------------------------------------------
    def _start_download(self) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.download_button.setText("正在取消...")
            self.status.setText("正在取消下载任务...")
            return

        files = self._selected_files()
        if not files:
            self.status.setText("请先勾选需要下载的文件")
            return
        target = Path(self.target_edit.text().strip() or self.ctx.config.download_path)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            InfoBar.error("无法创建目录", str(exc), parent=self, position=InfoBarPosition.TOP)
            return

        try:
            jobs, skipped = self.job_factory(files)
        except Exception as exc:  # noqa: BLE001
            InfoBar.error("准备下载失败", str(exc), parent=self, position=InfoBarPosition.TOP)
            return
        if not jobs:
            InfoBar.error(
                "没有可下载的文件",
                "文件可能已被取消共享。",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        self.ctx.config.download_dir = str(target)
        self.ctx.config.save()
        self.progress.setValue(0)
        self.progress.show()
        self.close_button.setEnabled(False)
        self.download_button.setText("取消下载")
        self.select_all_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.status.setText(
            f"正在下载 {len(jobs)} 个文件"
            + (f"（{len(skipped)} 个文件不可用）" if skipped else "")
        )

        self._worker = DownloadWorker(jobs, str(target), parent=self)
        self._worker.progress_changed.connect(self._on_progress)
        self._worker.file_completed.connect(
            lambda path: self.status.setText(f"已保存：{path}")
        )
        self._worker.completed.connect(self._on_completed)
        self._worker.start()

    def _on_progress(self, done: int, total: int, current: str) -> None:
        self.progress.setValue(int(done / max(1, total) * 100))
        if current:
            name = current if len(current) <= 60 else "..." + current[-57:]
            self.status.setText(f"{human_size(done)} / {human_size(total)} · {name}")

    def _on_completed(self, errors: list) -> None:
        self.progress.setValue(100)
        self.close_button.setEnabled(True)
        self.download_button.setText("下载选中文件")
        self.select_all_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        if errors:
            self.status.setText(f"下载完成，但有 {len(errors)} 个文件失败。")
            InfoBar.error(
                "部分文件下载失败",
                errors[0],
                parent=self,
                position=InfoBarPosition.TOP,
                duration=4200,
            )
        else:
            self.status.setText(f"下载完成，文件已保存到 {self.target_edit.text()}")
            InfoBar.success(
                "下载完成",
                "文件已保存到指定目录。",
                parent=self,
                position=InfoBarPosition.TOP,
            )
        self.download_completed.emit()

    def closeEvent(self, event) -> None:
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self.status.setText("下载任务仍在退出，请稍候再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)
