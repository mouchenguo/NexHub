"""我的共享：Fluent 发布对话框、列表管理与下载链接。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    ComboBox,
    FluentIcon as FIF,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    ProgressBar,
    RadioButton,
    SearchLineEdit,
    StrongBodyLabel,
    TitleLabel,
)

from ..file_utils import OSS_LIMIT_BYTES, collect_manifest, human_size, relative_time
from ..widgets.common import (
    Banner,
    EmptyState,
    ask_confirm,
    clear_layout,
    icon_widget,
    make_button,
    make_card,
    status_pill,
)
from ..workers import FunctionWorker, ShareCreateWorker
from .base import BasePage


class CreateShareDialog(QDialog):
    share_created = pyqtSignal(dict)

    def __init__(self, ctx, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._manifest: dict | None = None
        self._scan_worker: FunctionWorker | None = None
        self._create_worker: ShareCreateWorker | None = None
        self._scan_generation = 0
        self._done = False
        FluentStyleSheet.DIALOG.apply(self)
        self.setWindowTitle("NexHub · 发布共享")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(680)
        self._build()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("发布新共享"))
        layout.addWidget(
            BodyLabel("小于 5GB 可选择 OSS 长期缓存；超过 5GB 自动使用临时穿透。")
        )

        source_card, source_layout = make_card(18, 10)
        source_layout.addWidget(StrongBodyLabel("共享内容"))
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_edit = LineEdit()
        self.path_edit.setPlaceholderText("选择要共享的文件或文件夹")
        self.path_edit.editingFinished.connect(self._scan_path)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(
            make_button("选择文件", icon=FIF.DOCUMENT, on_click=self._browse_file)
        )
        path_row.addWidget(
            make_button("选择文件夹", icon=FIF.FOLDER, on_click=self._browse_folder)
        )
        source_layout.addLayout(path_row)
        self.scan_summary = CaptionLabel("尚未选择内容")
        source_layout.addWidget(self.scan_summary)
        layout.addWidget(source_card)

        layout.addWidget(StrongBodyLabel("存储方式"))
        options = QHBoxLayout()
        options.setSpacing(10)
        self.oss_radio = RadioButton()
        self.temp_radio = RadioButton()
        # 两个单选框分别位于不同 CardWidget 中，必须用 QButtonGroup
        # 才能保证全对话框内二选一。
        self.storage_group = QButtonGroup(self)
        self.storage_group.setExclusive(True)
        self.storage_group.addButton(self.oss_radio)
        self.storage_group.addButton(self.temp_radio)
        self.oss_card = self._option_card(
            self.oss_radio,
            FIF.CLOUD,
            "OSS 长期缓存",
            "上传至阿里云 OSS，共享长期有效。\n约产生外网流量费用，配额由管理员统一设置。",
        )
        self.temp_card = self._option_card(
            self.temp_radio,
            FIF.SYNC,
            "临时穿透共享",
            "不上传云端，通过 nps 从本机流式传输。\n关闭软件或取消共享后立即失效。",
        )
        self.temp_radio.setChecked(True)
        options.addWidget(self.oss_card, 1)
        options.addWidget(self.temp_card, 1)
        layout.addLayout(options)
        self.oss_radio.toggled.connect(self._sync_options)
        self.temp_radio.toggled.connect(self._sync_options)
        if not self.ctx.online:
            self.oss_radio.setEnabled(False)
            self.oss_card.setEnabled(False)
            self.oss_card.setToolTip("登录后可使用 OSS 长期缓存")
            offline_hint = CaptionLabel("演示预览模式仅支持临时穿透共享；登录后解锁 OSS 长期缓存。")
            offline_hint.setWordWrap(True)
            layout.addWidget(offline_hint)

        self.limit_warning = Banner("", FIF.INFO)
        self.limit_warning.hide()
        layout.addWidget(self.limit_warning)

        self.description_edit = LineEdit()
        self.description_edit.setPlaceholderText("共享说明（可选，将展示在共享广场）")
        layout.addWidget(self.description_edit)

        self.progress = ProgressBar()
        self.progress.setRange(0, 100)
        self.progress.hide()
        self.progress_text = CaptionLabel("")
        self.progress_text.hide()
        layout.addWidget(self.progress)
        layout.addWidget(self.progress_text)

        self.result_area, result_layout = make_card(16, 8)
        self.result_label = BodyLabel("")
        self.result_label.setWordWrap(True)
        result_layout.addWidget(self.result_label)
        link_row = QHBoxLayout()
        self.link_edit = LineEdit()
        self.link_edit.setReadOnly(True)
        self.copy_button = make_button(
            "复制链接", icon=FIF.COPY, on_click=self._copy_link
        )
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.copy_button)
        result_layout.addLayout(link_row)
        self.result_area.hide()
        layout.addWidget(self.result_area)
        layout.addStretch(1)

        footer = QHBoxLayout()
        footer.addWidget(CaptionLabel("客户端不会保存 OSS 密钥，上传由 ECS 后端代理。"))
        footer.addStretch(1)
        self.cancel_button = make_button("取消", on_click=self.reject)
        self.publish_button = make_button(
            "发布共享", primary=True, icon=FIF.SHARE, on_click=self._publish
        )
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.publish_button)
        layout.addLayout(footer)

    def _option_card(
        self, radio: RadioButton, icon: FIF, title: str, description: str
    ) -> CardWidget:
        card = CardWidget()
        card.setBorderRadius(10)
        card.setClickEnabled(True)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        layout.addWidget(radio, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(icon_widget(icon, 24), 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        text_box.addWidget(StrongBodyLabel(title))
        desc = CaptionLabel(description)
        desc.setWordWrap(True)
        text_box.addWidget(desc)
        layout.addLayout(text_box, 1)
        card.clicked.connect(lambda: radio.setChecked(True))
        return card

    # ------------------------------------------------------------------
    def _browse_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择要共享的文件")
        if path:
            self.path_edit.setText(path)
            self._scan_path()

    def _browse_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要共享的文件夹")
        if path:
            self.path_edit.setText(path)
            self._scan_path()

    def _scan_path(self) -> None:
        raw = self.path_edit.text().strip()
        if not raw:
            self._manifest = None
            self.scan_summary.setText("尚未选择内容")
            self.publish_button.setEnabled(False)
            return
        path = Path(raw).expanduser()
        if not path.exists():
            self._manifest = None
            self.scan_summary.setText("路径不存在，请重新选择")
            self.publish_button.setEnabled(False)
            return
        self._scan_generation += 1
        generation = self._scan_generation
        self._manifest = None
        self.publish_button.setEnabled(False)
        self.scan_summary.setText("正在扫描文件，请稍候...")
        self._scan_worker = FunctionWorker(collect_manifest, str(path), parent=self)
        self._scan_worker.succeeded.connect(
            lambda manifest, gen=generation: self._on_scanned(manifest, gen)
        )
        self._scan_worker.failed.connect(self._on_scan_failed)
        self._scan_worker.start()

    def _on_scanned(self, manifest: dict, generation: int) -> None:
        if generation != self._scan_generation:
            return
        if not manifest.get("files"):
            self._manifest = None
            self.scan_summary.setText("该路径下没有可共享的文件")
            self.publish_button.setEnabled(False)
            return
        self._manifest = manifest
        total = int(manifest["total_size"])
        self.scan_summary.setText(
            f"{manifest['name']}  ·  {manifest['file_count']} 个文件  ·  {human_size(total)}"
        )
        limit_exceeded = total >= OSS_LIMIT_BYTES
        oss_allowed = self.ctx.online and not limit_exceeded
        self.oss_radio.setEnabled(oss_allowed)
        self.oss_card.setEnabled(oss_allowed)
        if limit_exceeded:
            self.temp_radio.setChecked(True)
            self.limit_warning.label.setText(
                f"文件总大小为 {human_size(total)}，已达到 5GB 上限，"
                "仅支持临时穿透共享，OSS 长期缓存不可用。"
            )
            self.limit_warning.show()
        elif not self.ctx.online:
            self.temp_radio.setChecked(True)
            self.limit_warning.hide()
        else:
            self.limit_warning.hide()
        self._sync_options()
        self.publish_button.setEnabled(True)

    def _on_scan_failed(self, message: str) -> None:
        self._manifest = None
        self.scan_summary.setText(f"扫描失败：{message}")
        self.publish_button.setEnabled(False)

    def _sync_options(self) -> None:
        # RadioButton 本身表达选中状态，卡片保持 Fluent 原生样式
        if not self.oss_radio.isEnabled():
            self.temp_radio.setChecked(True)

    # ------------------------------------------------------------------
    def _publish(self) -> None:
        if self._create_worker and self._create_worker.isRunning():
            return
        if not self._manifest:
            InfoBar.warning(
                "尚未准备完成",
                "请先选择并扫描共享内容",
                parent=self,
                position=InfoBarPosition.TOP,
                duration=2200,
            )
            return
        if self.oss_radio.isChecked() and not self.ctx.online:
            InfoBar.warning(
                "需要登录",
                "演示预览模式不支持 OSS 长期缓存，请先登录或改用临时穿透共享",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        if self.oss_radio.isChecked() and self._manifest["total_size"] >= OSS_LIMIT_BYTES:
            InfoBar.warning(
                "文件过大",
                "文件超过 5GB，请选择临时穿透共享",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return

        storage = "oss" if self.oss_radio.isChecked() else "temp"
        self._set_busy(True)
        self.progress.setValue(0)
        self.progress.show()
        self.progress_text.show()
        self.progress_text.setText("正在准备...")
        self._create_worker = ShareCreateWorker(
            self.ctx.shares,
            self.path_edit.text().strip(),
            storage,
            self.description_edit.text().strip(),
            parent=self,
        )
        self._create_worker.progress_changed.connect(self._on_progress)
        self._create_worker.succeeded.connect(self._on_created)
        self._create_worker.failed.connect(self._on_create_failed)
        self._create_worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(percent)
        self.progress_text.setText(message)

    def _on_created(self, share: dict) -> None:
        self._done = True
        self.progress.hide()
        self.progress_text.hide()
        self.result_area.show()
        self.adjustSize()
        kind = "OSS 长期缓存" if share.get("storage_type") == "oss" else "临时穿透"
        link = self.ctx.shares.public_link(share)
        self.copy_button.setVisible(bool(link))
        if link:
            self.link_edit.setText(link)
            self.result_label.setText(f"共享「{share.get('name', '')}」已发布，存储方式：{kind}。")
        else:
            self.link_edit.setText("")
            self.link_edit.setPlaceholderText("离线体验模式暂不生成公网链接")
            self.result_label.setText(
                f"共享「{share.get('name', '')}」已保存到本地演示数据。"
                "接入 ECS 后可在共享广场中生成网页链接。"
            )
        self.oss_radio.setEnabled(False)
        self.temp_radio.setEnabled(False)
        self.publish_button.hide()
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("完成")
        try:
            self.cancel_button.clicked.disconnect()
        except TypeError:
            pass
        self.cancel_button.clicked.connect(self.accept)
        InfoBar.success(
            "发布成功",
            f"共享「{share.get('name', '')}」已发布",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=2200,
        )
        self.share_created.emit(share)
        if self.ctx.main_window:
            self.ctx.main_window.refresh_shell()

    def _on_create_failed(self, message: str) -> None:
        self._set_busy(False)
        self.progress.hide()
        self.progress_text.hide()
        InfoBar.error(
            "发布失败",
            message,
            parent=self,
            position=InfoBarPosition.TOP,
            duration=4200,
        )

    def _set_busy(self, busy: bool) -> None:
        self.path_edit.setEnabled(not busy)
        self.oss_radio.setEnabled(
            not busy
            and self.ctx.online
            and self._manifest is not None
            and self._manifest["total_size"] < OSS_LIMIT_BYTES
        )
        self.oss_card.setEnabled(self.oss_radio.isEnabled())
        self.temp_radio.setEnabled(not busy)
        self.description_edit.setEnabled(not busy)
        self.publish_button.setEnabled(not busy)
        self.cancel_button.setEnabled(not busy)

    def _copy_link(self) -> None:
        link = self.link_edit.text().strip()
        if not link:
            self.ctx.notify("当前离线体验模式没有可复制的公网链接", "warning")
            return
        from PyQt6.QtWidgets import QApplication

        QApplication.clipboard().setText(link)
        self.ctx.notify("链接已复制到剪贴板", "success")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._create_worker and self._create_worker.isRunning():
            InfoBar.warning(
                "正在发布",
                "共享正在发布中，请等待完成后再关闭窗口。",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            event.ignore()
            return
        super().closeEvent(event)


class MySharesPage(BasePage):
    page_id = "my_shares"
    title = "我的共享"
    subtitle = "管理你发布的长期缓存与临时穿透共享"

    def __init__(self, ctx, parent=None):
        self._shares: list[dict] = []
        self._worker: FunctionWorker | None = None
        self._action_worker: FunctionWorker | None = None
        super().__init__(ctx, parent)

    def build(self) -> None:
        toolbar, toolbar_layout = make_card(14, 8)
        row = QHBoxLayout()
        row.setSpacing(9)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索共享名称或发布者...")
        self.search.setFixedWidth(280)
        self.search.textChanged.connect(self._render)
        row.addWidget(self.search)

        self.type_filter = ComboBox()
        self.type_filter.addItems(["全部类型", "OSS 长期缓存", "临时穿透"])
        self.type_filter.setFixedWidth(150)
        row.addWidget(self.type_filter)

        self.status_filter = ComboBox()
        self.status_filter.addItems(["全部状态", "活跃", "已取消"])
        self.status_filter.setFixedWidth(120)
        row.addWidget(self.status_filter)
        row.addStretch(1)
        row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        row.addWidget(
            make_button(
                "新建共享", primary=True, icon=FIF.ADD, on_click=self._open_create
            )
        )
        toolbar_layout.addLayout(row)
        self.content_layout.addWidget(toolbar)

        self.summary = CaptionLabel("正在载入共享...")
        self.content_layout.addWidget(self.summary)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.content_layout.addWidget(self.list_host)

        self.empty = EmptyState(
            "还没有共享",
            "发布文件或文件夹后，好友可在共享广场浏览与选择性下载。",
            FIF.SHARE,
            make_button(
                "发布第一个共享",
                primary=True,
                icon=FIF.ADD,
                on_click=self._open_create,
            ),
        )
        self.empty.hide()
        self.content_layout.addWidget(self.empty)
        self.content_layout.addStretch(1)

        self.type_filter.currentIndexChanged.connect(self._render)
        self.status_filter.currentIndexChanged.connect(self._render)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self.ctx.shares.list_my_shares, parent=self)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, shares: list[dict]) -> None:
        self._shares = shares
        self._render()

    def _on_error(self, message: str) -> None:
        self.summary.setText(f"共享列表加载失败：{message}")
        self.ctx.notify(f"共享列表加载失败：{message}", "error")

    def _render(self) -> None:
        clear_layout(self.list_layout)
        query = self.search.text().strip().lower()
        type_index = self.type_filter.currentIndex()
        status_index = self.status_filter.currentIndex()
        filtered = []
        for share in self._shares:
            if (
                query
                and query not in share.get("name", "").lower()
                and query not in share.get("owner", "").lower()
            ):
                continue
            if type_index == 1 and share.get("storage_type") != "oss":
                continue
            if type_index == 2 and share.get("storage_type") == "oss":
                continue
            if status_index == 1 and share.get("status") != "active":
                continue
            if status_index == 2 and share.get("status") == "active":
                continue
            filtered.append(share)

        active = sum(1 for share in self._shares if share.get("status") == "active")
        self.summary.setText(
            f"共 {len(self._shares)} 个共享 · {active} 个活跃 · 显示 {len(filtered)} 个"
        )
        if not filtered:
            if not self._shares:
                self.empty.show()
            else:
                self.empty.hide()
                self.list_layout.addWidget(CaptionLabel("没有符合筛选条件的共享"))
            return
        self.empty.hide()
        for share in filtered:
            self.list_layout.addWidget(self._row(share))

    def _row(self, share: dict) -> QWidget:
        frame, layout = make_card(18, 8)
        row = QHBoxLayout()
        row.setSpacing(12)
        is_oss = share.get("storage_type") == "oss"
        row.addWidget(icon_widget(FIF.CLOUD if is_oss else FIF.SYNC, 28))

        info = QVBoxLayout()
        info.setSpacing(3)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        title_row.addWidget(StrongBodyLabel(share.get("name", "未命名共享")))
        active = share.get("status") == "active"
        title_row.addWidget(
            status_pill("活跃" if active else "已取消", FIF.ACCEPT if active else FIF.CANCEL)
        )
        title_row.addWidget(
            status_pill(
                "OSS 长期" if is_oss else "临时穿透",
                FIF.CLOUD if is_oss else FIF.SYNC,
            )
        )
        title_row.addStretch(1)
        info.addLayout(title_row)
        if share.get("description"):
            info.addWidget(BodyLabel(share["description"]))
        info.addWidget(
            CaptionLabel(
                f"{share.get('file_count', 0)} 个文件 · {human_size(share.get('total_size', 0))}"
                f" · 下载 {share.get('downloads', 0)} 次"
                f" · {relative_time(share.get('created_at'))}"
            )
        )
        row.addLayout(info, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        copy_button = make_button(
            "复制链接", transparent=True, icon=FIF.COPY, on_click=lambda: self._copy(share)
        )
        if not self.ctx.shares.public_link(share):
            copy_button.setEnabled(False)
            copy_button.setToolTip("离线体验模式不生成公网链接")
        actions.addWidget(copy_button)
        if active:
            actions.addWidget(
                make_button("取消共享", icon=FIF.CANCEL, on_click=lambda: self._cancel(share))
            )
        actions.addWidget(
            make_button("删除", icon=FIF.DELETE, on_click=lambda: self._delete(share))
        )
        row.addLayout(actions)
        layout.addLayout(row)
        return frame

    # ------------------------------------------------------------------
    def _open_create(self) -> None:
        dialog = CreateShareDialog(self.ctx, self)
        dialog.share_created.connect(lambda share: self.refresh())
        dialog.exec()
        self.refresh()

    def _copy(self, share: dict) -> None:
        link = self.ctx.shares.public_link(share)
        if not link:
            self.ctx.notify("离线体验模式没有公网链接", "warning")
            return
        QApplication.clipboard().setText(link)
        self.ctx.notify("共享链接已复制", "success")

    def _cancel(self, share: dict) -> None:
        if not ask_confirm(
            self,
            "取消共享",
            f"确定取消共享「{share.get('name')}」？\n取消后链接立即失效，临时穿透服务将停止。",
        ):
            return
        self._run_action(self.ctx.shares.cancel_share, share, "共享已取消")

    def _delete(self, share: dict) -> None:
        if not ask_confirm(
            self,
            "删除共享",
            f"确定永久删除共享「{share.get('name')}」？\nOSS 缓存与访问记录会一并清理，此操作不可恢复。",
        ):
            return
        self._run_action(self.ctx.shares.delete_share, share, "共享已删除")

    def _run_action(self, fn, share: dict, success_message: str) -> None:
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(fn, share, parent=self)
        self._action_worker.succeeded.connect(
            lambda result: (self.ctx.notify(success_message, "success"), self.refresh())
        )
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"操作失败：{message}", "error")
        )
        self._action_worker.start()
