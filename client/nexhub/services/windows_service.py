"""Windows 提权辅助函数。

远程文件主机模块已经移除；保留 NPC 配置写入/安装所需的 UAC 判断与提权。
"""
from __future__ import annotations

import ctypes
import sys


def is_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_admin() -> bool:
    """以管理员身份重新启动当前程序。返回是否成功发起 UAC。"""
    if sys.platform != "win32":
        raise RuntimeError("该操作仅支持 Windows 系统")
    if is_admin():
        return True
    launch_args = sys.argv[1:] if getattr(sys, "frozen", False) else sys.argv
    params = " ".join(f'"{arg}"' for arg in launch_args)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, params, None, 1
        )
        return int(result) > 32
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"无法请求管理员权限：{exc}") from exc
