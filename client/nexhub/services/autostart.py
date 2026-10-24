"""Windows 当前用户开机自启（HKCU Run）。

不写 HKLM，不需要管理员权限；安装目录由 Inno Setup 负责放到
``Program Files``，自启只记录启动命令。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "NexHub"


def autostart_supported() -> bool:
    return os.name == "nt"


def autostart_command() -> str:
    """返回写入注册表的完整启动命令，路径始终加引号。"""
    if bool(getattr(sys, "frozen", False)):
        executable = Path(sys.executable).resolve()
        return f'"{executable}" --autostart'

    python = Path(sys.executable).resolve()
    pythonw = python.with_name("pythonw.exe")
    launcher = pythonw if pythonw.exists() else python
    main_script = Path(__file__).resolve().parents[2] / "main.py"
    return f'"{launcher}" "{main_script}" --autostart'


def autostart_enabled() -> bool:
    if not autostart_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _value_type = winreg.QueryValueEx(key, VALUE_NAME)
        actual = str(value).strip()
        return bool(actual) and os.path.normcase(actual) == os.path.normcase(
            autostart_command()
        )
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not autostart_supported():
        raise RuntimeError("当前系统不支持 Windows 开机自启")
    import winreg

    with winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
    ) as key:
        if enabled:
            winreg.SetValueEx(
                key, VALUE_NAME, 0, winreg.REG_SZ, autostart_command()
            )
            return
        try:
            winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass
