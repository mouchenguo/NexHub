"""把服务端程序包 + 下载站资源部署到 Linux 主机（SSH）。

用法::

    # 密钥登录（推荐）
    python tools/deploy_server.py --host 47.108.130.177 --user root --key C:\\keys\\ecs.pem

    # 密码登录
    set NEXHUB_SSH_PASSWORD=xxxx
    python tools/deploy_server.py --host 47.108.130.177 --user root

默认动作：上传 ``release/NexHub-Server-Bundle.zip`` 与 ``install_server.sh`` 并执行安装
（自动备份旧 app、pip 安装依赖、重启 nexhub、健康检查），然后把 ``release/latest.json``
里列出的客户端/服务端产物同步到 ``/opt/nexhub/server/downloads``，并同步插件广场资源。

可用 ``--skip-install`` / ``--skip-assets`` / ``--skip-plugins`` 只做其中一部分。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "release"
CLIENT_DIST = ROOT / "client" / "dist"
PLUGIN_DIST = ROOT / "plugin_dist"


def log(message: str) -> None:
    print(message, flush=True)


def local_path(name: str) -> Path | None:
    """在 release/、client/dist/、client/dist/installer/ 中定位产物。"""
    for candidate in (
        RELEASE / name,
        CLIENT_DIST / name,
        CLIENT_DIST / "installer" / name,
    ):
        if candidate.is_file():
            return candidate
    return None


def connect(args: argparse.Namespace) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    options = dict(
        hostname=args.host,
        port=args.port,
        username=args.user,
        timeout=20,
        banner_timeout=30,
        auth_timeout=30,
    )
    if args.key:
        key = Path(args.key).expanduser()
        if not key.is_file():
            raise SystemExit(f"私钥不存在：{key}")
        pkey = paramiko.RSAKey.from_private_key_file(str(key))
        client.connect(pkey=pkey, look_for_keys=False, allow_agent=False, **options)
    else:
        password = args.password or os.environ.get("NEXHUB_SSH_PASSWORD", "")
        if not password:
            raise SystemExit("缺少凭据：请用 --key 指定私钥，或设置 NEXHUB_SSH_PASSWORD")
        client.connect(password=password, look_for_keys=False, allow_agent=False, **options)
    log(f"SSH_OK {args.user}@{args.host}:{args.port}")
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int = 900) -> int:
    stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    stdin.close()
    out = stdout.read().decode("utf-8", "replace").strip()
    err = stderr.read().decode("utf-8", "replace").strip()
    code = stdout.channel.recv_exit_status()
    if out:
        log(out)
    if err:
        log("STDERR: " + err)
    return code


def put(sftp: paramiko.SFTPClient, local: Path, remote: str) -> None:
    size = local.stat().st_size
    last = [-1]

    def progress(done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 100
        if pct >= last[0] + 20 or done == total:
            last[0] = pct
            log(f"  {local.name}: {pct}% ({done}/{total})")

    log(f"upload {remote} {size}")
    sftp.put(str(local), remote, callback=progress)


def install_bundle(client: paramiko.SSHClient) -> None:
    bundle = RELEASE / "NexHub-Server-Bundle.zip"
    script = RELEASE / "install_server.sh"
    if not bundle.is_file() or not script.is_file():
        raise SystemExit("缺少 release/NexHub-Server-Bundle.zip 或 install_server.sh")
    sftp = client.open_sftp()
    try:
        put(sftp, bundle, "/tmp/nexhub-server-bundle.zip")
        put(sftp, script, "/tmp/nexhub-install.sh")
    finally:
        sftp.close()
    log("== 安装服务端 ==")
    code = run(
        client,
        "chmod +x /tmp/nexhub-install.sh && "
        "APP_DIR=/opt/nexhub/server BUNDLE=/tmp/nexhub-server-bundle.zip bash /tmp/nexhub-install.sh",
    )
    log(f"install_exit={code}")


def upload_assets(client: paramiko.SSHClient, downloads_dir: str) -> None:
    manifest = json.loads((RELEASE / "latest.json").read_text(encoding="utf-8"))
    names = [
        manifest.get("installer_url"),
        manifest.get("download_url"),
        manifest.get("portable_url"),
        manifest.get("server_download_url"),
        manifest.get("server_bundle_url"),
        "latest.json",
    ]
    sftp = client.open_sftp()
    try:
        for name in names:
            if not name:
                continue
            path = local_path(Path(str(name)).name)
            if path is None:
                log(f"skip missing {name}")
                continue
            put(sftp, path, f"{downloads_dir}/{path.name}")
    finally:
        sftp.close()


def upload_plugins(client: paramiko.SSHClient, remote: str) -> None:
    if not PLUGIN_DIST.is_dir():
        log("skip plugins: plugin_dist 不存在")
        return
    sftp = client.open_sftp()
    try:
        for path in sorted(PLUGIN_DIST.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(PLUGIN_DIST).as_posix()
            put(sftp, path, f"{remote}/{relative}")
    finally:
        sftp.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="部署 NexHub 服务端与下载站资源")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="root")
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--key", default="", help="私钥路径（RSA PEM）")
    parser.add_argument("--password", default="", help="登录密码，或用 NEXHUB_SSH_PASSWORD")
    parser.add_argument("--app-dir", default="/opt/nexhub/server")
    parser.add_argument("--plugin-dist", default="/opt/nexhub/plugin-market/dist")
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--skip-assets", action="store_true")
    parser.add_argument("--skip-plugins", action="store_true")
    args = parser.parse_args()

    client = connect(args)
    try:
        if not args.skip_install:
            install_bundle(client)
        if not args.skip_assets:
            log("== 同步下载站资源 ==")
            upload_assets(client, f"{args.app_dir}/downloads")
        if not args.skip_plugins:
            log("== 同步插件广场 ==")
            upload_plugins(client, args.plugin_dist)
        log("== 修正权限 ==")
        run(
            client,
            f"chown -R nexhub:nexhub {args.app_dir} {args.plugin_dist} 2>/dev/null; "
            f"chmod -R a+rX {args.plugin_dist} {args.app_dir}/downloads; "
            f"stat -c '%n %s' {args.app_dir}/downloads/latest.json",
        )
        log("== 远端核对 ==")
        run(client, "curl -s --max-time 10 http://127.0.0.1:8000/api/health")
        run(client, "curl -s --max-time 10 http://127.0.0.1:8000/nexhub/downloads/latest.json")
        run(client, "systemctl is-active nexhub; systemctl show -p Restart nexhub")
    finally:
        client.close()
    log("DEPLOY_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
