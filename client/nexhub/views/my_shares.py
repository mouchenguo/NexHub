"""我的网盘：Fluent 共享对话框、网盘文件列表与下载链接。"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QDesktopServices
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
    CheckBox,
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
from ..workers import DriveUploadWorker, FunctionWorker, ShareCreateWorker
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
        self.setWindowTitle("NexHub · 共享文件")
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(680)
        self._build()
        self.adjustSize()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(TitleLabel("共享文件"))
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

        self.mirror_check = CheckBox("同时上传到我的网盘")
        self.mirror_check.setChecked(True)
        layout.addWidget(self.mirror_check)
        self.mirror_hint = CaptionLabel("")
        self.mirror_hint.setWordWrap(True)
        layout.addWidget(self.mirror_hint)

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
        self._sync_options()

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
            "开始共享", primary=True, icon=FIF.SHARE, on_click=self._publish
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
        is_temp = not self.oss_radio.isChecked()
        self.mirror_check.setEnabled(is_temp and self.ctx.online)
        if not is_temp:
            # OSS 长期共享本身就是网盘对象，复用同一次上传，避免重复流量
            self.mirror_check.setChecked(False)
            self.mirror_hint.setText(
                "OSS 长期共享会直接出现在我的网盘，只上传一次，不会重复占用流量。"
            )
        elif not self.ctx.online:
            self.mirror_hint.setText("离线体验模式无法同步到网盘。")
        else:
            self.mirror_hint.setText(
                "临时穿透不落云端；勾选后会额外上传一份到我的网盘，网页端可随时下载。"
            )

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
            mirror_to_drive=self.mirror_check.isChecked() and storage == "temp",
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
    title = "我的网盘"
    subtitle = "上传文件到网盘（OSS），随时共享；临时穿透可勾选同步进网盘"

    def __init__(self, ctx, parent=None):
        self._shares: list[dict] = []
        self._drive: dict = {}
        self.compact = str(getattr(ctx.config, "drive_density", "comfortable")) == "compact"
        self._worker: FunctionWorker | None = None
        self._drive_worker: FunctionWorker | None = None
        self._upload_worker = None
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
        self.density_button = make_button(
            "", transparent=True, icon=FIF.ZOOM, on_click=self._toggle_density
        )
        row.addWidget(self.density_button)
        row.addWidget(
            make_button(
                "网页网盘",
                transparent=True,
                icon=FIF.LINK,
                on_click=self._open_web_drive,
            )
        )
        row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        row.addWidget(
            make_button(
                "上传文件至网盘",
                icon=FIF.UP,
                on_click=self._upload_to_drive,
            )
        )
        row.addWidget(
            make_button(
                "共享文件", primary=True, icon=FIF.SHARE, on_click=self._open_create
            )
        )
        toolbar_layout.addLayout(row)
        self.content_layout.addWidget(toolbar)

        drive_card, drive_layout = make_card()
        drive_header = QHBoxLayout()
        drive_header.setSpacing(8)
        drive_header.addWidget(StrongBodyLabel("网盘文件"))
        drive_header.addWidget(CaptionLabel("存在服务器 OSS，可随时共享或删除"))
        drive_header.addStretch(1)
        self.drive_usage = CaptionLabel("正在载入网盘...")
        drive_header.addWidget(self.drive_usage)
        drive_layout.addLayout(drive_header)

        self.drive_host = QWidget()
        self.drive_layout = QVBoxLayout(self.drive_host)
        self.drive_layout.setContentsMargins(0, 0, 0, 0)
        self.drive_layout.setSpacing(4)
        drive_layout.addWidget(self.drive_host)
        self.content_layout.addWidget(drive_card)

        self.summary = CaptionLabel("正在载入共享...")
        self.content_layout.addWidget(self.summary)

        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(10)
        self.content_layout.addWidget(self.list_host)

        self.empty = EmptyState(
            "还没有共享",
            "在「上传文件至网盘」把文件存到云上，或用「共享文件」发布临时/长期共享。",
            FIF.SHARE,
            make_button(
                "共享文件",
                primary=True,
                icon=FIF.SHARE,
                on_click=self._open_create,
            ),
        )
        self.empty.hide()
        self.content_layout.addWidget(self.empty)
        self.content_layout.addStretch(1)

        self.type_filter.currentIndexChanged.connect(self._render)
        self.status_filter.currentIndexChanged.connect(self._render)
        self._sync_density_button()
        self._apply_density_layout()

    # ------------------------------------------------------------------
    def _toggle_density(self) -> None:
        self.compact = not self.compact
        self.ctx.config.drive_density = "compact" if self.compact else "comfortable"
        try:
            self.ctx.config.save()
        except OSError:
            pass
        self._sync_density_button()
        self._apply_density_layout()
        self._render_drive()
        self._render()
        self.ctx.notify("已切换为紧凑列表" if self.compact else "已切换为宽松卡片", "success")

    def _apply_density_layout(self) -> None:
        self.list_layout.setSpacing(2 if self.compact else 10)
        self.drive_layout.setSpacing(2 if self.compact else 4)

    def _sync_density_button(self) -> None:
        text = "紧凑视图" if not self.compact else "宽松视图"
        self.density_button.setText(text)
        self.density_button.setToolTip(
            "切换网盘与共享列表的排版（紧凑列表 / 宽松卡片，选择会记住）"
        )

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._load_drive()
        self._worker = FunctionWorker(self.ctx.shares.list_my_shares, parent=self)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    # ------------------------------------------------------------------ 网盘
    def _load_drive(self) -> None:
        if not self.ctx.online:
            self.drive_usage.setText("离线体验模式不支持网盘")
            return
        if self._drive_worker and self._drive_worker.isRunning():
            return
        self._drive_worker = FunctionWorker(self.ctx.api.drive_list, parent=self)
        self._drive_worker.succeeded.connect(self._on_drive_loaded)
        self._drive_worker.failed.connect(
            lambda message: self.drive_usage.setText(f"网盘载入失败：{message}")
        )
        self._drive_worker.start()

    def _on_drive_loaded(self, payload: dict) -> None:
        self._drive = payload if isinstance(payload, dict) else {}
        self._render_drive()

    def _render_drive(self) -> None:
        clear_layout(self.drive_layout)
        items = [item for item in (self._drive.get("items") or []) if isinstance(item, dict)]
        used = int(self._drive.get("used") or 0)
        quota = int(self._drive.get("quota") or 0)
        self.drive_usage.setText(
            f"{len(items)} 个文件 · 已用 {human_size(used)} / {human_size(quota)}"
        )
        if not items:
            self.drive_layout.addWidget(CaptionLabel("网盘还是空的，点右上角「上传文件至网盘」开始。"))
            return
        margins = (0, 1, 0, 1) if self.compact else (0, 4, 0, 4)
        spacing = 4 if self.compact else 10
        for item in items[:200]:
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(*margins)
            layout.setSpacing(spacing)
            if not self.compact:
                layout.addWidget(icon_widget(FIF.DOCUMENT, 18))
            name = CaptionLabel(str(item.get("name") or ""))
            name.setMinimumWidth(160 if self.compact else 220)
            layout.addWidget(name, 1)
            layout.addWidget(CaptionLabel(human_size(int(item.get("size") or 0))))
            layout.addWidget(CaptionLabel(relative_time(item.get("created_at"))))
            share_button = make_button(
                "共享",
                transparent=True,
                icon=FIF.SHARE,
                on_click=lambda _=None, data=item: self._share_drive_item(data),
            )
            delete_button = make_button(
                "删除",
                transparent=True,
                icon=FIF.DELETE,
                on_click=lambda _=None, data=item: self._delete_drive_item(data),
            )
            if self.compact:
                for button, hint in ((share_button, "共享这个文件"), (delete_button, "删除")):
                    button.setText("")
                    button.setToolTip(hint)
                    button.setFixedSize(30, 26)
            layout.addWidget(share_button)
            layout.addWidget(delete_button)
            self.drive_layout.addWidget(row)

    def _upload_to_drive(self) -> None:
        if not self.ctx.online:
            self.ctx.notify("离线体验模式不支持上传网盘", "warning")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "选择要上传到网盘的文件")
        if not paths:
            return
        files: list[tuple[str, str]] = []
        for raw in paths:
            path = Path(raw)
            files.append((str(path), path.name))
        self._upload_worker = DriveUploadWorker(self.ctx.api, files, parent=self)
        self._upload_worker.progress_changed.connect(self._on_upload_progress)
        self._upload_worker.succeeded.connect(self._on_upload_done)
        self._upload_worker.failed.connect(self._on_upload_failed)
        self.drive_usage.setText("正在上传到网盘...")
        self._upload_worker.start()

    def _on_upload_progress(self, percent: int, message: str) -> None:
        self.drive_usage.setText(f"{message}（{percent}%）")

    def _on_upload_done(self, result: dict) -> None:
        count = int((result or {}).get("count") or 0)
        self.ctx.notify(f"已上传 {count} 个文件到网盘", "success")
        self._load_drive()

    def _on_upload_failed(self, message: str) -> None:
        self.drive_usage.setText("网盘上传失败")
        self.ctx.notify(f"上传网盘失败：{message}", "error")

    def _share_drive_item(self, item: dict) -> None:
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(
            self.ctx.api.share_drive_file,
            int(item.get("id") or 0),
            "",
            "",
            parent=self,
        )
        self._action_worker.succeeded.connect(self._on_drive_shared)
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"创建共享失败：{message}", "error")
        )
        self._action_worker.start()

    def _on_drive_shared(self, share: dict) -> None:
        link = self.ctx.shares.public_link(share)
        QApplication.clipboard().setText(link)
        self.ctx.notify(
            f"共享已创建，链接已复制：{link}" if link else "共享已创建", "success"
        )
        self.refresh()

    def _delete_drive_item(self, item: dict) -> None:
        if not ask_confirm(self, f"从网盘删除「{item.get('name', '')}」？引用它的共享会被取消。"):
            return
        if self._action_worker and self._action_worker.isRunning():
            return
        self._action_worker = FunctionWorker(
            self.ctx.api.delete_drive_file, int(item.get("id") or 0), parent=self
        )
        self._action_worker.succeeded.connect(self._on_drive_deleted)
        self._action_worker.failed.connect(
            lambda message: self.ctx.notify(f"删除失败：{message}", "error")
        )
        self._action_worker.start()

    def _on_drive_deleted(self, _result) -> None:
        self.ctx.notify("已从网盘删除", "success")
        self.refresh()

    def _open_web_drive(self) -> None:
        base = (self.ctx.config.server_url or "").rstrip("/")
        if not base:
            self.ctx.notify("请先在「设置」里填写服务器地址", "warning")
            return
        QDesktopServices.openUrl(QUrl(f"{base}/drive"))

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
            self.list_layout.addWidget(
                self._row_compact(share) if self.compact else self._row(share)
            )

    def _row_compact(self, share: dict) -> QWidget:
        """紧凑模式：单行显示，操作按钮改为图标按钮。"""
        is_oss = share.get("storage_type") == "oss"
        active = share.get("status") == "active"
        frame = QWidget()
        row = QHBoxLayout(frame)
        row.setContentsMargins(0, 1, 0, 1)
        row.setSpacing(6)
        row.addWidget(icon_widget(FIF.CLOUD if is_oss else FIF.SYNC, 16))
        name = CaptionLabel(str(share.get("name") or "未命名共享"))
        name.setMinimumWidth(180)
        row.addWidget(name, 1)
        row.addWidget(CaptionLabel("OSS" if is_oss else "临时"))
        row.addWidget(CaptionLabel("活跃" if active else "已取消"))
        row.addWidget(
            CaptionLabel(
                f"{share.get('file_count', 0)} 个 · {human_size(share.get('total_size', 0))}"
                f" · {relative_time(share.get('created_at'))}"
            )
        )
        copy_button = make_button(
            "", transparent=True, icon=FIF.COPY, on_click=lambda: self._copy(share)
        )
        copy_button.setToolTip("复制链接")
        copy_button.setFixedSize(30, 26)
        if not self.ctx.shares.public_link(share):
            copy_button.setEnabled(False)
            copy_button.setToolTip("离线体验模式不生成公网链接")
        row.addWidget(copy_button)
        if active:
            cancel_button = make_button(
                "", transparent=True, icon=FIF.CANCEL, on_click=lambda: self._cancel(share)
            )
            cancel_button.setToolTip("取消共享")
            cancel_button.setFixedSize(30, 26)
            row.addWidget(cancel_button)
        delete_button = make_button(
            "", transparent=True, icon=FIF.DELETE, on_click=lambda: self._delete(share)
        )
        delete_button.setToolTip("删除共享")
        delete_button.setFixedSize(30, 26)
        row.addWidget(delete_button)
        return frame

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
