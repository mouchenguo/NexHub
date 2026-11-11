"""独立进程重启助手：等待旧服务端退出后重新拉起。

由 :mod:`app.restart_service` 以脱离会话的方式启动，只依赖标准库：

    python app/restart_helper.py <父进程PID> <服务端根目录> -- <启动命令...>
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

WAIT_SECONDS = 90
POLL_SECONDS = 0.4


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _log_path(root: str) -> str:
    data_dir = os.environ.get("NEXHUB_DATA_DIR") or os.path.join(root, "data")
    log_dir = os.path.join(data_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, "server.out")


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: restart_helper.py <pid> <root> -- <command...>", file=sys.stderr)
        return 2
    pid = int(argv[0])
    root = os.path.abspath(argv[1])
    command = argv[2:]
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        print("missing restart command", file=sys.stderr)
        return 2

    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline and _alive(pid):
        time.sleep(POLL_SECONDS)
    time.sleep(1.0)

    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(_log_path(root), "ab") as log:
        log.write(f"\n[{stamp}] 重启助手启动：{' '.join(command)}\n".encode("utf-8"))
        log.flush()
        kwargs = {
            "cwd": root,
            "stdin": subprocess.DEVNULL,
            "stdout": log,
            "stderr": log,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(command, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
