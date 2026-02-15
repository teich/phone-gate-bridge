#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${1:-/etc/phone-gate-bridge/phone-gate-bridge.env}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: env file not found: $ENV_FILE"
  echo "Hint: copy /opt/phone-gate-bridge/.env to $ENV_FILE"
  exit 1
fi

if [[ ! -r "$ENV_FILE" ]]; then
  echo "ERROR: env file is not readable: $ENV_FILE"
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

required_vars=(
  UNIFI_HOST
  UNIFI_ACCESS_API_TOKEN
  PUBLIC_BASE_URL
  TWILIO_AUTH_TOKEN
  ALLOWED_CALLERS
)

missing=()
for name in "${required_vars[@]}"; do
  if [[ -z "${!name:-}" ]]; then
    missing+=("$name")
  fi
done

if (( ${#missing[@]} > 0 )); then
  echo "ERROR: missing required env vars in $ENV_FILE: ${missing[*]}"
  exit 1
fi

echo "Env file OK: $ENV_FILE"
