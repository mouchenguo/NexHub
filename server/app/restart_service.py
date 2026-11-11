"""服务端重启调度。

安装完新版本后需要重启进程才能加载新代码，这里根据运行方式选择策略：

- ``systemd``：ECS 一键部署的 nexhub.service 带 ``Restart=always``，
  进程以非零码退出即可由 systemd 拉起（``INVOCATION_ID`` 存在说明在 unit 内）；
- ``self``：手动 / nohup 运行，用脱离会话的 ``restart_helper.py``
  等旧进程退出后再拉起；
- ``command``：设置 ``NEXHUB_RESTART_CMD`` 交给外部工具（supervisorctl / docker 等）；
- ``manual``：托管方式不支持自动重启，只能提示管理员手动执行。
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import settings

RESTART_ENV = "NEXHUB_RESTART_CMD"
SERVICE_NAME = os.environ.get("NEXHUB_SERVICE_NAME", "nexhub").strip() or "nexhub"
SAFE_POLICIES = ("always", "on-failure", "on-abnormal")


class RestartUnsupported(RuntimeError):
    """当前运行方式无法自动重启。"""


def _under_systemd() -> bool:
    if os.name == "nt":
        return False
    return bool(os.environ.get("INVOCATION_ID")) or Path("/run/systemd/system").is_dir()


def _systemd_policy() -> str:
    if not shutil.which("systemctl"):
        return ""
    try:
        result = subprocess.run(
            ["systemctl", "show", "-p", "Restart", "--value", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (result.stdout or "").strip().lower()


def strategy() -> str:
    """返回 ``command`` / ``systemd`` / ``self`` / ``manual``。"""
    if os.environ.get(RESTART_ENV, "").strip():
        return "command"
    if _under_systemd():
        return "systemd" if _systemd_policy() in SAFE_POLICIES else "manual"
    return "self"


def restart_hint() -> str:
    mode = strategy()
    if mode == "systemd":
        return f"重启后由 systemd 自动拉起（{SERVICE_NAME}.service, Restart=always）"
    if mode == "command":
        return f"通过 NEXHUB_RESTART_CMD 重启：{os.environ.get(RESTART_ENV, '').strip()}"
    if mode == "self":
        return "由后台重启助手在旧进程退出后拉起新进程"
    return f"当前运行方式不支持自动重启，请手动执行 systemctl restart {SERVICE_NAME}"


def _arg(flag: str, env_name: str, default: str) -> str:
    argv = sys.argv[1:]
    for index, item in enumerate(argv):
        if item == flag and index + 1 < len(argv):
            return argv[index + 1]
        if item.startswith(f"{flag}="):
            return item.split("=", 1)[1]
    return os.environ.get(env_name, default)


def launch_command() -> list[str]:
    """还原当前服务端的启动命令。"""
    raw = os.environ.get(RESTART_ENV, "").strip()
    if raw:
        return shlex.split(raw, posix=os.name != "nt")
    host = _arg("--host", "NEXHUB_HOST", "0.0.0.0")
    port = _arg("--port", "NEXHUB_PORT", "8000")
    run_py = settings.base_dir / "run.py"
    if run_py.is_file():
        return [sys.executable, str(run_py), "--host", host, "--port", port]
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        host,
        "--port",
        port,
    ]


def _spawn_detached(command: list[str]) -> None:
    options: dict = {
        "cwd": str(settings.base_dir),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        options["creationflags"] = 0x00000008 | 0x00000200
    else:
        options["start_new_session"] = True
    subprocess.Popen(command, **options)


def _exit_later(delay: float, code: int) -> None:
    time.sleep(max(delay, 0.5))
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def schedule_restart(delay: float = 1.5) -> dict:
    """安排一次重启，返回 ``{"mode", "detail", "command"}``。"""
    mode = strategy()
    if mode == "manual":
        raise RestartUnsupported(
            f"当前服务由外部进程托管且未开启自动重启，请手动执行 systemctl restart {SERVICE_NAME}"
        )
    command = launch_command()
    if mode == "systemd":
        threading.Thread(target=_exit_later, args=(delay, 1), daemon=True).start()
        return {
            "mode": mode,
            "detail": f"已请求退出，systemd 将在数秒内自动拉起（{SERVICE_NAME}.service）",
            "command": command,
        }

    helper = Path(__file__).with_name("restart_helper.py")
    if not helper.is_file():
        raise RestartUnsupported("缺少 app/restart_helper.py，无法自动重启")
    _spawn_detached(
        [sys.executable, str(helper), str(os.getpid()), str(settings.base_dir), "--", *command]
    )
    threading.Thread(target=_exit_later, args=(delay, 0), daemon=True).start()
    return {
        "mode": mode,
        "detail": f"{max(delay, 0.5):.1f} 秒后由重启助手重新拉起服务端",
        "command": command,
    }
