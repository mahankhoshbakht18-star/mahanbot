#!/bin/bash
# ==============================================================================
# MAHANBOT 2.0 & OTP RELAY - AUTOMATED SERVER DEPLOYMENT SCRIPT
# Target: Ubuntu 22.04 / 24.04 LTS (otp.mahanvip.ir)
# ==============================================================================

set -e

echo "🚀 [1/6] Updating system packages and installing prerequisites..."
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx curl wget git ufw unzip

INSTALL_DIR="/var/www/mahanbot"
DOWNLOADS_DIR="/var/www/mahanbot/downloads"

echo "📂 [2/6] Setting up project directory at $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
mkdir -p "$DOWNLOADS_DIR"

# Copy files if running from within project repository
if [ -f "server.py" ]; then
    cp -r * "$INSTALL_DIR/"
fi

cd "$INSTALL_DIR"

echo "🐍 [3/6] Setting up Python virtual environment..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt || true
pip install -r requirements-otp.txt

# Install Playwright browser dependencies for headless server execution
python -m playwright install --with-deps chromium || true

echo "⚙️ [4/6] Installing systemd services..."
cp deploy/mahan-otp.service /etc/systemd/system/
cp deploy/mahanbot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable mahan-otp.service
systemctl enable mahanbot.service
systemctl restart mahan-otp.service
systemctl restart mahanbot.service

echo "🌐 [5/6] Configuring Nginx reverse proxy for otp.mahanvip.ir..."
cp deploy/nginx-mahanvip.conf /etc/nginx/sites-available/otp.mahanvip.ir
ln -sf /etc/nginx/sites-available/otp.mahanvip.ir /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

echo "🔒 [6/6] Setting up SSL via Let's Encrypt (if DNS pointed)..."
# certbot --nginx -d otp.mahanvip.ir --non-interactive --agree-tos -m admin@mahanvip.ir || echo "Please run certbot after pointing DNS!"

echo "=============================================================================="
echo "✅ MAHANBOT 2.0 & OTP Server successfully deployed!"
echo "   - OTP API: https://otp.mahanvip.ir"
echo "   - Health Check: https://otp.mahanvip.ir/health"
echo "   - Version API: https://otp.mahanvip.ir/api/version"
echo "   - Downloads Folder: $DOWNLOADS_DIR"
echo "   - Download APK: https://otp.mahanvip.ir/downloads/mahan-otp-2.0.1.apk"
echo "   - Download MahanBot: https://otp.mahanvip.ir/downloads/mahanbot-latest.zip"
echo "=============================================================================="
