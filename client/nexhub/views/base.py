"""Fluent 页面基类：滚动容器、页面标题和全局状态。"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    SingleDirectionScrollArea,
    TitleLabel,
)

from ..context import AppContext
from ..widgets.common import icon_button, status_pill


class BasePage(QWidget):
    page_id = ""
    title = ""
    subtitle = ""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.setObjectName(self.page_id)
        self._loaded = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.scroll = SingleDirectionScrollArea(orient=Qt.Orientation.Vertical)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.content = QWidget()
        self.content.setObjectName("PageContent")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(36, 18, 36, 32)
        self.content_layout.setSpacing(18)
        self.scroll.setWidget(self.content)
        self.scroll.enableTransparentBackground()
        outer.addWidget(self.scroll)

        self._build_header()
        self.build()
        self.ctx.theme.theme_changed.connect(self._sync_header)

    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = QHBoxLayout()
        header.setSpacing(10)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = TitleLabel(self.title)
        self.subtitle_label = BodyLabel(self.subtitle)
        self.subtitle_label.setWordWrap(False)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header.addLayout(title_box)
        header.addStretch(1)

        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        self.connection_pill = status_pill("离线体验", FIF.INFO)
        self.theme_button = icon_button(
            FIF.BRIGHTNESS,
            tooltip="切换深浅主题",
            on_click=self.ctx.theme.toggle,
        )
        self.header_actions.addWidget(self.connection_pill)
        self.header_actions.addWidget(self.theme_button)
        header.addLayout(self.header_actions)
        self.content_layout.addLayout(header)

        self._sync_header()

    def add_header_action(self, widget: QWidget) -> None:
        self.header_actions.insertWidget(0, widget)

    def _sync_header(self, _theme: str | None = None) -> None:
        online = self.ctx.online
        self.connection_pill.setText("ECS 已连接" if online else "离线体验")
        self.connection_pill.setIcon(FIF.CONNECT if online else FIF.INFO)
        target = "浅色" if self.ctx.theme.effective_theme == "dark" else "深色"
        self.theme_button.setToolTip(f"切换为{target}主题")

    # ------------------------------------------------------------------
    def build(self) -> None:
        """子类构建页面内容。"""

    def on_show(self) -> None:
        self._loaded = True
        self._sync_header()
        self.refresh()

    def refresh(self) -> None:
        """子类按需实现数据刷新。"""

    def add_stretch(self) -> None:
        self.content_layout.addStretch(1)


def page_caption(text: str) -> CaptionLabel:
    label = CaptionLabel(text)
    label.setWordWrap(True)
    return label
