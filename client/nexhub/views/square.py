"""共享广场：Fluent 卡片网格与选择性下载。"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
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
    SearchLineEdit,
    StrongBodyLabel,
)

from ..file_utils import human_size, relative_time
from ..widgets.common import (
    EmptyState,
    icon_widget,
    make_button,
    make_card,
    status_pill,
)
from ..widgets.file_browser import FileBrowserDialog
from ..workers import FunctionWorker
from .base import BasePage


class SquarePage(BasePage):
    page_id = "square"
    title = "共享广场"
    subtitle = "浏览所有用户公开的共享资源，按需下载"

    def __init__(self, ctx, parent=None):
        self._shares: list[dict] = []
        self._worker: FunctionWorker | None = None
        self._manifest_worker: FunctionWorker | None = None
        super().__init__(ctx, parent)

    def build(self) -> None:
        toolbar, toolbar_layout = make_card(14, 8)
        row = QHBoxLayout()
        row.setSpacing(9)
        self.search = SearchLineEdit()
        self.search.setPlaceholderText("搜索共享、发布者...")
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self._render)
        row.addWidget(self.search)

        self.type_filter = ComboBox()
        self.type_filter.addItems(["全部类型", "OSS 长期缓存", "临时穿透"])
        self.type_filter.setFixedWidth(150)
        self.type_filter.currentIndexChanged.connect(self._render)
        row.addWidget(self.type_filter)

        self.sort_combo = ComboBox()
        self.sort_combo.addItems(["最新发布", "名称 A-Z", "文件最多"])
        self.sort_combo.setFixedWidth(120)
        self.sort_combo.currentIndexChanged.connect(self._render)
        row.addWidget(self.sort_combo)
        row.addStretch(1)
        row.addWidget(
            make_button("刷新", transparent=True, icon=FIF.SYNC, on_click=self.refresh)
        )
        toolbar_layout.addLayout(row)
        self.content_layout.addWidget(toolbar)

        self.summary = CaptionLabel("正在载入共享...")
        self.content_layout.addWidget(self.summary)

        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)
        self.content_layout.addWidget(self.grid_host)

        self.empty = EmptyState(
            "广场暂时安静",
            "还没有人发布共享，去「我的共享」发布第一个吧。",
            FIF.GLOBE,
            make_button(
                "去发布共享",
                primary=True,
                icon=FIF.ADD,
                on_click=lambda: self.ctx.navigate("my_shares"),
            ),
        )
        self.empty.hide()
        self.content_layout.addWidget(self.empty)
        self.content_layout.addStretch(1)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._worker = FunctionWorker(self.ctx.shares.list_public_shares, parent=self)
        self._worker.succeeded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.start()

    def _on_loaded(self, shares: list[dict]) -> None:
        self._shares = [item for item in shares if item.get("status", "active") == "active"]
        self._render()

    def _on_error(self, message: str) -> None:
        self.summary.setText(f"共享广场加载失败：{message}")
        self.ctx.notify(f"共享广场加载失败：{message}", "error")

    def _render(self) -> None:
        clear_grid(self.grid)
        query = self.search.text().strip().lower()
        type_index = self.type_filter.currentIndex()
        items = []
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
            items.append(share)
        sort_index = self.sort_combo.currentIndex()
        if sort_index == 1:
            items.sort(key=lambda item: item.get("name", "").lower())
        elif sort_index == 2:
            items.sort(key=lambda item: item.get("file_count", 0), reverse=True)
        else:
            items.sort(key=lambda item: item.get("created_at") or "", reverse=True)

        self.summary.setText(f"共 {len(self._shares)} 个公开共享 · 当前显示 {len(items)} 个")
        if not items:
            if self._shares:
                self.empty.hide()
                self.grid.addWidget(CaptionLabel("没有符合条件的共享"), 0, 0, 1, 2)
            else:
                self.empty.show()
            return
        self.empty.hide()
        for index, share in enumerate(items):
            self.grid.addWidget(self._card(share), index // 2, index % 2)

    def _card(self, share: dict) -> QWidget:
        card = CardWidget()
        card.setBorderRadius(12)
        card.setMinimumHeight(210)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        is_oss = share.get("storage_type") == "oss"
        top.addWidget(icon_widget(FIF.CLOUD if is_oss else FIF.SYNC, 24))
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = StrongBodyLabel(share.get("name", "未命名共享"))
        title.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(CaptionLabel(share.get("owner_display") or share.get("owner", "未知用户")))
        top.addLayout(title_box, 1)
        top.addWidget(status_pill("OSS" if is_oss else "临时", FIF.CLOUD if is_oss else FIF.SYNC))
        layout.addLayout(top)

        desc = BodyLabel(share.get("description") or "发布者没有填写共享说明。")
        desc.setWordWrap(True)
        desc.setMinimumHeight(36)
        layout.addWidget(desc)
        layout.addStretch(1)

        layout.addWidget(
            CaptionLabel(
                f"{share.get('file_count', 0)} 个文件 · {human_size(share.get('total_size', 0))}"
                f" · 下载 {share.get('downloads', 0)} 次"
                f" · {relative_time(share.get('created_at'))}"
            )
        )

        footer = QHBoxLayout()
        footer.setSpacing(6)
        copy_button = make_button(
            "复制链接", transparent=True, icon=FIF.COPY, on_click=lambda: self._copy_link(share)
        )
        if not self.ctx.shares.public_link(share):
            copy_button.setEnabled(False)
            copy_button.setToolTip("离线体验模式不生成公网链接")
        footer.addWidget(copy_button)
        footer.addStretch(1)
        browse = make_button("浏览文件", primary=True, icon=FIF.FOLDER)
        browse.clicked.connect(lambda: self._browse(share, browse))
        footer.addWidget(browse)
        layout.addLayout(footer)
        return card

    # ------------------------------------------------------------------
    def _browse(self, share: dict, button) -> None:
        if self._manifest_worker and self._manifest_worker.isRunning():
            self.ctx.notify("正在加载其他共享，请稍候", "warning")
            return
        button.setEnabled(False)
        button.setText("加载中...")
        self._manifest_worker = FunctionWorker(self.ctx.shares.manifest, share, parent=self)
        self._manifest_worker.succeeded.connect(
            lambda manifest: self._open_browser(share, manifest, button)
        )
        self._manifest_worker.failed.connect(
            lambda message: self._manifest_failed(message, button)
        )
        self._manifest_worker.start()

    def _open_browser(self, share: dict, manifest: dict, button) -> None:
        button.setEnabled(True)
        button.setText("浏览文件")
        dialog = FileBrowserDialog(
            self.ctx,
            title=share.get("name", "共享文件"),
            subtitle=f"发布者 {share.get('owner', '未知用户')} · token 鉴权访问",
            manifest=manifest,
            job_factory=lambda files: self.ctx.shares.build_download_jobs(share, files),
            storage_label="OSS 长期缓存"
            if share.get("storage_type") == "oss"
            else "临时穿透",
            parent=self,
        )
        dialog.exec()

    def _manifest_failed(self, message: str, button) -> None:
        button.setEnabled(True)
        button.setText("浏览文件")
        self.ctx.notify(f"无法打开共享：{message}", "error")

    def _copy_link(self, share: dict) -> None:
        link = self.ctx.shares.public_link(share)
        if not link:
            self.ctx.notify("离线体验模式没有公网链接", "warning")
            return
        QApplication.clipboard().setText(link)
        self.ctx.notify("共享链接已复制", "success")


def clear_grid(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()
