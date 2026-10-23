# NexHub 服务端部署教程

本文部署的是仓库中的 `server/` 目录，即 **NexHub ECS 后端**：

- FastAPI API
- Web 管理后台（`/admin`）
- SQLite 数据库
- 阿里云 OSS 代理
- 头像本地存储
- nps 状态探测与临时共享元数据

客户端（Windows 桌面端）不在此文档的部署范围内。

> 推荐生产环境：**Ubuntu 22.04 / 24.04 + Nginx + systemd + HTTPS**，后端只监听 `127.0.0.1:8000`，由 Nginx 对外提供 `80/443`。  
> 本文以 Ubuntu 为例；Windows 本机快速启动见文末。

## 1. 部署前准备

### 1.1 服务器配置

最低可用配置：

- 1 核 CPU / 1 GB 内存（仅小规模使用）
- 建议 2 核 / 2 GB 以上
- 系统盘至少预留 10 GB 可用空间

实际磁盘需求取决于：

- OSS 上传时的临时文件（`data/tmp`）
- 未配置 OSS 时的本地回退文件（`data/oss_files`）
- 用户头像（`data/avatars`）
- SQLite 数据库和日志

> OSS 只负责最终文件存储，**上传过程中仍会先在 ECS 临时落盘**，所以不要只看 OSS 容量。

### 1.2 域名和端口

生产环境建议准备一个域名，例如：

```text
hub.example.com
```

端口规划：

| 端口 | 用途 | 是否对公网开放 |
| --- | --- | --- |
| 22 | SSH | 仅管理 IP |
| 80 | HTTP / ACME 证书 | 是 |
| 443 | HTTPS | 是 |
| 8000 | FastAPI 内部端口 | **否**，只监听 127.0.0.1 |
| 8024 | nps Bridge（可选） | 仅需要临时穿透/RDP 隧道时开放 |

如果使用阿里云/腾讯云安全组，请在安全组中放行 `80/443`；`8000` 不要放行到公网。

### 1.3 可选组件

- **阿里云 OSS**：长期缓存共享文件。未配置时后端会回退到 ECS 本地磁盘，只适合开发或小规模试用。
- **nps 服务端**：临时穿透共享、远程文件主机需要。NexHub 仓库不包含 nps 服务端程序，需要单独部署。
- **HTTPS 证书**：推荐 Let's Encrypt + Certbot。

## 2. 安装系统依赖

以下命令使用 Ubuntu 的 `apt`：

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx sqlite3 curl git nano openssl ufw
python3 --version
```

NexHub 服务端要求 **Python 3.10+**。Ubuntu 22.04 默认是 3.10，Ubuntu 24.04 默认是 3.12，均满足要求。

### 2.1 创建运行用户

不要把服务端长期以 `root` 运行：

```bash
sudo useradd --system --create-home --home-dir /var/lib/nexhub \
  --shell /usr/sbin/nologin nexhub
```

创建代码目录和数据目录：

```bash
sudo mkdir -p /opt/nexhub
sudo mkdir -p /var/lib/nexhub
sudo chown -R nexhub:nexhub /opt/nexhub /var/lib/nexhub
```

### 2.2 放置代码

假设整个项目放在 `/opt/nexhub`，最终目录应为：

```text
/opt/nexhub/
├── client/
├── server/
├── docs/
└── README.md
```

可以用 Git 拉取：

```bash
sudo -u nexhub git clone <你的仓库地址> /opt/nexhub
```

也可以先把项目上传到服务器，再执行：

```bash
sudo mkdir -p /opt/nexhub
sudo cp -a /path/to/NexHub/. /opt/nexhub/
sudo chown -R nexhub:nexhub /opt/nexhub
```

确认服务端存在：

```bash
ls /opt/nexhub/server/run.py
ls /opt/nexhub/server/requirements.txt
```

## 3. 建立 Python 虚拟环境

```bash
cd /opt/nexhub/server
sudo -u nexhub python3 -m venv .venv
sudo -u nexhub .venv/bin/python -m pip install --upgrade pip
sudo -u nexhub .venv/bin/pip install -r requirements.txt
```

验证依赖：

```bash
sudo -u nexhub .venv/bin/python -c "import fastapi, uvicorn, oss2; print('deps ok')"
```

如果安装 `oss2` 时编译失败，先确认服务器可以访问 PyPI，并升级构建工具：

```bash
sudo apt install -y build-essential libssl-dev
sudo -u nexhub .venv/bin/pip install --upgrade pip setuptools wheel
sudo -u nexhub .venv/bin/pip install -r requirements.txt
```

## 4. 配置 `.env`

复制模板：

```bash
cd /opt/nexhub/server
sudo -u nexhub cp .env.example .env
sudo chown nexhub:nexhub .env
sudo chmod 600 .env
```

编辑：

```bash
sudo -u nexhub nano .env
```

### 4.1 生产环境推荐配置

把下面的占位内容替换成实际值：

```dotenv
# 只监听内网，由 Nginx 反向代理
NEXHUB_HOST=127.0.0.1
NEXHUB_PORT=8000

# 数据和 SQLite 放到独立目录，便于备份和升级
NEXHUB_DATA_DIR=/var/lib/nexhub

# 首次初始化管理员密码（只在数据库没有用户时生效）
NEXHUB_ADMIN_USERNAME=admin
NEXHUB_ADMIN_PASSWORD=CHANGE_ME_ADMIN_PASSWORD
NEXHUB_DEMO_PASSWORD=CHANGE_ME_DEMO_PASSWORD

# Token 签名密钥
NEXHUB_SECRET_KEY=请替换为随机长字符串
NEXHUB_TOKEN_TTL_HOURS=168
# 先用 HTTP 自测时保持 false；完成 HTTPS 后改为 true
NEXHUB_COOKIE_SECURE=false

# 网页分享链接使用的公网地址，不要带结尾斜杠
NEXHUB_WEB_BASE=https://hub.example.com

# 管理后台「服务端检查更新」更新源（默认 GitHub Releases；可换成镜像/latest.json）
NEXHUB_UPDATE_FEED=https://api.github.com/repos/mouchenguo/NexHub/releases

# 阿里云 OSS（可选；不配置则使用本地回退存储）
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET=your-bucket
OSS_ACCESS_KEY_ID=your-access-key-id
OSS_ACCESS_KEY_SECRET=your-access-key-secret
OSS_PREFIX=nexhub/shares

NEXHUB_OSS_QUOTA_GB=50
NEXHUB_MAX_OSS_BYTES=5368709120

# nps（可选；没有临时穿透/RDP 需求可留空）
NPS_SERVER_ADDR=
NPS_SERVER_PORT=8024
NPS_VKEY=
NPS_API_URL=
NPS_AUTH_KEY=
NPS_DOMAIN=
NPS_AUTO_TUNNEL=1
NPS_TUNNEL_PORT_START=30000
NPS_TUNNEL_PORT_END=30100
```

生成随机密钥：

```bash
openssl rand -hex 32
```

把输出填入 `NEXHUB_SECRET_KEY`。

> **重要：** `CHANGE_ME_*` 只是占位符，部署前必须全部替换；不要直接把示例密码用到公网。  
> 本项目 `.env` 解析器不会处理 `${VAR}` 插值，也不会去掉行尾的 `# 注释`。请把值写成普通文本，不要在值后面追加注释。

### 4.2 几个配置的注意事项

- `NEXHUB_COOKIE_SECURE=true` 只适用于 HTTPS。如果先用 `http://IP:8000` 测试，请暂时设为 `false`，否则管理后台 Cookie 可能无法保存。
- `NEXHUB_WEB_BASE` 用于生成 `/s/<token>` 分享链接。生产环境建议固定填写 HTTPS 域名。
- `NEXHUB_DATA_DIR` 一旦使用后不要随意修改，否则会出现“数据还在、但服务读不到”的情况。
- `NEXHUB_ADMIN_PASSWORD` 和 `NEXHUB_DEMO_PASSWORD` 只在数据库第一次初始化时生效；已有数据库不会因为改 `.env` 自动改密码。
- `NEXHUB_REGISTRATION_AUTO_APPROVE` 默认 `1`，新账号注册后自动审核通过；设为 `0` 后新账号进入待审核状态。首次启动后也可在管理后台「系统设置」中随时切换，无需重启。
- `NEXHUB_UPDATE_FEED` 默认读取 GitHub Releases API，管理后台「系统设置 → 服务端检查更新」会用它比较版本。无法访问 GitHub 时，可改成镜像地址或兼容 `latest.json` 的更新源。检查只做版本提示，不会自动升级服务端。
- `NEXHUB_WEB_BASE`、OSS 配额和 nps 配置在第一次初始化时也会写入数据库；之后应优先在管理后台的 `/admin/settings`、`/admin/oss` 或 `/admin/nps` 修改。
- 如果在管理后台执行过「轮换密钥」，数据库中的 `jwt_secret` 会优先于 `.env` 的 `NEXHUB_SECRET_KEY`。
- 生产环境不要把 `NEXHUB_HOST` 设置成 `0.0.0.0` 后直接暴露 `8000`，推荐使用本文的 Nginx 方案。

### 4.3 数据目录权限

配置文件中的 `NEXHUB_DATA_DIR=/var/lib/nexhub`，服务用户必须能读写：

```bash
sudo chown -R nexhub:nexhub /var/lib/nexhub
sudo chmod 750 /var/lib/nexhub
```

服务第一次启动时会自动创建：

```text
/var/lib/nexhub/
├── nexhub.db
├── tmp/
├── oss_files/
├── avatars/
└── logs/
```

## 5. 首次启动与初始化

先在前台启动，观察报错：

```bash
cd /opt/nexhub/server
sudo -u nexhub .venv/bin/python run.py --host 127.0.0.1 --port 8000
```

看到 Uvicorn 启动日志后，在服务器上执行：

```bash
curl http://127.0.0.1:8000/api/health
```

正常返回示例：

```json
{"status":"ok","service":"nexhub-server","version":"0.1.0-beta","database":"ok"}
```

初始化时会自动创建：

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| `admin` | `.env` 中的 `NEXHUB_ADMIN_PASSWORD` | 管理员 |
| `demo` | `.env` 中的 `NEXHUB_DEMO_PASSWORD` | 普通用户 |

首次登录后请立即：

1. 打开 `http://127.0.0.1:8000/admin`（正式环境使用 HTTPS 域名）；
2. 使用管理员账号登录；
3. 在「用户管理」中重置管理员密码；
4. 确认演示账号是否需要保留，不需要可在管理后台禁用。

> 修改 `.env` 中的管理员密码不会覆盖已有用户密码。


## 6. 配置 systemd 开机自启

前台启动验证通过后，按 `Ctrl+C` 停止，然后创建服务：

```bash
sudo tee /etc/systemd/system/nexhub.service >/dev/null <<'EOF'
[Unit]
Description=NexHub FastAPI Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nexhub
Group=nexhub
WorkingDirectory=/opt/nexhub/server
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/nexhub/server/.venv/bin/python /opt/nexhub/server/run.py --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
```

启用并启动：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nexhub
sudo systemctl status nexhub --no-pager
```

查看日志：

```bash
sudo journalctl -u nexhub -f
```

常用操作：

```bash
sudo systemctl restart nexhub
sudo systemctl stop nexhub
sudo systemctl start nexhub
```

> 建议只运行一个 Uvicorn 进程。SQLite 数据库、临时文件和进程内 OSS 锁都适合单进程部署；不要额外加 `--workers`。

## 7. 配置 Nginx 和 HTTPS

### 7.1 创建站点

```bash
sudo tee /etc/nginx/sites-available/nexhub >/dev/null <<'EOF'
server {
    listen 80;
    server_name hub.example.com;

    # 单文件上限为 5GB，给请求体留一点余量
    client_max_body_size 5120m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 大文件上传/下载：关闭代理缓冲，避免把文件堆在 Nginx
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
EOF
```

把 `hub.example.com` 换成你的域名，然后启用：

```bash
sudo ln -s /etc/nginx/sites-available/nexhub /etc/nginx/sites-enabled/nexhub
sudo nginx -t
sudo systemctl reload nginx
```

先在 HTTP 下验证：

```bash
curl -sS http://hub.example.com/api/health
```

### 7.2 申请 HTTPS 证书

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d hub.example.com
```

Certbot 会自动改写 Nginx 配置并配置证书续期。验证：

```bash
curl https://hub.example.com/api/health
sudo certbot renew --dry-run
```

然后确认 `.env` 中：

```dotenv
NEXHUB_COOKIE_SECURE=true
NEXHUB_WEB_BASE=https://hub.example.com
```

重启后端：

```bash
sudo systemctl restart nexhub
```

> 如果你的域名还没有解析到 ECS，Certbot 会失败。先在 DNS 添加 A 记录，并等待解析生效。

### 7.3 限制管理后台来源（可选）

如果不希望 `/admin` 对所有人开放，可以在 Nginx 的 `server` 中增加：

```nginx
location /admin {
    allow 203.0.113.10;
    deny all;

    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

把 `203.0.113.10` 换成你的固定公网 IP。配置完成后执行：

```bash
sudo nginx -t
sudo systemctl reload nginx
```


## 8. 防火墙和安全组

Ubuntu 使用 UFW：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
# 只有使用 nps 临时穿透/RDP 隧道时才需要
sudo ufw allow 8024/tcp
sudo ufw enable
sudo ufw status
```

对应云厂商安全组也只需放行：

- `22`：SSH，建议限制来源 IP
- `80/443`：Web 和 HTTPS
- `8024`：nps Bridge，按需开放
- **不要**对公网放行 `8000`

## 9. 配置阿里云 OSS（可选）

不配置 OSS 也可以启动，服务端会使用本地回退目录，但生产环境不建议长期这样运行。

### 9.1 阿里云侧

1. 创建 OSS Bucket，建议与 ECS 同地域，Bucket 权限设为**私有**。
2. 创建 RAM 用户，不要使用阿里云主账号 AccessKey。
3. 授予该 RAM 用户对目标 Bucket（或 `OSS_PREFIX` 前缀）的上传、读取、删除和列举权限。
4. 保存 AccessKey ID / Secret，仅填写到服务端 `.env`。

### 9.2 服务端侧

```dotenv
OSS_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
OSS_BUCKET=your-bucket
OSS_ACCESS_KEY_ID=your-access-key-id
OSS_ACCESS_KEY_SECRET=your-access-key-secret
OSS_PREFIX=nexhub/shares
```

`OSS_ENDPOINT` 要与 Bucket 地域匹配。修改后重启：

```bash
sudo systemctl restart nexhub
```

登录管理后台 `/admin/oss`，确认：

- 运行模式显示为「阿里云 OSS」
- AccessKey 显示「已配置（隐藏）」
- Bucket / Endpoint 正确

> 不要在客户端配置或 README 中写入 AccessKey。客户端始终通过后端上传。

### 9.3 OSS 容量与本地回退

- `NEXHUB_OSS_QUOTA_GB` 控制管理后台显示的全局配额。
- OSS 共享总大小必须小于 5GB；达到配额时后端会清理最早创建的活动 OSS 共享。
- 本地回退目录是 `NEXHUB_DATA_DIR/oss_files`，需要和数据库一起备份。
- 上传时服务端先用 `NEXHUB_DATA_DIR/tmp` 接收文件，请为临时目录预留空间。

## 10. 配置 nps 内网穿透（可选）

nps 用于：

- 临时穿透共享：客户端内嵌 FastAPI 通过 nps 暴露给公网；
- 远程文件主机：客户端主机守护进程通过 nps 提供文件浏览/下载；
- 管理员在后台查看 Bridge 是否可达。

**NexHub 不包含 nps 服务端。** 你需要单独准备 nps 服务端，按 nps 官方方式安装二进制或 Docker 版本。

### 10.1 网络规划

如果 nps 和 NexHub 部署在同一台 ECS：

- nps 通常也需要使用 `80/443`，会和 Nginx 冲突；
- 建议将 nps 部署到**另一台服务器**，或者为 nps 规划独立端口/独立域名；
- 不建议为了省事直接把 nps 管理端口暴露到公网。

需要保证：

- nps Bridge 端口（默认 `8024`）可被 Windows 客户端访问；
- nps 的 HTTP/HTTPS 入口能访问到客户端建立的隧道；
- nps 使用强 vkey，不使用默认密码。

### 10.2 在 NexHub 管理后台填写

登录 `/admin/nps`：

| 字段 | 含义 | 示例 |
| --- | --- | --- |
| 服务端地址 | nps 公网 IP 或域名，不带 `http://` | `nps.example.com` |
| Bridge 端口 | nps 客户端连接端口 | `8024` |
| vkey | nps 客户端的 vkey | 强随机字符串 |
| 公网访问域名 | 运维展示用公网域名 | `https://share.example.com` |
| nps 管理 API | nps Web API 地址 | `http://127.0.0.1:8081` |
| API auth_key | 与 nps 服务端 `nps.conf` 的 `auth_key` 相同；只在服务端使用 | 强随机字符串 |

保存后，后台会用 TCP 探测 `服务端地址:Bridge端口`。如果显示不可达，优先检查：

- 云安全组/UFW 是否放行 `8024`
- nps 服务是否正在监听
- 地址是否误填了 `http://`
- vkey 是否与 nps 服务端一致

> 管理后台里的「公网访问域名」和「nps 管理 API」主要是服务端记录/展示字段；临时共享由 ECS 自动分配 NPS TCP 端口并发起访问，RDP/NPS 隧道地址在客户端「设置 → NPC 与内网穿透」中维护。

NexHub 的「工具箱 → NPS 隧道管理」需要调用 NPS Web API 自动创建客户端。请确保：

1. nps 的 `nps.conf` 中 `auth_key` 非空，例如使用 `openssl rand -hex 32` 生成；
2. 同一值填入服务端 `.env` 的 `NPS_AUTH_KEY`，或管理后台 `/admin/nps`；
3. 修改 `nps.conf` 后重启 nps；修改 `.env` 后重启 nexhub；
4. 不要把 `NPS_AUTH_KEY` 写入客户端配置，客户端只需要 NPS 地址、Bridge 端口和每条隧道自己的 vkey。

启用 `NPS_AUTO_TUNNEL=1` 后，发布临时共享会按 `NPS_TUNNEL_PORT_START` 到
`NPS_TUNNEL_PORT_END` 自动分配公网端口并创建 NPS TCP 任务；取消或删除共享时
自动删除该任务。安全组需要放行这整个端口范围，例如 `30000-30100`。

### 10.3 客户端侧

在 Windows 客户端「设置 → NPC 与内网穿透」中配置：

- 主机 IP（含端口）：例如 `nps.example.com:8024`
- vkey：与 nps 服务端一致
- 临时共享域名：旧版兼容项，默认留空；ECS 会自动分配公网端口
- RDP/NPS 隧道地址：例如 `rdp.example.com:3389`

NPC 保存的是 `C:\Program Files\npc\conf\npc.conf` 中 `[common]` 的 `server_addr` 和 `vkey`。安装/保存/重启需要管理员权限，会弹出 UAC。

### 10.4 只用 OSS 可以跳过 nps

如果只需要「OSS 长期缓存 + 网页分享」，不需要临时穿透和 RDP 隧道，可以完全不配置 nps；此时后台的 nps 状态显示不可达是正常的。


## 11. 客户端连接服务端

服务端启动并完成域名/HTTPS 配置后，在 Windows 客户端「设置 → ECS 服务器」填写：

```text
服务器 IP / 域名：hub.example.com
端口：443
协议：HTTPS
```

不要填写内部端口 `8000`，除非你明确在用本机/内网直连。保存后点击左下角「请登录」，在居中的内嵌面板中登录或注册；登录面板也可以直接配置 IP、端口和 HTTP/HTTPS。默认自动审核通过，关闭自动审核后需管理员在 `/admin/users` 中通过注册申请。

登录后打开「设置 → NPC 与内网穿透」，点击「一键自动配置 NPC」：客户端会请求 ECS 创建/复用当前设备的 NPS 客户端和 TCP 隧道，自动安装 NPC、写入本机 `npc.conf`、重启 NPC 服务，并在界面显示分配到的公网 RDP 地址。首次操作需要 Windows 管理员授权。

验证顺序：

1. 浏览器打开 `https://hub.example.com/api/health`，确认返回 `status: ok`；
2. 打开 `https://hub.example.com/admin` 登录管理后台；
3. 客户端使用管理员、普通用户登录，或在登录面板切换到「注册」创建新账号；
4. 在客户端「我的共享」发布一个小文件，测试 OSS 上传；
5. 在网页端打开分享链接，测试下载；
6. 如启用了 nps，再测试临时穿透共享和 RDP 隧道。

客户端默认以演示预览模式启动，不依赖 ECS，不能代表服务端部署是否成功；请点击左下角「账户」入口登录或注册后再验证云端功能。关闭自动审核时，新注册账号会显示「等待管理员审核」，管理员通过后才能登录。

## 12. 备份与恢复

### 12.1 需要备份的内容

- SQLite 数据库：`/var/lib/nexhub/nexhub.db`
- 头像：`/var/lib/nexhub/avatars/`
- 本地 OSS 回退文件（如果未配置 OSS）：`/var/lib/nexhub/oss_files/`
- 服务端配置：`/opt/nexhub/server/.env`
- nps 服务端配置（如果单独部署）

OSS 中的实际文件由阿里云负责持久化，但数据库里的共享元数据仍然必须备份。

### 12.2 停机备份示例

```bash
sudo mkdir -p /var/backups/nexhub
sudo systemctl stop nexhub
sudo tar -czf "/var/backups/nexhub/nexhub-$(date +%F-%H%M).tar.gz" \
  -C /var/lib nexhub \
  -C /opt/nexhub/server .env
sudo systemctl start nexhub
```

> 备份包包含 `.env` 中的 OSS 密钥和签名密钥，请设置严格权限，不要上传到公开网盘或 Git。

如果服务必须持续在线，也可以只备份 SQLite：

```bash
sudo -u nexhub sqlite3 /var/lib/nexhub/nexhub.db \
  ".backup '/var/backups/nexhub/nexhub-$(date +%F-%H%M).db'"
```

### 12.3 恢复

```bash
sudo systemctl stop nexhub
# 先确认备份文件存在，再分别恢复到两个目录
sudo mkdir -p /var/lib /opt/nexhub/server
sudo tar -xzf /var/backups/nexhub/nexhub-YYYY-MM-DD-HHMM.tar.gz -C /var/lib nexhub
sudo tar -xzf /var/backups/nexhub/nexhub-YYYY-MM-DD-HHMM.tar.gz -C /opt/nexhub/server .env
sudo chown -R nexhub:nexhub /var/lib/nexhub /opt/nexhub/server/.env
sudo systemctl start nexhub
```

恢复后检查 `/api/health`、管理员登录和最近共享记录。

## 13. 升级服务端

先登录管理后台打开「系统设置 → 服务端检查更新」，确认当前版本和最新版本，并下载对应的 `NexHub-Server-Deploy.zip`。更新检查不会自动覆盖运行中的服务端文件。

升级前先备份数据库和 `.env`：

```bash
cd /opt/nexhub
sudo -u nexhub git pull
sudo -u nexhub server/.venv/bin/pip install -r server/requirements.txt
sudo systemctl restart nexhub
```

启动时会自动执行数据库初始化；当前版本会自动补充 `avatar_ext` 等新增字段。

升级后检查：

```bash
curl http://127.0.0.1:8000/api/health
sudo journalctl -u nexhub -n 100 --no-pager
```

如果升级涉及配置项变化，先更新 `.env` 或管理后台配置，再重启。

## 14. 常见问题

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| Nginx 返回 502 | 后端未启动，或端口不是 8000 | `systemctl status nexhub`；`curl 127.0.0.1:8000/api/health` |
| 上传大文件返回 413 | Nginx 请求体上限太小 | 设置 `client_max_body_size 5120m;` 并 reload |
| 管理后台登录后马上掉线 | HTTPS 下 Cookie 设置不匹配 | HTTPS 设置 `NEXHUB_COOKIE_SECURE=true`；HTTP 测试时设 `false` |
| 修改 `.env` 后配置没变 | 域名/配额/nps 在首次初始化时已写入数据库；OSS 凭证在启动时读取 | 在管理后台修改；OSS 修改后重启后端；不要重复初始化数据库 |
| OSS 显示本地回退 | OSS 环境变量不完整、SDK 未安装或服务未重启 | 检查四项 OSS 变量；`pip install -r requirements.txt`；重启 |
| OSS 上传失败 | RAM 权限不足、Endpoint/Bucket 不匹配 | 检查 Bucket 地域、私有权限和 RAM 授权 |
| 共享超过 5GB 被拒绝 | OSS 单共享上限 | 拆分共享，或改用临时穿透 |
| nps 显示不可达 | 8024 未放行、nps 未启动、地址填错 | 检查安全组、`ss -lntp`、地址不要带 `http://` |
| 数据库 permission denied | `/var/lib/nexhub` 不属于 `nexhub` | `chown -R nexhub:nexhub /var/lib/nexhub` |
| 旧 token 全部失效 | 管理后台轮换了签名密钥 | 重新登录即可，这是预期行为 |
| `.env` 配置值异常 | 值后面写了行尾注释 | 本项目不解析行尾注释，删除 `#` 后的内容 |

## 15. Windows 本机快速启动（开发/内网测试）

如果只是把服务端跑在自己的 Windows 电脑上联调：

```powershell
cd D:\Code\Project\NexHub\server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
python run.py --host 0.0.0.0 --port 8000
```

如果 PowerShell 禁止执行激活脚本：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

客户端服务器地址可填：

```text
http://127.0.0.1:8000
```

局域网其他电脑访问时，使用这台电脑的局域网 IP，并在 Windows 防火墙放行 `8000`。这只是开发/内网用法，不建议直接把 `8000` 暴露到公网。

Windows 生产部署建议使用 Linux ECS + Nginx + HTTPS；RDP 由客户端调用系统原生 mstsc.exe，服务端不保存 RDP 凭据。

## 16. 上线检查清单

- [ ] 服务器时间、时区正确，DNS 已解析到 ECS
- [ ] Python 3.10+，依赖安装在独立 `.venv`
- [ ] `.env` 权限为 `600`，未提交到 Git
- [ ] `NEXHUB_ADMIN_PASSWORD` 已换成强密码
- [ ] `NEXHUB_SECRET_KEY` 已换成随机长字符串
- [ ] `NEXHUB_DATA_DIR` 权限正确，磁盘余量充足
- [ ] systemd 已 `enable`，服务异常会自动重启
- [ ] Nginx 已配置 `client_max_body_size`、流式代理和超时
- [ ] HTTPS 证书有效，`NEXHUB_COOKIE_SECURE=true`
- [ ] 公网未开放 `8000`，`/admin` 有 IP 白名单或 VPN 保护
- [ ] OSS Bucket 为私有，使用 RAM 子账号且权限最小化
- [ ] 数据库、头像、`.env` 已纳入备份
- [ ] 登录、发布、下载、头像上传、管理员重置密码均已测试
- [ ] 演示账号 `demo` 已禁用或已修改密码
- [ ] `/api/docs` 和 `/admin` 按需限制来源（Nginx IP 白名单 / VPN）
- [ ] nps（如启用）使用强 vkey，管理端口未暴露公网

完成以上步骤后，NexHub 服务端即可供 Windows 客户端和网页分享页使用。

