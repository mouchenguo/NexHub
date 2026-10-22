"""管理员权限下的 NPC 操作 helper。

NexHub 以 ``--npc-admin --request <json>`` 启动本模块，完成解压、写配置或
重启服务后把结果写入 request 中指定的 response 文件。普通用户不需要手动
调用这个模块。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def run_request(request_path: str | Path) -> int:
    """执行一个 elevated 请求并返回进程退出码。"""
    request_file = Path(request_path)
    response: Path | None = None
    result: dict[str, Any]
    try:
        payload = json.loads(request_file.read_text(encoding="utf-8"))
        response = Path(payload["response"])
        if payload.get("install_dir"):
            os.environ["NEXHUB_NPC_DIR"] = str(payload["install_dir"])
        action = payload.get("action")
        if action == "install":
            from .services.npc_service import install_bundle

            data = install_bundle(repair=bool(payload.get("repair")))
        elif action == "write_config":
            from .services.npc_service import write_npc_config

            data = write_npc_config(
                str(payload.get("server_addr", "")),
                str(payload.get("vkey", "")),
            )
        elif action == "write_config_restart":
            from .services.npc_service import (
                start_npc_service,
                stop_npc_service,
                write_npc_config,
            )

            stop_npc_service()
            try:
                data = write_npc_config(
                    str(payload.get("server_addr", "")),
                    str(payload.get("vkey", "")),
                )
            finally:
                start_npc_service()
        elif action == "restart":
            from .services.npc_service import restart_service

            data = restart_service()
        else:
            raise RuntimeError(f"未知的 NPC 管理员操作：{action}")
        result = {"ok": True, "data": data}
        code = 0
    except Exception as exc:  # noqa: BLE001
        log.exception("NPC 管理员操作失败")
        result = {"ok": False, "error": str(exc)}
        code = 1

    if response is not None:
        response.parent.mkdir(parents=True, exist_ok=True)
        temp = response.with_suffix(response.suffix + ".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        os.replace(temp, response)
    return code
