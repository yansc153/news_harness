#!/usr/bin/env bash
# News Harness V1 — VPS deployment script
# Run on VPS after first git clone.
# Usage: sudo bash scripts/vps_deploy.sh <github-repo-url> <domain>
set -euo pipefail

REPO_URL="${1:?Usage: $0 <github-repo-url> <domain>}"
DOMAIN="${2:?Usage: $0 <github-repo-url> <domain>}"
APP_DIR="/opt/news_harness"
SECRETS_DIR="/run/news-harness/secrets"
ENV_FILE="/run/news-harness/news_harness.env"

echo "=== News Harness V1 VPS Deploy ==="

# 1. Create system user
echo ">>> Creating news-harness user..."
if ! id news-harness &>/dev/null; then
    useradd --system --no-create-home --shell /usr/sbin/nologin news-harness
fi

# 2. Clone repo
echo ">>> Cloning repo..."
if [ -d "$APP_DIR" ]; then
    echo "    $APP_DIR exists, pulling..."
    cd "$APP_DIR"
    git pull
else
    git clone "$REPO_URL" "$APP_DIR"
fi
chown -R news-harness:news-harness "$APP_DIR"

# 3. Create secrets directory
echo ">>> Setting up secrets..."
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
chown news-harness:news-harness "$SECRETS_DIR"

# 4. Create env file for systemd
echo ">>> Creating env file at $ENV_FILE..."
mkdir -p "$(dirname "$ENV_FILE")"
cp "$APP_DIR/configs/secrets.vps.env" "$ENV_FILE"
chmod 600 "$ENV_FILE"
chown news-harness:news-harness "$ENV_FILE"

echo "    ⚠️  Place your secret files at:"
echo "       $SECRETS_DIR/deepseek-api-key"
echo "       $SECRETS_DIR/x-list-reader-cookie"
echo "       $SECRETS_DIR/reddit-reader-cookie"

# 5. Install systemd units
echo ">>> Installing systemd units..."
cp "$APP_DIR/configs/systemd/news-harness-cycle.service" /etc/systemd/system/
cp "$APP_DIR/configs/systemd/news-harness-cycle.timer" /etc/systemd/system/
cp "$APP_DIR/configs/systemd/news-harness-site.service" /etc/systemd/system/
cp "$APP_DIR/configs/systemd/news-harness-healthcheck.service" /etc/systemd/system/
systemctl daemon-reload

# 6. Enable and start services
echo ">>> Enabling services..."
systemctl enable news-harness-cycle.timer
systemctl enable news-harness-site.service
systemctl start news-harness-cycle.timer
systemctl start news-harness-site.service

# 7. Caddy
echo ">>> Setting up Caddy..."
if ! command -v caddy &>/dev/null; then
    echo "    Installing Caddy..."
    apt-get install -y caddy
fi
cp "$APP_DIR/configs/Caddyfile" /etc/caddy/Caddyfile
sed -i "s/YOUR_DOMAIN/$DOMAIN/g" /etc/caddy/Caddyfile
systemctl enable caddy
systemctl reload caddy

# 8. Verify
echo ""
echo "=== Deploy complete ==="
echo "Check status:"
echo "  systemctl status news-harness-cycle.timer"
echo "  systemctl status news-harness-site.service"
echo "  systemctl status caddy"
echo ""
echo "Dashboard: https://$DOMAIN/"
