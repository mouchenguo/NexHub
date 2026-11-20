"""共享详情浮窗：紧凑列表里点「介绍」弹出的卡片（无边框 Popup）。"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    FluentIcon as FIF,
    StrongBodyLabel,
    isDarkTheme,
)

from ..file_utils import human_size, relative_time
from .common import make_button, status_pill

MAX_DESCRIPTION_HEIGHT = 120


class ClickableRow(QWidget):
    """整行可点击的行容器（点击空白处也能触发）。"""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ShareInfoPopup(QWidget):
    """展示共享介绍与关键信息的浮窗，点击外部自动关闭。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)

        self.card = QFrame()
        self.card.setObjectName("ShareInfoCard")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setFixedWidth(392)
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 130))
        shadow.setOffset(0, 8)
        self.card.setGraphicsEffect(shadow)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        self.title_label = StrongBodyLabel("")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.meta_row = QHBoxLayout()
        self.meta_row.setSpacing(6)
        self.owner_label = CaptionLabel("")
        self.meta_row.addWidget(self.owner_label)
        self.meta_row.addStretch(1)
        self.type_pill = status_pill("OSS", FIF.CLOUD)
        self.meta_row.addWidget(self.type_pill)
        layout.addLayout(self.meta_row)

        self.stats_label = CaptionLabel("")
        self.stats_label.setWordWrap(True)
        layout.addWidget(self.stats_label)

        self.desc_label = BodyLabel("")
        self.desc_label.setWordWrap(True)
        self.desc_scroll = QScrollArea()
        self.desc_scroll.setWidgetResizable(True)
        self.desc_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.desc_scroll.setMaximumHeight(MAX_DESCRIPTION_HEIGHT)
        self.desc_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.desc_scroll.setStyleSheet("background: transparent;")
        self.desc_scroll.viewport().setAutoFillBackground(False)
        self.desc_scroll.setWidget(self.desc_label)
        layout.addWidget(self.desc_scroll)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.copy_button = make_button("复制链接", transparent=True, icon=FIF.COPY)
        self.browse_button = make_button("浏览文件", primary=True, icon=FIF.FOLDER)
        self.actions.addWidget(self.copy_button)
        self.actions.addStretch(1)
        self.actions.addWidget(self.browse_button)
        layout.addLayout(self.actions)

        outer.addWidget(self.card)

    # ------------------------------------------------------------------
    def _apply_style(self) -> None:
        """浮窗必须不透明：深色用 #262626、浅色用白底，避免和背后列表重叠。"""
        if isDarkTheme():
            background, border = "#2f2f2f", "#5c5c5c"
        else:
            background, border = "#ffffff", "#cfd7e3"
        self.card.setStyleSheet(
            "QFrame#ShareInfoCard {"
            f"background-color: {background};"
            f"border: 1px solid {border};"
            "border-radius: 12px;"
            "}"
        )

    def show_for(
        self,
        share: dict,
        anchor: QWidget,
        on_copy: Callable[[], None] | None = None,
        on_browse: Callable[[], None] | None = None,
        public_link: str = "",
    ) -> None:
        self._apply_style()
        is_oss = share.get("storage_type") == "oss"
        title = str(share.get("name") or "未命名共享")
        self.title_label.setText(title)
        self.owner_label.setText(
            "发布者 "
            + str(share.get("owner_display") or share.get("owner") or "未知用户")
        )
        self.type_pill.deleteLater()
        self.type_pill = status_pill(
            "OSS 长期" if is_oss else "临时穿透", FIF.CLOUD if is_oss else FIF.SYNC
        )
        self.meta_row.addWidget(self.type_pill)
        self.stats_label.setText(
            f"{share.get('file_count', 0)} 个文件 · {human_size(share.get('total_size', 0))}"
            f" · 下载 {share.get('downloads', 0)} 次"
            f" · {relative_time(share.get('created_at'))}"
        )
        description = str(share.get("description") or "").strip()
        self.desc_label.setText(description or "发布者没有填写介绍。")
        self.desc_label.setVisible(True)

        self.copy_button.setEnabled(bool(public_link))
        self.copy_button.setToolTip("复制公开链接" if public_link else "当前没有公开链接")
        try:
            self.copy_button.clicked.disconnect()
        except TypeError:
            pass
        if on_copy:
            self.copy_button.clicked.connect(on_copy)
        try:
            self.browse_button.clicked.disconnect()
        except TypeError:
            pass
        if on_browse:
            self.browse_button.clicked.connect(on_browse)

        self.adjustSize()
        self._place_near(anchor)
        self.show()
        self.raise_()

    def _place_near(self, anchor: QWidget) -> None:
        rect = anchor.rect()
        bottom = anchor.mapToGlobal(QPoint(0, rect.height() + 6))
        top = anchor.mapToGlobal(QPoint(0, 0))
        screen = self.screen().availableGeometry() if self.screen() else None
        x = bottom.x() - self.width() + rect.width()
        y = bottom.y()
        if screen is not None:
            x = max(screen.left() + 8, min(x, screen.right() - self.width() - 8))
            if y + self.height() > screen.bottom() - 8:
                y = top.y() - self.height() - 6
            y = max(screen.top() + 8, y)
        self.move(x, y)
