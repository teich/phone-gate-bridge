# phone-gate-bridge

Phone-call to UniFi Access gate unlock bridge.

## What it does

- Twilio sends inbound call webhooks to `/twilio/voice`.
- Service checks caller ID against `ALLOWED_CALLERS`.
- If allowed, it resolves door by name (default `Gate`) and unlocks through UniFi Access API.
- It answers the call with TwiML:
  - Allowed: "The gate is now open."
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

## Environment file

Start from `.env.example` and copy values into your real `.env`.

Required values:

- `UNIFI_HOST`
- `UNIFI_ACCESS_API_TOKEN`
- `ALLOWED_CALLERS` (comma-separated, E.164 recommended)

Important:

- Keep `.env` out of git.
- If your UDM cert is self-signed, set `UNIFI_INSECURE_TLS=true`.

## Twilio configuration

In Twilio Console for your number:

1. Open `Phone Numbers` -> your number.
2. Under `Voice Configuration`:
- `A CALL COMES IN` -> `Webhook`.
- URL: `https://<your-cloudflare-hostname>/twilio/voice`
- Method: `HTTP POST`
3. Save.

Optional: if you want extra control, use a TwiML Bin that redirects to the same URL, but webhook direct is simplest.

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

6. Ensure ingress points to local webhook:
- `service: http://127.0.0.1:8080`

## systemd services (Debian 13)

### 1) App service

```bash
sudo useradd --system --home /opt/phone-gate-bridge --shell /usr/sbin/nologin gatebridge || true
sudo mkdir -p /opt/phone-gate-bridge /etc/phone-gate-bridge
sudo rsync -a --delete ./ /opt/phone-gate-bridge/
python3 -m venv /opt/phone-gate-bridge/.venv
sudo /opt/phone-gate-bridge/.venv/bin/pip install -e /opt/phone-gate-bridge
sudo cp .env /etc/phone-gate-bridge/phone-gate-bridge.env
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

3. Call Twilio number from an allowed caller. You should hear: "The gate is now open."

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```
