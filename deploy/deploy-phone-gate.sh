#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/phone-gate-bridge}"
APP_USER="${APP_USER:-gatebridge}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-phone-gate-webhook.service}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${ENV_FILE:-/etc/phone-gate-bridge/phone-gate-bridge.env}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "ERROR: $APP_DIR is not a git checkout"
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "ERROR: user '$APP_USER' does not exist"
  exit 1
fi

echo "Deploying $APP_DIR from origin/$BRANCH ..."
sudo -u "$APP_USER" git -C "$APP_DIR" fetch --all --prune
sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$BRANCH"

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "Creating virtualenv at $APP_DIR/.venv ..."
  sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

echo "Installing app into virtualenv ..."
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -e "$APP_DIR"

echo "Validating environment file ..."
"$APP_DIR/deploy/validate-env.sh" "$ENV_FILE"

echo "Restarting systemd service $SERVICE_NAME ..."
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME" || true

echo "Done."
