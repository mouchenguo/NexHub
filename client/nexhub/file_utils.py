"""文件扫描、大小格式化与安全路径工具。"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

GIB = 1024 ** 3
MIB = 1024 ** 2
OSS_LIMIT_BYTES = 5 * GIB  # 小于 5GB 才允许 OSS 长期缓存


def human_size(num: float | int | None) -> str:
    """把字节数转换为便于阅读的字符串。"""
    if num is None:
        return "--"
    value = float(num)
    if value < 1024:
        return f"{int(value)} B"
    for unit in ("KB", "MB", "GB", "TB"):
        value /= 1024.0
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TB"


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_time(value: str | None, with_time: bool = True) -> str:
    """把 ISO 时间转换为本地可读格式。"""
    dt = parse_iso(value)
    if dt is None:
        return value or "--"
    local = dt.astimezone() if dt.tzinfo else dt
    return local.strftime("%Y-%m-%d %H:%M" if with_time else "%Y-%m-%d")


def relative_time(value: str | None) -> str:
    dt = parse_iso(value)
    if dt is None:
        return "--"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟前"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} 天前"
    return format_time(value, with_time=False)


def safe_parts(relative: str) -> tuple[str, ...]:
    """校验并拆分相对路径，拒绝目录穿越与绝对路径。"""
    rel = (relative or "").replace("\\", "/").lstrip("/")
    pure = PurePosixPath(rel)
    parts = tuple(p for p in pure.parts if p not in ("", "."))
    if not parts or any(p == ".." for p in parts):
        raise ValueError("非法文件路径")
    if pure.is_absolute() or (parts and ":" in parts[0]):
        raise ValueError("非法文件路径")
    return parts


def safe_join(base: Path, relative: str) -> Path:
    """把相对路径安全地拼接到 base 之下，防止目录穿越。"""
    base = base.resolve()
    target = base.joinpath(*safe_parts(relative)).resolve()
    if base != target and base not in target.parents:
        raise ValueError("非法文件路径")
    return target


def collect_manifest(source: str | Path) -> dict:
    """扫描文件或文件夹，生成共享 manifest。

    返回：``{"name", "root", "is_dir", "files": [{"path", "size"}], "total_size", "file_count"}``
    大目录扫描可能耗时，调用方应放在 QThread 中执行。
    """
    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"路径不存在：{path}")

    files: list[dict] = []
    if path.is_file():
        files.append({"path": path.name, "size": path.stat().st_size})
        name = path.name
        root = path.parent
        is_dir = False
    else:
        name = path.name or str(path)
        root = path
        is_dir = True
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                full = Path(dirpath) / filename
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                rel = full.relative_to(path).as_posix()
                files.append({"path": rel, "size": size})

    total = sum(item["size"] for item in files)
    return {
        "name": name,
        "root": str(root),
        "is_dir": is_dir,
        "files": files,
        "total_size": total,
        "file_count": len(files),
    }


def manifest_tree(files: Iterable[dict]) -> dict:
    """把平面文件列表构建成便于展示的目录树。"""
    root: dict = {"name": "", "children": {}, "files": [], "size": 0}
    for item in files:
        parts = safe_parts(item.get("path", ""))
        node = root
        for part in parts[:-1]:
            node = node["children"].setdefault(part, {"name": part, "children": {}, "files": [], "size": 0})
        size = int(item.get("size") or 0)
        node["files"].append({"name": parts[-1], "path": "/".join(parts), "size": size})
        cursor = root
        cursor["size"] += size
        for part in parts[:-1]:
            cursor = cursor["children"][part]
            cursor["size"] += size
    return root


def ensure_unique_path(path: Path) -> Path:
    """若目标文件已存在，自动追加 (1)(2)..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 1000):
        candidate = path.with_name(f"{stem} ({index}){suffix}")
        if not candidate.exists():
            return candidate
    return path
