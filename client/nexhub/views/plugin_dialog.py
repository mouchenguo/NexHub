"""自定义插件模板弹窗：保存到用户插件目录后可即时加载。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout, QVBoxLayout
from qfluentwidgets import (
    CaptionLabel,
    FluentStyleSheet,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PlainTextEdit,
    StrongBodyLabel,
    TitleLabel,
)

from ..paths import user_plugins_dir
from ..widgets.common import ask_confirm, make_button


class AddPluginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.saved_path: Path | None = None
        self.setWindowTitle("NexHub · 添加自定义插件")
        self.setMinimumSize(760, 650)
        FluentStyleSheet.DIALOG.apply(self)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(TitleLabel("添加自定义插件"))
        warning = CaptionLabel(
            "插件代码会在当前用户权限下执行。请只保存和加载你信任的代码。"
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        layout.addWidget(CaptionLabel("插件名称"))
        self.name_edit = LineEdit()
        self.name_edit.setText("我的插件")
        layout.addWidget(self.name_edit)

        layout.addWidget(CaptionLabel("插件 ID（仅字母、数字、下划线，不能以数字开头）"))
        self.id_edit = LineEdit()
        self.id_edit.setText("my_plugin")
        layout.addWidget(self.id_edit)

        row = QHBoxLayout()
        row.setSpacing(8)
        name_box = QVBoxLayout()
        name_box.addWidget(CaptionLabel("作者（可选）"))
        self.author_edit = LineEdit()
        self.author_edit.setPlaceholderText("你的名字")
        name_box.addWidget(self.author_edit)
        row.addLayout(name_box, 1)
        desc_box = QVBoxLayout()
        desc_box.addWidget(CaptionLabel("描述（可选）"))
        self.description_edit = LineEdit()
        self.description_edit.setPlaceholderText("插件用途")
        desc_box.addWidget(self.description_edit)
        row.addLayout(desc_box, 2)
        layout.addLayout(row)

        code_header = QHBoxLayout()
        code_header.addWidget(StrongBodyLabel("插件模板"))
        code_header.addStretch(1)
        code_header.addWidget(
            make_button(
                "按上方信息重新生成模板",
                transparent=True,
                on_click=self._regenerate,
            )
        )
        layout.addLayout(code_header)
        self.editor = PlainTextEdit()
        self.editor.setFont(QFont("Consolas", 9))
        self._generated_template = self._template()
        self.editor.setPlainText(self._generated_template)
        layout.addWidget(self.editor, 1)

        location = CaptionLabel(f"保存目录：{user_plugins_dir()}")
        location.setWordWrap(True)
        layout.addWidget(location)
        layout.addWidget(
            CaptionLabel(
                "模板只是一个最小插件；可以在 create_widget() 中自由布局，"
                "并通过 ctx 使用配置、API、共享和 RDP 等服务。"
            )
        )

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(
            make_button("复制模板", transparent=True, on_click=self._copy)
        )
        footer.addStretch(1)
        footer.addWidget(make_button("取消", on_click=self.reject))
        footer.addWidget(
            make_button("保存并加载", primary=True, on_click=self._save)
        )
        layout.addLayout(footer)
    def _plugin_id(self) -> str:
        return self.id_edit.text().strip()

    def _class_name(self) -> str:
        parts = re.split(r"[^0-9a-zA-Z]+", self._plugin_id())
        name = "".join(part[:1].upper() + part[1:] for part in parts if part)
        if not name or name[0].isdigit():
            name = "Custom" + name
        return name + "Plugin"

    @staticmethod
    def _py_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def _template(self) -> str:
        plugin_id = self._plugin_id() or "my_plugin"
        name = self.name_edit.text().strip() or "我的插件"
        author = self.author_edit.text().strip()
        description = self.description_edit.text().strip() or "自定义 NexHub 插件"
        return f'''from PyQt6.QtWidgets import QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, CaptionLabel, TitleLabel

from nexhub.services.plugin_manager import ToolPlugin


class {self._class_name()}(ToolPlugin):
    plugin_id = {self._py_string(plugin_id)}
    name = {self._py_string(name)}
    description = {self._py_string(description)}
    version = "1.0.0"
    author = {self._py_string(author)}
    glyph = "★"
    accent = "accent"

    def create_widget(self, ctx, parent=None):
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(TitleLabel({self._py_string(name)}))
        layout.addWidget(BodyLabel("在这里实现你的插件界面。"))

        hint = CaptionLabel(
            "可以通过 ctx.config、ctx.api、ctx.shares、ctx.rdp 等访问 NexHub 服务。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return widget
'''

    def _regenerate(self) -> None:
        self._generated_template = self._template()
        self.editor.setPlainText(self._generated_template)
        InfoBar.success(
            "模板已更新",
            "已按上方插件信息重新生成代码模板",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=1800,
        )

    def _copy(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())
        InfoBar.success(
            "已复制",
            "插件模板已复制到剪贴板",
            parent=self,
            position=InfoBarPosition.TOP,
            duration=1800,
        )

    def _save(self) -> None:
        plugin_id = self._plugin_id()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", plugin_id):
            InfoBar.error(
                "插件 ID 无效",
                "请使用字母、数字、下划线，且不能以数字开头",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        if self.editor.toPlainText().strip() == self._generated_template.strip():
            self._generated_template = self._template()
            self.editor.setPlainText(self._generated_template)
        code = self.editor.toPlainText().strip()
        if not code:
            InfoBar.error(
                "代码为空",
                "请填写插件代码",
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        path = user_plugins_dir() / f"{plugin_id}.py"
        if path.exists() and not ask_confirm(
            self,
            "覆盖插件",
            f"插件文件 {path.name} 已存在，确定覆盖吗？",
        ):
            return
        try:
            path.write_text(code + "\n", encoding="utf-8")
        except OSError as exc:
            InfoBar.error(
                "保存失败",
                str(exc),
                parent=self,
                position=InfoBarPosition.TOP,
            )
            return
        self.saved_path = path
        self.accept()

