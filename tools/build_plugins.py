"""Build plugin zip packages and the plugin marketplace index."""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugin_src"
DIST = ROOT / "plugin_dist"
DOWNLOADS = DIST / "downloads"
ICONS = DIST / "icons"

PLUGINS = [
    {
        "folder": "memory_optimizer",
        "category": "系统工具",
        "glyph": "◍",
    },
    {
        "folder": "domain_proxy",
        "category": "网络工具",
        "glyph": "↗",
    },
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def build_zip(folder: str) -> Path:
    source = SOURCE / folder
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    target = DOWNLOADS / f"{folder}.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            arcname = (Path(folder) / path.relative_to(source)).as_posix()
            archive.write(path, arcname)
    return target


def main() -> int:
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    items = []
    for meta in PLUGINS:
        folder = meta["folder"]
        source = SOURCE / folder
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        package = build_zip(folder)
        icon_source = source / "icon.png"
        icon_target = ICONS / f"{folder}.png"
        if icon_source.is_file():
            shutil.copy2(icon_source, icon_target)
        items.append(
            {
                "id": manifest["id"],
                "name": manifest["name"],
                "version": manifest["version"],
                "description": manifest["description"],
                "requirements": manifest["requirements"],
                "category": meta["category"],
                "glyph": meta["glyph"],
                "download_url": f"downloads/{package.name}",
                "sha256": sha256(package),
                "size": package.stat().st_size,
                "icon_url": f"icons/{icon_target.name}" if icon_target.is_file() else "",
            }
        )
    payload = {"plugins": items}
    (DIST / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for item in items:
        print(item["id"], item["version"], item["size"], item["sha256"][:16])
    print("index", DIST / "index.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
