"""客户端内嵌 FastAPI 本地服务。

临时穿透共享时，本机只监听 127.0.0.1，nps 将公网请求转发到这里。
所有接口都使用共享 token 鉴权，文件通过 FileResponse 流式返回，支持 Range。
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .file_utils import safe_join, safe_parts


@dataclass
class LocalShareEntry:
    token: str
    name: str
    root: Path
    files: dict[str, int] = field(default_factory=dict)
    storage_type: str = "temp"


_LOCK = threading.RLock()
_SHARES: dict[str, LocalShareEntry] = {}
_UPLOAD_LIMIT = 10 * 1024 ** 3


def register_share(token: str, name: str, root: str | Path, files: list[dict]) -> None:
    with _LOCK:
        _SHARES[token] = LocalShareEntry(
            token=token,
            name=name,
            root=Path(root).resolve(),
            files={item["path"]: int(item.get("size") or 0) for item in files},
        )


def unregister_share(token: str) -> None:
    with _LOCK:
        _SHARES.pop(token, None)


def _get_entry(token: str) -> LocalShareEntry:
    with _LOCK:
        entry = _SHARES.get(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="共享未启用或已过期")
    return entry


def _resolve(entry: LocalShareEntry, relative: str) -> Path:
    if relative not in entry.files:
        raise HTTPException(status_code=404, detail="文件不在共享范围内")
    # 兼容旧版把单个文件本身作为 root 注册的共享。
    try:
        target = safe_join(entry.root, relative)
        if (
            not target.is_file()
            and entry.root.is_file()
            and entry.root.name == relative
        ):
            target = entry.root
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return target


def create_app() -> FastAPI:
    app = FastAPI(title="NexHub Local Agent", docs_url=None, redoc_url=None)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "service": "nexhub-local", "shares": len(_SHARES)}

    @app.get("/api/local/shares/{token}/manifest")
    def manifest(token: str) -> dict:
        entry = _get_entry(token)
        files = [{"path": path, "size": size} for path, size in entry.files.items()]
        return {
            "name": entry.name,
            "token": token,
            "storage_type": entry.storage_type,
            "files": files,
            "total_size": sum(entry.files.values()),
            "file_count": len(files),
        }

    @app.get("/file/{token}/{file_path:path}")
    def download(token: str, file_path: str):
        entry = _get_entry(token)
        target = _resolve(entry, file_path)
        return FileResponse(
            target,
            filename=target.name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, max-age=0"},
        )

    @app.post("/api/local/upload/{token}/{file_path:path}")
    async def upload(token: str, file_path: str, request: Request):
        """预留上传接口：远程主机写回文件时使用，限制在共享根目录内。"""
        entry = _get_entry(token)
        try:
            safe_parts(file_path)
            target = safe_join(entry.root, f"_uploads/{file_path}")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with target.open("wb") as handle:
            async for chunk in request.stream():
                total += len(chunk)
                if total > _UPLOAD_LIMIT:
                    target.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="文件超过内嵌服务上传限制")
                handle.write(chunk)
        return JSONResponse({"ok": True, "path": f"_uploads/{file_path}", "size": total})

    return app


class EmbeddedServer:
    """管理内嵌 uvicorn 服务的生命周期。"""

    def __init__(self, port: int = 8765):
        self.port = port
        self.app = create_app()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        if self.running:
            return
        self._ensure_port_free()
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            # 不要用 uvicorn 的 dictConfig：打包后可能与主程序 logging 冲突，
            # 典型错误是 Unable to configure formatter 'default'。
            log_config=None,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run, name="nexhub-local-server", daemon=True
        )
        self._thread.start()
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f"本地文件服务启动失败（端口 {self.port} 可能被占用）")

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=3)
        self._server = None
        self._thread = None

    def _ensure_port_free(self) -> None:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            probe.bind(("127.0.0.1", self.port))
        except OSError as exc:
            raise RuntimeError(f"端口 {self.port} 已被占用，请在设置中修改本地服务端口") from exc
        finally:
            probe.close()
