#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/nexhub/server}"
BUNDLE="${BUNDLE:-/tmp/nexhub-server-bundle.zip}"

if [ ! -f "$BUNDLE" ]; then
  echo "Server bundle not found: $BUNDLE" >&2
  exit 1
fi

if ! command -v unzip >/dev/null 2>&1; then
  apt-get update -y
  apt-get install -y unzip
fi

if ! id nexhub >/dev/null 2>&1; then
  useradd --system --home /opt/nexhub --shell /usr/sbin/nologin nexhub
fi

mkdir -p "$APP_DIR"
if [ -d "$APP_DIR/app" ]; then
  BACKUP="$APP_DIR/app.bak-$(date +%Y%m%d%H%M%S)"
  cp -a "$APP_DIR/app" "$BACKUP"
  echo "Previous app backed up to $BACKUP"
fi

unzip -oq "$BUNDLE" -d "$APP_DIR"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env from .env.example; fill in production secrets."
fi

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

if [ ! -f /etc/systemd/system/nexhub.service ]; then
  cat > /etc/systemd/system/nexhub.service <<'EOF'
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
ExecStart=/opt/nexhub/server/.venv/bin/python /opt/nexhub/server/run.py --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF
fi

chown -R nexhub:nexhub "$APP_DIR"
systemctl daemon-reload
systemctl enable nexhub >/dev/null 2>&1 || true
systemctl restart nexhub
sleep 3

if curl -fsS http://127.0.0.1:8000/api/health >/dev/null; then
  echo "NexHub server deployed successfully."
  curl -sS http://127.0.0.1:8000/api/health
else
  echo "Server health check failed. Check: journalctl -u nexhub -n 80 --no-pager" >&2
  exit 1
fi
