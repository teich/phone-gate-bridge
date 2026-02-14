# phone-gate-bridge

Local gate unlock worker for UniFi Access (UDM Pro) using the developer API.

## What this does now

- Sends `PUT /api/v1/developer/doors/:id/unlock` to your UDM Pro Access API.
- Supports optional `actor_id`, `actor_name`, and `extra` payload fields.
- Provides a CLI command (`gate-open`) for smoke testing from the LXC.

## Requirements

- Debian 13 with Python 3.11+
- Network access from LXC to your UDM Pro on port `12445`
- UniFi Access API token with `edit:space` permission

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Set token in environment:

```bash
export UNIFI_ACCESS_API_TOKEN='YOUR_TOKEN_HERE'
```

List doors:

```bash
gate-open list-doors \
  --host 192.168.1.1 \
  --insecure
```

Run unlock test:

```bash
gate-open unlock \
  --host 192.168.1.1 \
  --door-name Gate \
  --actor-id phone-gate-bridge \
  --actor-name "Phone Gate Bridge" \
  --extra-json '{"source":"manual-smoke-test"}' \
  --insecure
```

Notes:

- Keep `--insecure` only while using self-signed/local certs; remove it once trusted certs are configured.
- If successful, CLI prints API JSON response or `Unlock request accepted.`

## Run tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py' -v
```

## Next step (after this passes)

- Add a systemd service and timer-safe wrapper for gate command.
- Add Cloudflare tunnel + webhook receiver service.
- Add telephone trigger flow that calls the local unlock service.
