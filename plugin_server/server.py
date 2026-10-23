"""NexHub 独立插件广场与审核服务（标准库实现）。

对外站点：
  /nexhub               插件广场介绍
  /nexhub/plugin        网页版插件广场
  /nexhub/plugin/api    JSON 清单（客户端）
  /nexhub/plugin/review 插件审核后台（Basic Auth）
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import logging
import mimetypes
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from .market import MAX_ARCHIVE_BYTES, MarketError, PluginMarket
except ImportError:  # 直接以脚本运行时
    from market import MAX_ARCHIVE_BYTES, MarketError, PluginMarket

ROOT = Path(__file__).resolve().parent
DEFAULT_DIST = ROOT.parent / "plugin_dist"
TEMPLATES_DIR = ROOT / "templates"
ASSETS_DIR = ROOT / "assets"
LOGGER = logging.getLogger("nexhub.plugin_server")
MAIN_SITE = os.environ.get("NEXHUB_MAIN_SITE", "http://47.108.130.177:8000/nexhub").rstrip("/")
GITHUB_URL = os.environ.get("NEXHUB_GITHUB_URL", "https://github.com/mouchenguo/NexHub").rstrip("/")
MAX_BODY_BYTES = MAX_ARCHIVE_BYTES + 1024 * 1024


def _human_size(value: int) -> str:
    size = float(max(0, value))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_name(value: str) -> str:
    name = Path(urllib.parse.unquote(value or "")).name
    if re.fullmatch(r"[A-Za-z0-9._-]{1,160}", name) and not name.startswith("."):
        return name
    return ""


def _render(template: str, **values: object) -> bytes:
    text = (TEMPLATES_DIR / template).read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", str(value))
    return text.encode("utf-8")


def _plugin_cards(plugins: list[dict]) -> str:
    if not plugins:
        return '<div class="empty-state">还没有已发布插件。上传并通过审核后会显示在这里。</div>'
    cards: list[str] = []
    for plugin in plugins:
        name = html.escape(str(plugin.get("name") or plugin.get("id") or "未命名插件"))
        version = html.escape(str(plugin.get("version") or ""))
        description = html.escape(str(plugin.get("description") or "暂无描述"))
        category = html.escape(str(plugin.get("category") or "其他"))
        requirements = html.escape(str(plugin.get("requirements") or "无额外依赖"))
        glyph = html.escape(str(plugin.get("glyph") or "✦"))
        raw_download = str(plugin.get("download_url") or "")
        download_name = Path(raw_download).name
        download_href = f"/nexhub/plugin/downloads/{urllib.parse.quote(download_name)}" if download_name else "#"
        icon_url = str(plugin.get("icon_url") or "")
        icon_name = Path(icon_url).name if icon_url else ""
        icon = (
            f'<img src="/nexhub/plugin/icons/{urllib.parse.quote(icon_name)}" alt="">'
            if icon_name and re.fullmatch(r"[A-Za-z0-9._-]{1,160}", icon_name)
            else glyph
        )
        size = _human_size(int(plugin.get("size") or 0))
        search_text = html.escape(f"{name} {category} {description}", quote=True)
        cards.append(
            f'<article class="plugin-card" data-search="{search_text}">'
            f'<div class="plugin-card-head"><span class="plugin-icon">{icon}</span>'
            f'<span><h3>{name}</h3><span class="version">v{version} · {category}</span></span></div>'
            f'<p>{description}</p>'
            f'<div class="plugin-meta"><span>{requirements}</span><span>{size}</span></div>'
            f'<div class="card-actions"><a class="btn btn-primary" href="{download_href}" download>下载插件</a>'
            f'<span class="version">SHA-256 已发布</span></div></article>'
        )
    return "".join(cards)


def _plugin_preview(plugins: list[dict]) -> str:
    if not plugins:
        return '<div class="empty-state">还没有插件，去广场上传第一个吧。</div>'
    rows: list[str] = []
    for plugin in plugins[:2]:
        name = html.escape(str(plugin.get("name") or plugin.get("id") or "插件"))
        glyph = html.escape(str(plugin.get("glyph") or "✦"))
        category = html.escape(str(plugin.get("category") or "其他"))
        rows.append(
            f'<div class="plugin-row"><span class="plugin-glyph">{glyph}</span>'
            f'<span><strong>{name}</strong><small>{category}</small></span>'
            '<a href="/nexhub/plugin">查看</a></div>'
        )
    return "".join(rows)


class PluginHandler(BaseHTTPRequestHandler):
    server_version = "NexHubPluginMarket/2.0"

    @property
    def market(self) -> PluginMarket:
        return self.server.market  # type: ignore[attr-defined]

    def _send_bytes(
        self,
        data: bytes,
        content_type: str,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_html(
        self,
        data: bytes,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
        no_store: bool = False,
    ) -> None:
        headers = {"Cache-Control": "no-store" if no_store else "no-cache"}
        headers.update(extra_headers or {})
        self._send_bytes(data, "text/html; charset=utf-8", status, headers)

    def _send_json(self, data: object, status: int = 200) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_bytes(payload, "application/json; charset=utf-8", status)

    def _redirect(self, location: str, status: int = 303) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, path: Path, content_type: str, support_range: bool = False) -> None:
        if not path.is_file():
            self._send_json({"detail": "not found"}, 404)
            return
        size = path.stat().st_size
        range_header = self.headers.get("Range", "") if support_range else ""
        start, end = 0, size - 1
        status = 200
        if range_header.startswith("bytes="):
            try:
                start_text, _, end_text = range_header.split("=", 1)[1].partition("-")
                start = max(0, int(start_text))
                end = int(end_text) if end_text.strip().isdigit() else size - 1
                end = min(end, size - 1)
                if start >= size or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            except (ValueError, IndexError):
                start, end, status = 0, size - 1, 200
        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes" if support_range else "none")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(1024 * 256, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _wants_json(self, query: dict[str, list[str]]) -> bool:
        if (query.get("format") or [""])[0].lower() == "json":
            return True
        accept = self.headers.get("Accept", "").lower()
        if "text/html" in accept:
            return False
        if "*/*" in accept:
            return True
        return "application/json" in accept or not accept

    def _auth_review(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            self._send_html(
                "<h1>需要审核凭据</h1><p>插件审核后台使用 HTTP Basic Auth。</p>".encode("utf-8"),
                401,
                extra_headers={"WWW-Authenticate": 'Basic realm="NexHub Plugin Review", charset="UTF-8"'},
            )
            return None
        try:
            decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
            user, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            user, password = "", ""
        if not self.market.check_review_auth(user, password):
            self._send_html(
                "<h1>凭据错误</h1><p>请检查插件审核账号和密码。</p>".encode("utf-8"),
                401,
                extra_headers={"WWW-Authenticate": 'Basic realm="NexHub Plugin Review", charset="UTF-8"'},
            )
            return None
        return user or "admin"

    def _intro_page(self) -> None:
        plugins = self.market.plugins()
        pending = self.market.pending()
        page = _render(
            "intro.html",
            PLUGIN_COUNT=len(plugins),
            PENDING_COUNT=len(pending),
            PLUGIN_CARDS=_plugin_preview(plugins),
            MAIN_SITE=MAIN_SITE,
            GITHUB_URL=GITHUB_URL,
        )
        self._send_html(page)

    def _upload_message(self, query: dict[str, list[str]]) -> str:
        status = (query.get("upload") or [""])[0]
        name = html.escape((query.get("name") or [""])[0])
        message = html.escape((query.get("message") or [""])[0])
        if status == "pending":
            return f'<div class="flash ok">插件「{name}」已进入审核队列，审核通过后会出现在广场。</div>'
        if status == "error":
            return f'<div class="flash error">上传失败：{message or "请检查插件包"}</div>'
        return ""

    def _market_page(self, query: dict[str, list[str]] | None = None) -> None:
        plugins = self.market.plugins()
        page = _render(
            "market.html",
            PLUGIN_COUNT=len(plugins),
            PLUGIN_CARDS=_plugin_cards(plugins),
            UPLOAD_MESSAGE=self._upload_message(query or {}),
            MAIN_SITE=MAIN_SITE,
            GITHUB_URL=GITHUB_URL,
        )
        self._send_html(page)

    @staticmethod
    def _review_item(item: dict) -> str:
        manifest = item.get("manifest") or {}
        upload_id = html.escape(str(item.get("upload_id") or ""), quote=True)
        name = html.escape(str(manifest.get("name") or "未命名插件"))
        plugin_id = html.escape(str(manifest.get("id") or ""))
        version = html.escape(str(manifest.get("version") or ""))
        description = html.escape(str(manifest.get("description") or ""))
        requirements = html.escape(str(manifest.get("requirements") or "无额外依赖"))
        uploader = html.escape(str(item.get("uploader") or "匿名"))
        notes = html.escape(str(item.get("notes") or "无备注"))
        submitted = html.escape(str(item.get("submitted_at") or ""))
        sha = html.escape(str(item.get("sha256") or ""))
        size = _human_size(int(item.get("size") or 0))
        return (
            '<article class="review-item">'
            f'<h3>{name} <span class="pending-tag">待审核</span></h3>'
            f'<p><b>ID</b> {plugin_id} · <b>版本</b> v{version} · <b>大小</b> {size}</p>'
            f'<p>{description}</p>'
            f'<p><b>依赖</b> {requirements}</p>'
            f'<p><b>上传者</b> {uploader} · <b>时间</b> {submitted}</p>'
            f'<p><b>备注</b> {notes}</p>'
            f'<p style="word-break:break-all"><b>SHA-256</b> {sha}</p>'
            f'<form class="review-actions" method="post" action="/nexhub/plugin/review">'
            f'<input type="hidden" name="upload_id" value="{upload_id}">'
            '<button class="btn btn-primary" type="submit" name="action" value="approve">通过并发布</button>'
            '<button class="btn btn-danger" type="submit" name="action" value="reject">拒绝</button>'
            '</form></article>'
        )

    def _review_page(self, reviewer: str, message: str = "", message_type: str = "ok") -> None:
        pending = self.market.pending()
        if pending:
            pending_html = "".join(self._review_item(item) for item in pending)
        else:
            pending_html = '<div class="empty-state">当前没有待审核插件。</div>'
        logs = []
        for entry in self.market.review_log(20):
            action = "通过" if entry.get("action") == "approve" else "拒绝"
            logs.append(
                '<li>'
                f'{html.escape(str(entry.get("time") or ""))} · {action} · '
                f'{html.escape(str(entry.get("name") or entry.get("plugin_id") or ""))} '
                f'v{html.escape(str(entry.get("version") or ""))} · {html.escape(str(entry.get("reviewer") or ""))}'
                '</li>'
            )
        audit_html = "".join(logs) or '<li>暂无审核记录</li>'
        flash = f'<div class="flash {message_type}">{html.escape(message)}</div>' if message else ""
        page = _render(
            "review.html",
            PENDING_COUNT=len(pending),
            PENDING_ITEMS=pending_html,
            AUDIT_ITEMS=audit_html,
            REVIEW_MESSAGE=flash,
        )
        self._send_html(page, no_store=True)

    def _not_found(self) -> None:
        accept = self.headers.get("Accept", "").lower()
        if "text/html" in accept:
            self._send_html("<h1>404</h1><p>页面不存在。</p>".encode("utf-8"), 404)
        else:
            self._send_json({"detail": "not found"}, 404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        if path == "/":
            self._redirect("/nexhub", 307)
            return
        if path in ("/nexhub", "/nexhub/"):
            if path.endswith("/"):
                self._redirect("/nexhub", 307)
            else:
                self._intro_page()
            return
        if path in ("/nexhub/plugin", "/nexhub/plugin/"):
            if self._wants_json(query):
                self._send_json({"plugins": self.market.plugins()})
            else:
                self._market_page(query)
            return
        if path in ("/nexhub/plugin/api", "/nexhub/plugin/api/"):
            self._send_json({"plugins": self.market.plugins(), "stats": self.market.stats()})
            return
        if path in ("/nexhub/plugin/review", "/nexhub/plugin/review/"):
            reviewer = self._auth_review()
            if reviewer is not None:
                message = (query.get("msg") or [""])[0]
                message_type = (query.get("type") or ["ok"])[0]
                self._review_page(reviewer, message, message_type if message_type in ("ok", "error") else "ok")
            return
        if path.startswith("/nexhub/plugin/downloads/"):
            name = _safe_name(path.rsplit("/", 1)[-1])
            self._send_file(self.market.downloads_dir / name, "application/zip", support_range=True)
            return
        if path.startswith("/nexhub/plugin/icons/"):
            name = _safe_name(path.rsplit("/", 1)[-1])
            self._send_file(self.market.icons_dir / name, "image/png")
            return
        if path.startswith("/nexhub/assets/"):
            name = _safe_name(path.rsplit("/", 1)[-1])
            self._send_file(ASSETS_DIR / name, mimetypes.guess_type(name)[0] or "application/octet-stream")
            return
        if path == "/health":
            self._send_json({"status": "ok", "service": "nexhub-plugin-market", **self.market.stats()})
            return
        self._not_found()

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def _read_body(self) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send_json({"detail": "invalid content length"}, 400)
            return None
        if length <= 0:
            self._send_json({"detail": "empty request"}, 400)
            return None
        if length > MAX_BODY_BYTES:
            self._send_json({"detail": "request too large"}, 413)
            return None
        return self.rfile.read(length)

    @staticmethod
    def _parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, bytes], dict[str, str]]:
        if "multipart/form-data" not in content_type.lower():
            raise MarketError("上传请求必须使用 multipart/form-data")
        boundary_match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type, re.I)
        boundary = (boundary_match.group(1) or boundary_match.group(2)).strip() if boundary_match else ""
        if not boundary:
            raise MarketError("上传请求缺少 multipart boundary")
        fields: dict[str, bytes] = {}
        filenames: dict[str, str] = {}
        for raw in body.split(("--" + boundary).encode("utf-8")):
            if raw in (b"", b"--", b"--\r\n"):
                continue
            if raw.startswith(b"\r\n"):
                raw = raw[2:]
            if raw.endswith(b"\r\n"):
                raw = raw[:-2]
            head, separator, data = raw.partition(b"\r\n\r\n")
            if not separator:
                continue
            disposition = ""
            for line in head.decode("utf-8", "replace").split("\r\n"):
                if line.lower().startswith("content-disposition:"):
                    disposition = line.split(":", 1)[1]
                    break
            name_match = re.search(r'name="([^"]*)"', disposition)
            if not name_match:
                continue
            name = name_match.group(1)
            fields[name] = data
            filename_match = re.search(r'filename="([^"]*)"', disposition)
            if filename_match:
                filenames[name] = filename_match.group(1)
        return fields, filenames

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        body = self._read_body()
        if body is None:
            return
        if path in ("/nexhub/plugin/upload", "/nexhub/plugin/upload/"):
            try:
                fields, filenames = self._parse_multipart(body, self.headers.get("Content-Type", ""))
                package = fields.get("file") or b""
                filename = filenames.get("file") or "plugin.zip"
                uploader = (fields.get("uploader") or b"").decode("utf-8", "replace")
                notes = (fields.get("notes") or b"").decode("utf-8", "replace")
                meta = self.market.queue_upload(package, filename, uploader, notes)
                plugin_name = str((meta.get("manifest") or {}).get("name") or filename)
                location = "/nexhub/plugin?" + urllib.parse.urlencode(
                    {"upload": "pending", "name": plugin_name}
                )
                self._redirect(location)
            except (MarketError, OSError) as exc:
                location = "/nexhub/plugin?" + urllib.parse.urlencode(
                    {"upload": "error", "message": str(exc)}
                )
                self._redirect(location)
            return
        if path in ("/nexhub/plugin/review", "/nexhub/plugin/review/"):
            reviewer = self._auth_review()
            if reviewer is None:
                return
            try:
                form = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
                action = (form.get("action") or [""])[0]
                upload_id = (form.get("upload_id") or [""])[0]
                if action == "approve":
                    entry = self.market.approve(upload_id, reviewer)
                    message = f"已发布 {entry.get('name')} v{entry.get('version')}"
                elif action == "reject":
                    self.market.reject(upload_id, reviewer)
                    message = "已拒绝该插件上传"
                else:
                    raise MarketError("未知审核操作")
                location = "/nexhub/plugin/review?" + urllib.parse.urlencode({"msg": message, "type": "ok"})
            except (MarketError, OSError) as exc:
                location = "/nexhub/plugin/review?" + urllib.parse.urlencode(
                    {"msg": str(exc), "type": "error"}
                )
            self._redirect(location)
            return
        self._not_found()

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        LOGGER.info("%s - %s", self.address_string(), format % args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    parser.add_argument("--base", type=Path, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    market = PluginMarket(args.dist, args.base)
    server = ThreadingHTTPServer((args.host, args.port), PluginHandler)
    server.daemon_threads = True
    server.market = market  # type: ignore[attr-defined]
    user, _ = market.review_credentials()
    LOGGER.info("plugin market listening on %s:%s, dist=%s", args.host, args.port, market.dist)
    LOGGER.info("review user=%s, password file=%s", user, market.password_file)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
