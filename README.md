# phone-gate-bridge

Phone-call to UniFi Access gate unlock bridge.

## What it does

- Twilio sends inbound call webhooks to `/twilio/voice`.
- Service checks caller ID against a callers file (`ALLOWED_CALLERS_FILE`).
- If allowed, caller must press `1` (DTMF) to open.
- Only when digit `1` is received on `/twilio/voice/confirm`, it resolves door by name (default `Gate`) and unlocks through UniFi Access API.
- It answers the call with TwiML:
  - Step 1 (allowed): "Press 1 now to open the gate."
  - Step 2 (allowed + pressed 1): "The gate is now open."
  - Blocked: "This incoming number is not authorized for this gate."

## Project commands

Install in a virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

List doors:

```bash
set -a; source .env; set +a
gate-open list-doors --host "$UNIFI_HOST" --insecure
```

Unlock by door name:

```bash
set -a; source .env; set +a
gate-open unlock \
  --host "$UNIFI_HOST" \
  --door-name Gate \
  --actor-id phone-gate-bridge \
  --actor-name "Phone Gate Bridge" \
  --extra-json '{"source":"manual-smoke-test"}' \
  --insecure
```

Run webhook locally:

```bash
set -a; source .env; set +a
gate-webhook
```

Health check:

```bash
curl -sS http://127.0.0.1:8080/healthz
```

Dashboard (local networks only):

```bash
curl -sS http://127.0.0.1:8080/dashboard
```

## Environment file

Use two env files:

- Local/dev: `/opt/phone-gate-bridge/.env`
- systemd runtime: `/etc/phone-gate-bridge/phone-gate-bridge.env`

Start from `.env.example` and create your local `.env`.

Required values:

- `UNIFI_HOST`
- `UNIFI_ACCESS_API_TOKEN`
- `PUBLIC_BASE_URL` (example: `https://gate.teich.network`)
- `TWILIO_AUTH_TOKEN` (from Twilio Console)
- `TWILIO_TTS_VOICE` (optional, default: `Polly.Joanna-Neural`)
- `ALLOWED_CALLERS_FILE` (example: `/etc/phone-gate-bridge/allowed-callers.toml`)
- `DASHBOARD_ALLOWED_CIDRS` (optional, default: `127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`)
- `DASHBOARD_RECENT_EVENTS_LIMIT` (optional, default: `100`)
- `DASHBOARD_DB_PATH` (optional, default: `var/activity.sqlite3`)

Caller allowlist file:

- Copy `/opt/phone-gate-bridge/deploy/config/allowed-callers.toml.example` to `/etc/phone-gate-bridge/allowed-callers.toml`.
- Edit that file to add/remove numbers and metadata (`name`, `notes`, `enabled`).
- The webhook reads this file on each request, so number changes do not require redeploy.

Important:

- Keep `.env` out of git.
- If your UDM cert is self-signed, set `UNIFI_INSECURE_TLS=true`.
- After any `.env` change, sync it to the systemd env file:

```bash
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/.env /etc/phone-gate-bridge/phone-gate-bridge.env
```

- Validate runtime env file before restart:

```bash
sudo /opt/phone-gate-bridge/deploy/validate-env.sh /etc/phone-gate-bridge/phone-gate-bridge.env
```

- Install callers file:

```bash
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/deploy/config/allowed-callers.toml.example /etc/phone-gate-bridge/allowed-callers.toml
sudoedit /etc/phone-gate-bridge/allowed-callers.toml
```

## Twilio configuration

In Twilio Console for your number:

1. Open `Phone Numbers` -> your number.
2. Under `Voice Configuration`:
- `A CALL COMES IN` -> `Webhook`.
- URL: `https://<your-cloudflare-hostname>/twilio/voice`
- Method: `HTTP POST`
3. Save.

No Twilio Function/Studio flow is required.

Security note:

- Incoming webhook requests are validated with `X-Twilio-Signature`.
- `PUBLIC_BASE_URL` must exactly match the external URL Twilio calls.
- Voice quality can be improved by setting `TWILIO_TTS_VOICE` (for example `Polly.Joanna-Neural` or another Twilio-supported voice).
- Dashboard requests are only served when source IP matches `DASHBOARD_ALLOWED_CIDRS`.
- Dashboard activity persists in SQLite at `DASHBOARD_DB_PATH` and survives service restarts.

## Cloudflare tunnel configuration

Template: `deploy/cloudflared/phone-gate.yml`

1. Install `cloudflared` on Debian 13.
2. Authenticate:

```bash
cloudflared tunnel login
```

3. Create tunnel:

```bash
cloudflared tunnel create phone-gate
```

4. Route DNS:

```bash
cloudflared tunnel route dns phone-gate gate.example.com
```

5. Copy template into place and set real values:

```bash
sudo mkdir -p /etc/cloudflared
sudo cp deploy/cloudflared/phone-gate.yml /etc/cloudflared/phone-gate.yml
sudoedit /etc/cloudflared/phone-gate.yml
```

6. Keep public ingress scoped to Twilio paths:
- `/twilio/*` should route to `http://127.0.0.1:8080`.
- Do not expose `/dashboard` in tunnel ingress.

7. If you want to view dashboard from another LAN host, bind webhook to LAN:
- Set `WEBHOOK_BIND_HOST=0.0.0.0`.
- Restrict access with firewall rules and `DASHBOARD_ALLOWED_CIDRS`.

## systemd services (Debian 13)

### 1) App service

First-time server bootstrap (git clone + venv):

```bash
sudo useradd --system --home /opt/phone-gate-bridge --shell /usr/sbin/nologin gatebridge || true
sudo mkdir -p /opt/phone-gate-bridge /etc/phone-gate-bridge
sudo chown -R gatebridge:gatebridge /opt/phone-gate-bridge
sudo -u gatebridge git clone <YOUR_GITHUB_REPO_URL> /opt/phone-gate-bridge
sudo -u gatebridge python3 -m venv /opt/phone-gate-bridge/.venv
sudo -u gatebridge /opt/phone-gate-bridge/.venv/bin/pip install -e /opt/phone-gate-bridge
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/.env /etc/phone-gate-bridge/phone-gate-bridge.env
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/deploy/config/allowed-callers.toml.example /etc/phone-gate-bridge/allowed-callers.toml
sudo chown root:gatebridge /etc/phone-gate-bridge/phone-gate-bridge.env
sudo chmod 640 /etc/phone-gate-bridge/phone-gate-bridge.env
sudo cp /opt/phone-gate-bridge/deploy/systemd/phone-gate-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phone-gate-webhook.service
```

Update deploys (recommended):

```bash
sudo /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

Optional variables for deploy script:

```bash
sudo APP_DIR=/opt/phone-gate-bridge APP_USER=gatebridge BRANCH=main SERVICE_NAME=phone-gate-webhook.service ENV_FILE=/etc/phone-gate-bridge/phone-gate-bridge.env /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

Legacy/manual approach:

```bash
sudo useradd --system --home /opt/phone-gate-bridge --shell /usr/sbin/nologin gatebridge || true
sudo mkdir -p /opt/phone-gate-bridge /etc/phone-gate-bridge
sudo rsync -a --delete ./ /opt/phone-gate-bridge/
sudo -u gatebridge python3 -m venv /opt/phone-gate-bridge/.venv
sudo -u gatebridge /opt/phone-gate-bridge/.venv/bin/pip install -e /opt/phone-gate-bridge
sudo install -m 640 -o root -g gatebridge .env /etc/phone-gate-bridge/phone-gate-bridge.env
sudo install -m 640 -o root -g gatebridge deploy/config/allowed-callers.toml.example /etc/phone-gate-bridge/allowed-callers.toml
sudo chown -R gatebridge:gatebridge /opt/phone-gate-bridge
sudo chown root:gatebridge /etc/phone-gate-bridge/phone-gate-bridge.env
sudo chmod 640 /etc/phone-gate-bridge/phone-gate-bridge.env
sudo cp deploy/systemd/phone-gate-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phone-gate-webhook.service
sudo systemctl status phone-gate-webhook.service
```

### 2) Cloudflare tunnel service

```bash
sudo useradd --system --home /var/lib/cloudflared --shell /usr/sbin/nologin cloudflared || true
sudo cp deploy/systemd/cloudflared-phone-gate.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared-phone-gate.service
sudo systemctl status cloudflared-phone-gate.service
```

## Quick end-to-end test

1. Confirm webhook health local:

```bash
curl -sS http://127.0.0.1:8080/healthz
```

2. Confirm tunnel hostname responds (from external network):

```bash
curl -sS -X POST "https://gate.example.com/twilio/voice" \
  --data-urlencode "From=+17075551111" \
  --data-urlencode "CallSid=TEST123"
```

Expected response contains a `<Gather ... action="/twilio/voice/confirm">`.

Note: manual curl tests will return `403 forbidden` unless you include a valid Twilio signature.

3. Simulate pressing 1:

```bash
curl -sS -X POST "https://gate.example.com/twilio/voice/confirm" \
  --data-urlencode "From=+17075551111" \
  --data-urlencode "CallSid=TEST123" \
  --data-urlencode "Digits=1"
```

4. Call Twilio number from an allowed caller. You should hear prompt, press `1`, then hear: "The gate is now open."

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```
