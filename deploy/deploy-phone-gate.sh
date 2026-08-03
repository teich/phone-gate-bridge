#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/phone-gate-bridge}"
APP_USER="${APP_USER:-gatebridge}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-phone-gate-webhook.service}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${ENV_FILE:-/etc/phone-gate-bridge/phone-gate-bridge.env}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/$SERVICE_NAME}"

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "ERROR: $APP_DIR is not a git checkout"
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "ERROR: user '$APP_USER' does not exist"
  exit 1
fi

previous_commit="${PHONE_GATE_PREVIOUS_COMMIT:-$(sudo -u "$APP_USER" git -C "$APP_DIR" rev-parse HEAD)}"
rollback_armed=false
package_installed=false
unit_installed=false
service_restarted=false

install_checkout() {
  local release_dir
  local release_zip
  release_dir="$(sudo -u "$APP_USER" mktemp -d /tmp/phone-gate-release.XXXXXX)"
  release_zip="$release_dir/release.zip"
  if ! sudo -u "$APP_USER" git -C "$APP_DIR" archive \
    --format=zip \
    --output="$release_zip" \
    HEAD; then
    rm -f "$release_zip"
    rmdir "$release_dir"
    return 1
  fi
  if ! sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install \
    --force-reinstall \
    --no-deps \
    "$release_zip"; then
    rm -f "$release_zip"
    rmdir "$release_dir"
    return 1
  fi
  rm -f "$release_zip"
  rmdir "$release_dir"
}

rollback() {
  local exit_status="${1:-1}"
  local reason="${2:-deployment failed}"
  trap - ERR
  set +e
  echo "ERROR: $reason; rolling back to $previous_commit" >&2

  sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "$previous_commit"
  if "$package_installed"; then
    install_checkout
  fi
  if "$unit_installed"; then
    install -m 644 "$APP_DIR/deploy/systemd/phone-gate-webhook.service" "$UNIT_FILE"
    systemctl daemon-reload
  fi
  if "$service_restarted"; then
    systemctl restart "$SERVICE_NAME"
    if ! systemctl is-active --quiet "$SERVICE_NAME"; then
      echo "ERROR: rollback service did not start" >&2
    fi
  fi
  exit "$exit_status"
}

on_error() {
  local exit_status="$?"
  local line="$1"
  if "$rollback_armed"; then
    rollback "$exit_status" "deployment command failed at line $line"
  fi
  exit "$exit_status"
}

trap 'on_error "$LINENO"' ERR

echo "Deploying $APP_DIR from origin/$BRANCH ..."
if [[ "${PHONE_GATE_DEPLOY_REEXECED:-0}" != "1" ]]; then
  sudo -u "$APP_USER" git -C "$APP_DIR" fetch --all --prune
  rollback_armed=true
  sudo -u "$APP_USER" git -C "$APP_DIR" reset --hard "origin/$BRANCH"

  # Continue with the just-fetched deploy logic. This keeps future changes to
  # this script, the unit, and rollback checks effective in the same rollout.
  exec env \
    PHONE_GATE_DEPLOY_REEXECED=1 \
    PHONE_GATE_PREVIOUS_COMMIT="$previous_commit" \
    "$APP_DIR/deploy/deploy-phone-gate.sh"
fi
rollback_armed=true

if [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
  echo "Creating virtualenv at $APP_DIR/.venv ..."
  sudo -u "$APP_USER" "$PYTHON_BIN" -m venv "$APP_DIR/.venv"
fi

echo "Validating environment file ..."
"$APP_DIR/deploy/validate-env.sh" "$ENV_FILE"

echo "Validating bundled dashboard assets ..."
sudo -u "$APP_USER" env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$APP_DIR/src" \
  "$PYTHON_BIN" -c \
  'from gate_bridge.dashboard import missing_dashboard_assets; missing = missing_dashboard_assets(); assert not missing, f"missing dashboard assets: {missing}"'

echo "Running backend tests ..."
sudo -u "$APP_USER" env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$APP_DIR/src" \
  "$PYTHON_BIN" -m unittest discover -s "$APP_DIR/tests" -p 'test_*.py'

echo "Installing immutable Python package into virtualenv ..."
package_installed=true
install_checkout

echo "Installing systemd unit ..."
unit_installed=true
install -m 644 "$APP_DIR/deploy/systemd/phone-gate-webhook.service" "$UNIT_FILE"
systemctl daemon-reload

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
health_port="${WEBHOOK_BIND_PORT:-8080}"

echo "Restarting systemd service $SERVICE_NAME ..."
service_restarted=true
if ! systemctl restart "$SERVICE_NAME"; then
  rollback 1 "service restart failed"
fi
if ! systemctl is-active --quiet "$SERVICE_NAME"; then
  rollback 1 "service did not become active"
fi
health_url="http://127.0.0.1:${health_port}/healthz"
health_ok=false
for _attempt in {1..10}; do
  if curl --fail --silent --max-time 2 "$health_url" >/dev/null; then
    health_ok=true
    break
  fi
  sleep 1
done
if ! "$health_ok"; then
  rollback 1 "health check failed"
fi
systemctl --no-pager --full status "$SERVICE_NAME"

rollback_armed=false
trap - ERR
echo "Done."
