"""NPC 安装、配置与 Windows 服务控制。

生产路径按需求固定为 ``C:\\Program Files\\npc``：
- 核心文件：``npc.exe``
- 配置：``conf\\npc.conf``
- 服务名：``npc``

安装/写入 Program Files 时若当前进程不是管理员，会通过 UAC 启动同一个
NexHub 可执行文件的 ``--npc-admin`` 模式；helper 完成后把结果写入临时
JSON 返回，避免在命令行中传递 vkey。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import csv
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from ..paths import CLIENT_ROOT, resource_path
from .windows_service import is_admin

log = logging.getLogger(__name__)

NPC_EXE_NAME = "npc.exe"
NPC_SERVICE_NAME = "npc"
NPC_CONF_REL = Path("conf") / "npc.conf"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
SEE_MASK_NOCLOSEPROCESS = 0x00000040
SEE_MASK_FLAG_NO_UI = 0x00000400
ERROR_CANCELLED = 1223

DEFAULT_CONF = """[common]
server_addr=
vkey=
"""


# ---------------------------------------------------------------------- 路径
def install_dir() -> Path:
    override = os.environ.get("NEXHUB_NPC_DIR")
    if override:
        return Path(override).expanduser()
    return Path(r"C:\Program Files\npc")


def npc_exe_path() -> Path:
    return install_dir() / NPC_EXE_NAME


def npc_conf_path() -> Path:
    return install_dir() / NPC_CONF_REL


def bundle_dir() -> Path:
    override = os.environ.get("NEXHUB_NPC_BUNDLE")
    if override:
        return Path(override).expanduser()
    return resource_path("npc")


def _find_bundled_exe(root: Path) -> Path | None:
    direct = root / NPC_EXE_NAME
    if direct.is_file():
        return direct
    if root.is_dir():
        for candidate in root.rglob(NPC_EXE_NAME):
            if candidate.is_file():
                return candidate
    return None


def _find_bundled_zip(root: Path) -> Path | None:
    if root.is_file() and root.suffix.lower() == ".zip":
        return root
    if root.is_dir():
        for candidate in root.glob("*.zip"):
            if candidate.is_file():
                return candidate
    return None


def has_bundle() -> bool:
    root = bundle_dir()
    return _find_bundled_exe(root) is not None or _find_bundled_zip(root) is not None


def _target_requires_admin(target: Path | None = None) -> bool:
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files")).resolve()
    path = str((target or install_dir()).resolve()).lower()
    return path.startswith(str(program_files).lower())


# ---------------------------------------------------------------------- 配置
def _clean_value(value: str) -> str:
    value = (value or "").strip()
    if "\n" in value or "\r" in value:
        raise ValueError("配置值不能包含换行")
    return value


def validate_server_addr(value: str) -> str:
    value = _clean_value(value)
    if not value:
        raise ValueError("请填写主机 IP（含端口），例如 x.x.x.x:8024")
    if ":" not in value:
        raise ValueError("主机地址必须包含端口，例如 x.x.x.x:8024")
    host, port = value.rsplit(":", 1)
    if not host.strip():
        raise ValueError("主机地址不能为空")
    if not port.isdigit() or not 1 <= int(port) <= 65535:
        raise ValueError("端口必须是 1-65535 的数字")
    return value


def read_npc_config(path: Path | None = None) -> dict[str, str]:
    target = path or npc_conf_path()
    result = {"server_addr": "", "vkey": ""}
    if not target.is_file():
        return result
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise RuntimeError(f"读取 NPC 配置失败：{exc}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";", "[")) or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().lower()
        if key in result and not result[key]:
            result[key] = value.strip()
    return result


def _replace_conf_value(lines: list[str], key: str, value: str) -> list[str]:
    """只在 [common] 段更新键；没有段时更新全文第一个键。"""
    common_start = None
    common_end = len(lines)
    for index, line in enumerate(lines):
        if line.strip().lower() == "[common]":
            common_start = index
            for end in range(index + 1, len(lines)):
                if lines[end].strip().startswith("["):
                    common_end = end
                    break
            break

    start = common_start + 1 if common_start is not None else 0
    updated = False
    for index in range(start, common_end):
        match = re.match(r"^\s*([A-Za-z0-9_]+)\s*=", lines[index])
        if match and match.group(1).lower() == key.lower():
            if not updated:
                lines[index] = f"{key}={value}"
                updated = True
            else:
                lines[index] = f"# duplicate {key} ignored"
    if not updated:
        insert_at = common_start + 1 if common_start is not None else 0
        lines.insert(insert_at, f"{key}={value}")
    return lines


def write_npc_config(
    server_addr: str,
    vkey: str,
    path: Path | None = None,
) -> dict[str, str]:
    server_addr = validate_server_addr(server_addr)
    vkey = _clean_value(vkey)
    if not vkey:
        raise ValueError("请填写 vkey")
    target = path or npc_conf_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise RuntimeError(f"读取 NPC 配置失败：{exc}") from exc
    else:
        text = DEFAULT_CONF
    lines = text.splitlines()
    lines = _replace_conf_value(lines, "server_addr", server_addr)
    lines = _replace_conf_value(lines, "vkey", vkey)

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
            prefix=".npc-",
            suffix=".tmp",
        ) as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
            temp_name = handle.name
        os.replace(temp_name, target)
    except OSError as exc:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise PermissionError(f"写入 NPC 配置失败，可能需要管理员权限：{exc}") from exc
    return {"server_addr": server_addr, "vkey": vkey, "path": str(target)}


# ---------------------------------------------------------------------- 安装
def _safe_extract_zip(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (destination / member.filename).resolve()
            if destination not in target.parents and target != destination:
                raise RuntimeError(f"内置 NPC 压缩包包含非法路径：{member.filename}")
            zf.extract(member, destination)


def _copy_bundle(source_root: Path, target: Path, repair: bool) -> None:
    target.mkdir(parents=True, exist_ok=True)
    existing_conf = target / NPC_CONF_REL
    for item in source_root.iterdir():
        destination = target / item.name
        if item.name.lower() == "conf":
            destination.mkdir(parents=True, exist_ok=True)
            for conf_item in item.iterdir():
                conf_target = destination / conf_item.name
                if conf_item.name.lower() == "npc.conf" and existing_conf.exists():
                    continue
                if conf_item.is_dir():
                    shutil.copytree(conf_item, conf_target, dirs_exist_ok=True)
                else:
                    shutil.copy2(conf_item, conf_target)
        elif item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=repair)
        else:
            shutil.copy2(item, destination)
    if not existing_conf.exists() and not (target / NPC_CONF_REL).exists():
        existing_conf.parent.mkdir(parents=True, exist_ok=True)
        existing_conf.write_text(DEFAULT_CONF, encoding="utf-8")


def install_bundle(
    repair: bool = False,
    register_service: bool | None = None,
) -> dict[str, Any]:
    """解压内置 NPC 资源到安装目录，并注册开机自启服务。"""
    source = bundle_dir()
    if not has_bundle():
        raise RuntimeError(
            "客户端未内置 NPC 资源，请将 npc.exe 放入 nexhub/resources/npc 后重新打包"
        )
    temp_dir: Path | None = None
    try:
        zip_path = _find_bundled_zip(source)
        if zip_path is not None:
            temp_dir = Path(tempfile.mkdtemp(prefix="nexhub-npc-"))
            _safe_extract_zip(zip_path, temp_dir)
            exe = _find_bundled_exe(temp_dir)
        else:
            exe = _find_bundled_exe(source)
        if exe is None:
            raise RuntimeError("内置 NPC 资源中未找到 npc.exe")
        target = install_dir()
        if target.exists() and (target / NPC_EXE_NAME).is_file() and not repair:
            if register_service is None:
                register_service = _target_requires_admin(target)
            if register_service:
                ensure_npc_service()
            return get_npc_status()
        _copy_bundle(exe.parent, target, repair=repair)
        if register_service is None:
            register_service = _target_requires_admin(target)
        if register_service:
            ensure_npc_service()
        log.info("NPC 已安装到 %s（repair=%s，service=%s）", target, repair, register_service)
        return get_npc_status()
    except (OSError, shutil.Error, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"释放 NPC 资源失败：{exc}") from exc
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------- 服务
def _service_state() -> str | None:
    result = subprocess.run(
        ["sc.exe", "query", NPC_SERVICE_NAME],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        return None
    match = re.search(r"STATE\s*:\s*(\d+)", result.stdout, re.IGNORECASE)
    if not match:
        return "unknown"
    return {
        1: "stopped",
        2: "start-pending",
        3: "stop-pending",
        4: "running",
        5: "continue-pending",
        6: "pause-pending",
        7: "paused",
    }.get(int(match.group(1)), "unknown")


def _process_running() -> bool:
    result = subprocess.run(
        ["tasklist.exe", "/FI", "IMAGENAME eq npc.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )
    return result.returncode == 0 and "npc.exe" in result.stdout.lower()


def get_npc_status() -> dict[str, Any]:
    target = install_dir()
    exe = target / NPC_EXE_NAME
    conf = target / NPC_CONF_REL
    state = _service_state()
    running = state == "running" or _process_running()
    return {
        "install_dir": str(target),
        "install_dir_exists": target.is_dir(),
        "exe_path": str(exe),
        "config_path": str(conf),
        "installed": exe.is_file(),
        "config_exists": conf.is_file(),
        "service_installed": state is not None,
        "service_state": state,
        "running": running,
        "bundle_available": has_bundle(),
        "is_admin": is_admin(),
        "requires_admin": _target_requires_admin(target),
    }


def _run_cmd(command: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        creationflags=CREATE_NO_WINDOW,
    )
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(message or f"命令执行失败：{' '.join(command)}")
    return result


def _wait_service(expected: set[str], timeout: float = 15.0) -> str | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = _service_state()
        if state in expected:
            return state
        time.sleep(0.35)
    return _service_state()


def stop_npc_service() -> dict[str, Any]:
    """停止 NPC 服务；没有注册服务时结束 npc.exe 进程。"""
    if not npc_exe_path().is_file():
        raise RuntimeError("NPC 未安装，无法停止")
    state = _service_state()
    if state is not None:
        if state in {"running", "start-pending", "stop-pending", "paused", "pause-pending"}:
            _run_cmd(["sc.exe", "stop", NPC_SERVICE_NAME], check=False)
            state = _wait_service({"stopped"}, 15)
            if state != "stopped":
                raise RuntimeError(f"停止 NPC 服务超时，当前状态：{state}")
        # 清理可能由旧版本手动启动、未注册为服务的 npc.exe 进程。
        _run_cmd(["taskkill.exe", "/IM", NPC_EXE_NAME, "/F"], check=False)
        time.sleep(0.3)
        return {"method": "service", "state": state, "status": get_npc_status()}

    _run_cmd(["taskkill.exe", "/IM", NPC_EXE_NAME, "/F"], check=False)
    time.sleep(0.6)
    return {"method": "process", "state": "stopped", "status": get_npc_status()}


def start_npc_service() -> dict[str, Any]:
    """启动 NPC 服务；没有注册服务时启动 npc.exe。"""
    if not npc_exe_path().is_file():
        raise RuntimeError("NPC 未安装，无法启动")
    state = _service_state()
    if state is not None:
        if state != "running":
            _run_cmd(["sc.exe", "start", NPC_SERVICE_NAME])
            state = _wait_service({"running"}, 15)
            if state != "running":
                raise RuntimeError(f"启动 NPC 服务超时，当前状态：{state}")
        return {"method": "service", "state": state, "status": get_npc_status()}

    process = subprocess.Popen(
        [str(npc_exe_path())],
        cwd=str(install_dir()),
        creationflags=DETACHED_PROCESS,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    log.info("已启动 npc.exe，PID=%s", process.pid)
    return {"method": "process", "pid": process.pid, "status": get_npc_status()}


def restart_service() -> dict[str, Any]:
    """重启 NPC 服务；未注册服务时回退到结束/启动 npc.exe。"""
    stop_npc_service()
    return start_npc_service()


def ensure_npc_service(auto_start: bool = True, start_if_configured: bool = True) -> dict[str, Any]:
    """创建或修正 NPC 服务，并设置开机自启。

    等价于：
        sc create npc binPath= "C:\\Program Files\\npc\\npc.exe" start= auto
    已存在时使用 sc config 修正 binPath/start 类型。
    """
    exe = npc_exe_path()
    if not exe.is_file():
        raise RuntimeError("NPC 未安装，无法创建服务")
    start_mode = "auto" if auto_start else "demand"
    command = [
        "sc.exe",
        "create",
        NPC_SERVICE_NAME,
        "binPath=",
        str(exe),
        "start=",
        start_mode,
        "DisplayName=",
        "Npc",
    ]
    if _service_state() is None:
        try:
            _run_cmd(command)
        except RuntimeError:
            if _service_state() is None:
                raise
            _run_cmd(
                [
                    "sc.exe",
                    "config",
                    NPC_SERVICE_NAME,
                    "binPath=",
                    str(exe),
                    "start=",
                    start_mode,
                ]
            )
    else:
        _run_cmd(
            [
                "sc.exe",
                "config",
                NPC_SERVICE_NAME,
                "binPath=",
                str(exe),
                "start=",
                start_mode,
            ]
        )
    if start_if_configured:
        config = read_npc_config()
        if config.get("server_addr") and config.get("vkey"):
            if _service_state() != "running":
                _run_cmd(["sc.exe", "start", NPC_SERVICE_NAME], check=False)
    return get_npc_status()


# ---------------------------------------------------------------------- UAC
class _SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("fMask", wintypes.ULONG),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", ctypes.c_void_p),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", ctypes.c_void_p),
        ("dwHotKey", wintypes.DWORD),
        ("hIcon", ctypes.c_void_p),
        ("hProcess", wintypes.HANDLE),
    ]


def _current_user_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            row = next(csv.reader([result.stdout.strip()]), [])
            if row and row[-1].strip().startswith("S-1-"):
                return row[-1].strip()
    except (OSError, ValueError):
        pass
    return ""


def _allow_elevated_exchange(path: Path) -> None:
    """允许当前用户、管理员和 SYSTEM 读写 UAC 响应交换目录。

    tempfile.mkdtemp 在部分 Windows 账户策略下只留下 OWNER RIGHTS 和
    Administrators，UAC 提权后的进程（尤其是使用另一个管理员账户）无法
    写 response.json。这里显式设置继承 ACL，避免权限错误。
    """
    if sys.platform != "win32":
        return
    grants = []
    sid = _current_user_sid()
    if sid:
        grants.append(f"*{sid}:(OI)(CI)F")
    grants.extend(
        [
            "*S-1-5-32-544:(OI)(CI)F",  # BUILTIN\Administrators
            "*S-1-5-18:(OI)(CI)F",  # NT AUTHORITY\SYSTEM
        ]
    )
    try:
        subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", *grants],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    except OSError as exc:
        log.warning("设置 UAC 交换目录权限失败：%s", exc)


def _launch_elevated(request_path: Path) -> None:
    if sys.platform != "win32":
        raise RuntimeError("NPC 管理仅支持 Windows")
    if getattr(sys, "frozen", False):
        parameters = f'--npc-admin --request "{request_path}"'
    else:
        main_py = CLIENT_ROOT / "main.py"
        parameters = f'"{main_py}" --npc-admin --request "{request_path}"'

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_FLAG_NO_UI
    info.lpVerb = "runas"
    info.lpFile = str(sys.executable)
    info.lpParameters = parameters
    info.lpDirectory = str(CLIENT_ROOT)
    info.nShow = 0
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        code = ctypes.get_last_error()
        if code == ERROR_CANCELLED:
            raise PermissionError("已取消管理员授权，NPC 操作已终止")
        raise RuntimeError(f"无法启动管理员授权进程（错误 {code}）")
    if not info.hProcess:
        raise RuntimeError("管理员授权进程未启动")
    try:
        wait_result = kernel32.WaitForSingleObject(info.hProcess, 180_000)
        if wait_result == 0x00000102:  # WAIT_TIMEOUT
            raise TimeoutError("NPC 管理员操作超时")
        exit_code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        if exit_code.value != 0:
            log.warning("NPC 管理员 helper 退出码：%s", exit_code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


class NpcManager:
    """UI 使用的 NPC 管理门面，自动处理 UAC。"""

    def status(self) -> dict[str, Any]:
        return get_npc_status()

    def read_config(self) -> dict[str, str]:
        return read_npc_config()

    def install(self, repair: bool = False) -> dict[str, Any]:
        if _target_requires_admin() and not is_admin():
            return self._elevated("install", {"repair": repair})
        return install_bundle(repair=repair)

    def save_config(self, server_addr: str, vkey: str) -> dict[str, str]:
        validate_server_addr(server_addr)
        if not _clean_value(vkey):
            raise ValueError("请填写 vkey")
        if _target_requires_admin() and not is_admin():
            return self._elevated(
                "write_config", {"server_addr": server_addr, "vkey": vkey}
            )
        return write_npc_config(server_addr, vkey)

    def apply_config(self, server_addr: str, vkey: str) -> dict[str, Any]:
        """停止 NPC、写入新配置、再启动，避免旧 vkey 继续在线。"""
        validate_server_addr(server_addr)
        if not _clean_value(vkey):
            raise ValueError("请填写 vkey")
        if _target_requires_admin() and not is_admin():
            return self._elevated(
                "write_config_restart",
                {"server_addr": server_addr, "vkey": vkey},
            )
        stop_npc_service()
        try:
            data = write_npc_config(server_addr, vkey)
        finally:
            # 即使写入失败，也尽量恢复旧配置对应的 NPC 运行。
            start_npc_service()
        return data

    def restart(self) -> dict[str, Any]:
        if not is_admin():
            return self._elevated("restart", {})
        return restart_service()

    # ------------------------------------------------------------------
    def _elevated(self, action: str, payload: dict[str, Any]) -> Any:
        temp_dir = Path(tempfile.mkdtemp(prefix="nexhub-npc-elevated-"))
        _allow_elevated_exchange(temp_dir)
        request_path = temp_dir / "request.json"
        response_path = temp_dir / "response.json"
        request = dict(payload)
        request.update(
            action=action,
            response=str(response_path),
            install_dir=str(install_dir()),
        )
        request_path.write_text(
            json.dumps(request, ensure_ascii=False), encoding="utf-8"
        )
        try:
            _launch_elevated(request_path)
            if not response_path.is_file():
                raise RuntimeError("管理员进程未返回结果，操作可能被取消")
            result = json.loads(response_path.read_text(encoding="utf-8"))
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "NPC 管理员操作失败")
            return result.get("data")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
