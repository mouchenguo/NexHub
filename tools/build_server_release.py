"""Build the server deployment packages from the current server source."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "server"
RELEASE = ROOT / "release"
BUNDLE = RELEASE / "NexHub-Server-Bundle.zip"
DEPLOY = RELEASE / "NexHub-Server-Deploy.zip"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_bundle() -> None:
    temp = BUNDLE.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative in ("run.py", "requirements.txt", ".env.example"):
            archive.write(SERVER / relative, relative)
        for path in sorted((SERVER / "app").rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(SERVER).as_posix())
    temp.replace(BUNDLE)


def build_deploy() -> None:
    temp = DEPLOY.with_suffix(".tmp.zip")
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(BUNDLE, BUNDLE.name)
        for relative in ("install_server.sh", "Deploy-NexHub-Server.bat", "SERVER_DEPLOY.txt"):
            archive.write(RELEASE / relative, relative)
    temp.replace(DEPLOY)


def main() -> int:
    build_bundle()
    build_deploy()
    latest_path = RELEASE / "latest.json"
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    latest["server_sha256"] = sha256(DEPLOY)
    latest_path.write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("bundle", BUNDLE.stat().st_size, sha256(BUNDLE))
    print("deploy", DEPLOY.stat().st_size, sha256(DEPLOY))
    print("latest server_sha256", latest["server_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
