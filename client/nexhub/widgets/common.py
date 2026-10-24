"""Fluent 通用组件与小型布局助手。"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    FluentIcon as FIF,
    IconWidget,
    MessageBox,
    PillPushButton,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    TitleLabel,
    TransparentPushButton,
    TransparentToolButton,
)


def icon_widget(icon: FIF, size: int = 28) -> IconWidget:
    widget = IconWidget()
    widget.setIcon(icon)
    widget.setFixedSize(size, size)
    return widget


def status_pill(text: str, icon: FIF | None = None) -> PillPushButton:
    """只用于展示的状态胶囊，不参与交互。"""
    pill = PillPushButton(text)
    pill.setCheckable(False)
    pill.setCursor(Qt.CursorShape.ArrowCursor)
    if icon is not None:
        pill.setIcon(icon)
    pill.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return pill


def latency_text(value) -> tuple[str, QColor]:
    """节点延迟文本与颜色，与插件内保持一致（绿/蓝/黄/红）。"""
    if value is None or value == "":
        return "未测", QColor("#8b949e")
    try:
        latency = int(value)
    except (TypeError, ValueError):
        return "未测", QColor("#8b949e")
    if latency < 0:
        return "超时", QColor("#f85149")
    if latency < 250:
        return f"{latency} ms", QColor("#3fb950")
    if latency < 400:
        return f"{latency} ms", QColor("#58a6ff")
    return f"{latency} ms", QColor("#d29922")


def latency_dot(value) -> str:
    """四色延迟圆点，用于 ComboBox 等无法逐项设置前景色的控件。"""
    text, _color = latency_text(value)
    if text == "未测":
        return "⚪"
    if text == "超时":
        return "🔴"
    try:
        latency = int(value)
    except (TypeError, ValueError):
        return "⚪"
    if latency < 250:
        return "🟢"
    if latency < 400:
        return "🔵"
    return "🟡"


def make_button(
    text: str,
    *,
    primary: bool = False,
    transparent: bool = False,
    icon: FIF | None = None,
    on_click: Callable | None = None,
    tooltip: str = "",
) -> PushButton:
    if primary:
        button = PrimaryPushButton(text, icon=icon)
    elif transparent:
        button = TransparentPushButton(text, icon=icon)
    else:
        button = PushButton(text, icon=icon)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click:
        button.clicked.connect(on_click)
    return button


def icon_button(
    icon: FIF,
    *,
    tooltip: str = "",
    on_click: Callable | None = None,
) -> TransparentToolButton:
    button = TransparentToolButton(icon)
    if tooltip:
        button.setToolTip(tooltip)
    if on_click:
        button.clicked.connect(on_click)
    return button


def make_card(padding: int = 20, spacing: int = 12) -> tuple[CardWidget, QVBoxLayout]:
    card = CardWidget()
    card.setBorderRadius(12)
    card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(spacing)
    return card, layout


class FolderRow(CardWidget):
    """可点击的文件夹条目：图标、标题、说明和进入箭头。"""

    clicked = pyqtSignal()

    def __init__(
        self,
        icon: FIF,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setMinimumHeight(72)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 11, 16, 11)
        layout.setSpacing(12)
        layout.addWidget(icon_widget(icon, 24))
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        text_box.addWidget(StrongBodyLabel(title))
        if subtitle:
            subtitle_label = CaptionLabel(subtitle)
            subtitle_label.setWordWrap(True)
            text_box.addWidget(subtitle_label)
        layout.addLayout(text_box, 1)
        layout.addWidget(icon_widget(FIF.CHEVRON_RIGHT_MED, 16))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class FolderDetail(QWidget):
    """文件夹子页面：返回按钮 + 标题 + 内容卡片。"""

    back_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(
            icon_button(
                FIF.RETURN,
                tooltip="返回上一级",
                on_click=lambda: self.back_requested.emit(),
            )
        )
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = TitleLabel(title)
        self.subtitle_label = CaptionLabel(subtitle)
        self.subtitle_label.setWordWrap(True)
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.subtitle_label)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        self.card = CardWidget()
        self.card.setBorderRadius(12)
        self.card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.content_layout = QVBoxLayout(self.card)
        self.content_layout.setContentsMargins(20, 16, 20, 16)
        self.content_layout.setSpacing(12)
        layout.addWidget(self.card)
        layout.addStretch(1)

    def set_title(self, title: str, subtitle: str = "") -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)

    def set_content(self, content: QWidget | None) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
        if content is not None:
            self.content_layout.addWidget(content)


def section_header(title: str, subtitle: str = "") -> tuple[QWidget, QHBoxLayout]:
    """卡片标题行，返回 (widget, actions_layout)。"""
    wrapper = QWidget()
    row = QHBoxLayout(wrapper)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(10)

    text_box = QVBoxLayout()
    text_box.setSpacing(2)
    text_box.addWidget(StrongBodyLabel(title))
    if subtitle:
        text_box.addWidget(CaptionLabel(subtitle))
    row.addLayout(text_box)
    row.addStretch(1)

    actions = QHBoxLayout()
    actions.setSpacing(8)
    row.addLayout(actions)
    return wrapper, actions


def form_field(label: str, widget: QWidget, hint: str = "") -> QVBoxLayout:
    """设置页字段：标签 + 控件 + 可选说明。"""
    box = QVBoxLayout()
    box.setSpacing(5)
    box.addWidget(BodyLabel(label))
    box.addWidget(widget)
    if hint:
        caption = CaptionLabel(hint)
        caption.setWordWrap(True)
        box.addWidget(caption)
    return box


class StatCard(CardWidget):
    """统计卡片：图标 + 标题 + 数值 + 说明。"""

    def __init__(
        self,
        title: str,
        value: str,
        hint: str,
        icon: FIF,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setBorderRadius(12)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.setMinimumHeight(148)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(icon_widget(icon, 22))
        top.addWidget(CaptionLabel(title))
        top.addStretch(1)
        layout.addLayout(top)

        self.value_label = TitleLabel(value)
        layout.addWidget(self.value_label)
        self.hint_label = CaptionLabel(hint)
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)

    def set_value(self, value: str, hint: str | None = None) -> None:
        self.value_label.setText(value)
        if hint is not None:
            self.hint_label.setText(hint)


class EmptyState(QWidget):
    """列表空状态。"""

    def __init__(
        self,
        title: str,
        description: str = "",
        icon: FIF = FIF.FOLDER,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 34, 24, 34)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(icon_widget(icon, 42), 0, Qt.AlignmentFlag.AlignHCenter)
        title_label = StrongBodyLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_label)
        if description:
            desc = CaptionLabel(description)
            desc.setWordWrap(True)
            desc.setMaximumWidth(480)
            desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(desc)
        if action is not None:
            layout.addSpacing(4)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)


class Banner(CardWidget):
    """带图标的信息条卡片。"""

    def __init__(
        self,
        text: str,
        icon: FIF = FIF.INFO,
        action: QWidget | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setBorderRadius(10)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)
        layout.addWidget(icon_widget(icon, 18))
        self.label = BodyLabel(text)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)
        if action is not None:
            layout.addWidget(action)


def ask_confirm(parent: QWidget, title: str, content: str) -> bool:
    """Fluent 确认对话框。"""
    box = MessageBox(title, content, parent)
    box.yesButton.setText("确定")
    box.cancelButton.setText("取消")
    return bool(box.exec())


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.deleteLater()
        child = item.layout()
        if child:
            clear_layout(child)
