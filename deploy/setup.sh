#!/usr/bin/env bash
#
# One-shot server setup for the Investment Tracker bot.
# Target: a fresh Ubuntu 24.04 server, run as root.
#
#   curl -fsSL https://raw.githubusercontent.com/voncont9000/investment-tracker/main/deploy/setup.sh | bash
#
# Safe to re-run: it updates the code and restarts the service without
# touching your .env or your database.

set -euo pipefail

REPO_URL="https://github.com/voncont9000/investment-tracker.git"
APP_DIR="/opt/investment-tracker"
APP_USER="tracker"
SERVICE="investment-tracker"

echo "==> Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "==> Creating service user '${APP_USER}'"
# System account with no login shell — it only ever runs the bot.
id -u "${APP_USER}" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "${APP_USER}"

if [ -d "${APP_DIR}/.git" ]; then
    echo "==> Updating existing checkout"
    git -C "${APP_DIR}" fetch --quiet origin
    git -C "${APP_DIR}" reset --hard --quiet origin/main
else
    echo "==> Cloning ${REPO_URL}"
    git clone --quiet "${REPO_URL}" "${APP_DIR}"
fi

echo "==> Installing Python dependencies"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --quiet --upgrade pip
"${APP_DIR}/.venv/bin/pip" install --quiet -r "${APP_DIR}/requirements.txt"

# --- Credentials -----------------------------------------------------------
# Never overwrite an existing .env; re-running setup must not clobber
# credentials or force the user to paste them again.
if [ ! -f "${APP_DIR}/.env" ]; then
    echo
    echo "==> Configuration needed"
    read -rp "    Telegram bot token: " BOT_TOKEN
    read -rp "    Telegram chat ID:   " CHAT_ID

    cp "${APP_DIR}/.env.example" "${APP_DIR}/.env"
    sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=${BOT_TOKEN}|" "${APP_DIR}/.env"
    sed -i "s|^TELEGRAM_CHAT_ID=.*|TELEGRAM_CHAT_ID=${CHAT_ID}|" "${APP_DIR}/.env"
    echo "    Wrote ${APP_DIR}/.env"
else
    echo "==> Keeping existing .env"
fi

chmod 600 "${APP_DIR}/.env"

echo "==> Initializing database"
mkdir -p "${APP_DIR}/data"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/scripts/init_db.py"

echo "==> Setting ownership"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}"

echo "==> Installing systemd service"
cp "${APP_DIR}/deploy/${SERVICE}.service" "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable --quiet "${SERVICE}"
systemctl restart "${SERVICE}"

sleep 3
echo
if systemctl is-active --quiet "${SERVICE}"; then
    echo "✅ ${SERVICE} is running."
    echo
    echo "   Logs:    journalctl -u ${SERVICE} -f"
    echo "   Restart: systemctl restart ${SERVICE}"
    echo "   Stop:    systemctl stop ${SERVICE}"
    echo
    echo "   Message your bot on Telegram to confirm it responds."
else
    echo "❌ ${SERVICE} failed to start. Recent logs:"
    echo
    journalctl -u "${SERVICE}" -n 30 --no-pager
    exit 1
fi
