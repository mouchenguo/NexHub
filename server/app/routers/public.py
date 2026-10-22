"""网页端备用访问：无需安装客户端，凭分享 token 浏览与下载。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db
from ..common import client_ip, log_download, share_manifest, share_payload, web_base
from ..share_ops import fetch_share_by_token
from ..templating import render
from ..transfer import find_file, stream_share_file

router = APIRouter(tags=["public"])


@router.get("/s/{token}", response_class=HTMLResponse)
def public_share_page(token: str, request: Request):
    share = fetch_share_by_token(token)
    if not share or share["status"] != "active":
        return render(
            request,
            "public_expired.html",
            {"title": "共享不存在", "token": token},
        )
    db.log_event(
        "info",
        "share",
        f"网页访问共享：{share['name']}",
        ip=client_ip(request),
    )
    files = share_manifest(share)
    payload = share_payload(share, None, web_base(request))
    return render(
        request,
        "public_share.html",
        {
            "title": share["name"],
            "share": payload,
            "files": files,
            "total_size": share["total_size"],
        },
    )


@router.get("/api/public/shares/{token}/manifest")
def public_manifest(token: str):
    share = fetch_share_by_token(token)
    if not share or share["status"] != "active":
        raise HTTPException(status_code=404, detail="共享不存在或已失效")
    files = share_manifest(share)
    return {
        "name": share["name"],
        "token": share["token"],
        "storage_type": share["storage_type"],
        "files": files,
        "total_size": share["total_size"],
        "file_count": share["file_count"],
    }


@router.get("/api/public/shares/{token}/download")
def public_download(token: str, request: Request, path: str = ""):
    share = fetch_share_by_token(token)
    if not share or share["status"] != "active":
        raise HTTPException(status_code=404, detail="共享不存在或已失效")
    item = find_file(share, path)
    response = stream_share_file(share, item["path"])
    log_download(
        share["id"],
        None,
        item["path"],
        int(item.get("size") or 0),
        client_ip(request),
    )
    db.execute("UPDATE shares SET downloads = downloads + 1 WHERE id = ?", (share["id"],))
    return response
