"""NexHub plugin marketplace test/server API (stdlib only)."""
from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIST = ROOT / "plugin_dist"
LOGGER = logging.getLogger("nexhub.plugin_server")


class PluginHandler(BaseHTTPRequestHandler):
    server_version = "NexHubPluginMarket/1.0"

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, content_type: str, support_range: bool = False) -> None:
        if not path.is_file():
            self._send_bytes(b'{"detail":"not found"}', "application/json", 404)
            return
        data = path.read_bytes()
        range_header = self.headers.get("Range", "")
        if support_range and range_header.startswith("bytes="):
            try:
                start_text, _, end_text = range_header.split("=", 1)[1].partition("-")
                start = max(0, int(start_text))
                end = int(end_text) if end_text.strip().isdigit() else len(data) - 1
                end = min(end, len(data) - 1)
                if start >= len(data) or end < start:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{len(data)}")
                    self.end_headers()
                    return
                payload = data[start : end + 1]
                self.send_response(206)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(payload)
                return
            except (ValueError, IndexError):
                pass
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Accept-Ranges", "bytes" if support_range else "none")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlsplit(self.path).path)
        dist: Path = self.server.dist_dir
        if path in ("/nexhub/plugin", "/nexhub/plugin/"):
            index = dist / "index.json"
            self._send_file(index, "application/json; charset=utf-8")
            return
        if path.startswith("/nexhub/plugin/downloads/"):
            name = Path(path.rsplit("/", 1)[-1]).name
            self._send_file(dist / "downloads" / name, "application/zip", support_range=True)
            return
        if path.startswith("/nexhub/plugin/icons/"):
            name = Path(path.rsplit("/", 1)[-1]).name
            self._send_file(dist / "icons" / name, "image/png")
            return
        if path == "/health":
            self._send_bytes(
                json.dumps({"status": "ok", "service": "nexhub-plugin-market"}).encode(),
                "application/json",
            )
            return
        self._send_bytes(b'{"detail":"not found"}', "application/json", 404)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        LOGGER.info("%s - %s", self.address_string(), format % args)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--dist", type=Path, default=DEFAULT_DIST)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), PluginHandler)
    server.dist_dir = args.dist.resolve()
    LOGGER.info("plugin market listening on %s:%s, dist=%s", args.host, args.port, server.dist_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
