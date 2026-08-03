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

Install the dashboard toolchain and build the committed production bundle:

```bash
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run build
```

Node is only needed for dashboard development. The Debian host runs the
compiled assets from the Python package and does not need Node or npm.

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

## Admin dashboard

Open `http://<host>:8080/dashboard` from a machine on an allowed network
(`DASHBOARD_ALLOWED_CIDRS`). It is **read-only** and is not exposed through the
Cloudflare tunnel — the tunnel only routes `/twilio/*` and `/healthz`.

It shows:

- **Live gate status** — secured, opening, or held open, with a countdown ring
  and the time the hold expires.
- **Summary tiles** — opens today/this week, distinct callers, denied attempts,
  failures.
- **Gate opens** — hourly for the last 24 hours.
- **Activity** — one row per phone call, filterable, with caller names resolved
  from `ALLOWED_CALLERS_FILE`.

The dashboard is a Vite/React/TypeScript application compiled into the Python
package. Python remains the only production process and owns all Twilio, UniFi,
SQLite, and authorization work.

| Route | Purpose |
|---|---|
| `GET /dashboard` | Compiled React page. |
| `GET /dashboard/assets/*` | Hashed JS/CSS with immutable browser caching. |
| `GET /dashboard/api/state` | JSON state; the page polls it every 3s. |

Every dashboard route is behind the same CIDR check. Page loads are audited as
`dashboard_view`; assets and polled state are not. The API contract is explicitly
versioned, and activity rows carry stable `call:<CallSid>` or `event:<sqlite-id>`
identities. Combined with ignoring poll responses whose semantic state did not
change, React updates only the rows that actually changed instead of rebuilding
the page on every refresh.

### Where gate status comes from

Status comes from the doors listing (`GET /api/v1/developer/doors`, via
`AccessClient.find_door`), cached for a couple of seconds so polling does not
reach the controller at the same rate. That one request carries both the door id
and its live state, so no second lookup is needed.

Two fields drive the display:

| Field | Values | Meaning |
| --- | --- | --- |
| `door_position_status` | `open` / `close` | physical position sensor |
| `door_lock_relay_status` | `unlock` / `lock` | strike relay |

Position wins when it reports `open` — a gate that has swung is open whatever
the relay has since done. A released relay behind a closed gate reads as
**Opening**. Either field can be null on a door with no sensor or no hub; a door
reporting neither reads as **Status unavailable** rather than claiming the gate
is locked, as does an unreachable controller.

A momentary unlock can finish between polls without ever showing on the door
record, so a `unlock_success` in the last 12 seconds is also surfaced as
**Opening** straight from the activity log.

### Hold-open foundation

The activity model already supports finite, clearable holds:

```python
activity.record_hold_open(
    duration_seconds=30 * 60,
    caller=from_number,
    call_sid=call_sid,
)
activity.record_hold_cleared(caller=from_number, reason="manual")
```

The dashboard shows a hold only while it is unexpired, not cleared, and the
physical gate is open/opening. The SQLite event includes absolute start and
expiry times, so restart does not lose the timer.

This is only the durable state foundation. `AccessClient` does not yet implement
a verified UniFi hold-open command, and digit `2` is intentionally not
advertised until that controller operation is confirmed. When it is added,
grant `hold_open` only to callers that should receive that higher-impact action.
The action-reservation table already prevents duplicate Twilio callbacks from
executing a controller command twice. If the service restarts mid-command, the
reservation becomes `unknown` and is never replayed automatically; the caller
is told to check the gate before trying again.

### Dashboard development

Source lives in `frontend/src`. During development, run:

```bash
npm --prefix frontend run dev
```

Vite proxies `/dashboard/api/*` to the Python service on port `8080`. Production
output is committed under `src/gate_bridge/static/dashboard` because the gate
host intentionally has no Node toolchain. Always run the frontend check and
build commands before committing dashboard changes.

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
- `TWILIO_PHONE_NUMBER` (optional; the inbound number in E.164 form, such as
  `+17075551111`, exposed to the LAN-only dashboard as `phone_number`)
- `TWILIO_TTS_VOICE` (optional, default: `Polly.Joanna-Neural`)
- `ALLOWED_CALLERS_FILE` (example: `/etc/phone-gate-bridge/allowed-callers.toml`)
- `DASHBOARD_ALLOWED_CIDRS` (optional, default: `127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`)
- `DASHBOARD_RECENT_EVENTS_LIMIT` (optional, default: `100`)
- `DASHBOARD_DB_PATH` (optional; new installs default to
  `/var/lib/phone-gate-bridge/activity.sqlite3`, while an existing
  `var/activity.sqlite3` is reused automatically)
- `MAX_WEBHOOK_BODY_BYTES` (optional, default: `16384`)

For the existing systemd installation, edit the runtime environment directly;
there is no `/opt/phone-gate-bridge/.env` to sync:

```bash
sudoedit /etc/phone-gate-bridge/phone-gate-bridge.env
# Add: TWILIO_PHONE_NUMBER=+17075551111
sudo /opt/phone-gate-bridge/deploy/validate-env.sh /etc/phone-gate-bridge/phone-gate-bridge.env
sudo /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

Caller allowlist file:

- Copy `/opt/phone-gate-bridge/deploy/config/allowed-callers.toml.example` to `/etc/phone-gate-bridge/allowed-callers.toml`.
- Edit that file to add/remove numbers and metadata (`name`, `notes`, `enabled`,
  `actions`). Existing entries default to `actions = ["open"]`; `hold_open`
  remains explicit and is not implemented yet.
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
sudo -u gatebridge /opt/phone-gate-bridge/.venv/bin/pip install /opt/phone-gate-bridge
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/.env /etc/phone-gate-bridge/phone-gate-bridge.env
sudo install -m 640 -o root -g gatebridge /opt/phone-gate-bridge/deploy/config/allowed-callers.toml.example /etc/phone-gate-bridge/allowed-callers.toml
sudo chown root:gatebridge /etc/phone-gate-bridge/phone-gate-bridge.env
sudo chmod 640 /etc/phone-gate-bridge/phone-gate-bridge.env
sudo cp /opt/phone-gate-bridge/deploy/systemd/phone-gate-webhook.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now phone-gate-webhook.service
```

The unit creates `/var/lib/phone-gate-bridge` via `StateDirectory` and confines
service writes to that directory. It temporarily also permits
`/opt/phone-gate-bridge/var` so existing installations can keep their current
database until it is deliberately migrated.

Update deploys (recommended):

```bash
sudo /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

For the **first deployment of this release**, bootstrap only the new deploy
script before running it. This keeps the current commit available for rollback:

```bash
sudo -u gatebridge git -C /opt/phone-gate-bridge fetch origin main
sudo -u gatebridge git -C /opt/phone-gate-bridge checkout origin/main -- deploy/deploy-phone-gate.sh
sudo /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

After that one-time handoff, the deploy script re-executes its freshly fetched
version automatically on each rollout.

Optional variables for deploy script:

```bash
sudo APP_DIR=/opt/phone-gate-bridge APP_USER=gatebridge BRANCH=main SERVICE_NAME=phone-gate-webhook.service ENV_FILE=/etc/phone-gate-bridge/phone-gate-bridge.env /opt/phone-gate-bridge/deploy/deploy-phone-gate.sh
```

The deploy script validates the committed dashboard bundle, runs backend tests,
installs the exact commit from a clean Git archive, updates the systemd sandbox,
restarts, and checks `/healthz` with a bounded startup window. A failed step,
restart, or health check rolls back the checkout and installed package.

### Moving an existing activity database

Do not simply change `DASHBOARD_DB_PATH`, or the dashboard will appear to lose
its history. For an installation currently using
`/opt/phone-gate-bridge/var/activity.sqlite3`:

```bash
sudo systemctl stop phone-gate-webhook.service
sudo install -d -m 750 -o gatebridge -g gatebridge /var/lib/phone-gate-bridge
sudo -u gatebridge python3 -c \
  'import sqlite3; source=sqlite3.connect("/opt/phone-gate-bridge/var/activity.sqlite3"); target=sqlite3.connect("/var/lib/phone-gate-bridge/activity.sqlite3"); source.backup(target); target.close(); source.close()'
sudoedit /etc/phone-gate-bridge/phone-gate-bridge.env
# Set DASHBOARD_DB_PATH=/var/lib/phone-gate-bridge/activity.sqlite3
sudo systemctl start phone-gate-webhook.service
curl -fsS http://127.0.0.1:8080/healthz
```

Keep the old database until the activity history has been verified in the
dashboard.

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
npm --prefix frontend run check
npm --prefix frontend run build
```

The Python suite covers database migration, hold lifecycle, action idempotency,
the versioned API, compiled assets, and live HTTP handler flows. The frontend
suite checks contract parsing and activity filtering.
