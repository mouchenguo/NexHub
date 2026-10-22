"""基于 PyQt6-Fluent-Widgets 的主题管理。

- 默认深色主题
- 支持深色 / 浅色 / 跟随系统
- 主题选择写入本地 config.json
- 所有 Fluent 控件由 qfluentwidgets 自动刷新，无需手写 QSS
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication
from qfluentwidgets import Theme, isDarkTheme, setTheme, setThemeColor

from .config import AppConfig

THEME_MAP = {
    "dark": Theme.DARK,
    "light": Theme.LIGHT,
    "system": Theme.AUTO,
}

ACCENT_COLOR = "#4f8cff"


class ThemeManager(QObject):
    """全局 Fluent 主题控制器。"""

    theme_changed = pyqtSignal(str)

    def __init__(self, app: QApplication, config: AppConfig):
        super().__init__()
        self.app = app
        self.config = config
        setThemeColor(ACCENT_COLOR, save=False)
        self.apply(config.theme, persist=False)

    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self.config.theme

    @property
    def effective_theme(self) -> str:
        return "dark" if isDarkTheme() else "light"

    def set_theme(self, mode: str) -> None:
        if mode not in THEME_MAP:
            mode = "dark"
        self.apply(mode, persist=True)

    def apply(self, mode: str, persist: bool = True) -> None:
        if mode not in THEME_MAP:
            mode = "dark"
        setTheme(THEME_MAP[mode], save=False)
        setThemeColor(ACCENT_COLOR, save=False)
        self.config.theme = mode
        if persist:
            self.config.save()
        self.theme_changed.emit(self.effective_theme)

    def toggle(self) -> None:
        """在深色 / 浅色之间快速切换。"""
        self.set_theme("light" if self.effective_theme == "dark" else "dark")
