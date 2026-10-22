"""启动 ECS 后端。

    python run.py --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SERVER_ROOT = Path(__file__).resolve().parent
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

import uvicorn  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="NexHub ECS 服务端")
    parser.add_argument("--host", default=os.environ.get("NEXHUB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("NEXHUB_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(SERVER_ROOT / "app")] if args.reload else None,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
