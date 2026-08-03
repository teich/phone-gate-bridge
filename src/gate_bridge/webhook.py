from __future__ import annotations

import base64
import ipaddress
import hashlib
import hmac
import os
import threading
import time
import xml.sax.saxutils as saxutils
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Union
from urllib.parse import parse_qs, urlparse

from gate_bridge.activity import ActivityStore
from gate_bridge.client import AccessApiError, AccessClient
from gate_bridge.dashboard import (
    GateStatus,
    build_dashboard_state,
    build_gate_status,
    render_dashboard_html,
    render_dashboard_json,
)

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:
    tomllib = None


IpNetwork = Union[ipaddress.IPv4Network, ipaddress.IPv6Network]


@dataclass(frozen=True)
class WebhookConfig:
    host: str
    token: str
    door_name: str = "Gate"
    access_port: int = 12445
    timeout: float = 5.0
    verify_tls: bool = True
    actor_id: str = "phone-gate-bridge"
    actor_name: str = "Phone Gate Bridge"
    bind_host: str = "127.0.0.1"
    bind_port: int = 8080
    allowed_callers_file: str = "/etc/phone-gate-bridge/allowed-callers.toml"
    twilio_auth_token: str = ""
    public_base_url: str = ""
    twilio_tts_voice: str = "Polly.Joanna-Neural"
    dashboard_allowed_cidrs: tuple[str, ...] = (
        "127.0.0.1/32",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
    )
    dashboard_recent_events_limit: int = 100
    dashboard_db_path: str = "var/activity.sqlite3"


@dataclass(frozen=True)
class AllowedCaller:
    number: str
    name: str = ""
    enabled: bool = True
    notes: str = ""


def normalize_phone(value: str) -> str:
    value = value.strip()
    if value.startswith("+"):
        return "+" + "".join(ch for ch in value[1:] if ch.isdigit())
    return "".join(ch for ch in value if ch.isdigit())


def load_allowed_callers(path: str) -> tuple[AllowedCaller, ...]:
    parsed: dict
    if tomllib is not None:
        with open(path, "rb") as f:
            parsed = tomllib.load(f)
    else:
        with open(path, "r", encoding="utf-8") as f:
            parsed = _parse_simple_callers_toml(f.read())

    raw_callers = parsed.get("callers")
    if not isinstance(raw_callers, list):
        raise ValueError("allowed callers file must contain [[callers]] entries")

    callers: list[AllowedCaller] = []
    for entry in raw_callers:
        if not isinstance(entry, dict):
            continue
        number_raw = str(entry.get("number", "")).strip()
        number = normalize_phone(number_raw)
        if not number:
            continue
        enabled = bool(entry.get("enabled", True))
        name = str(entry.get("name", "")).strip()
        notes = str(entry.get("notes", "")).strip()
        callers.append(
            AllowedCaller(
                number=number,
                name=name,
                enabled=enabled,
                notes=notes,
            )
        )
    return tuple(callers)


def _parse_simple_callers_toml(text: str) -> dict:
    callers: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[callers]]":
            current = {}
            callers.append(current)
            continue
        if current is None:
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        k = key.strip()
        v = value.strip()
        if v.startswith('"') and v.endswith('"') and len(v) >= 2:
            current[k] = v[1:-1]
        elif v.lower() in {"true", "false"}:
            current[k] = v.lower() == "true"
        else:
            current[k] = v

    return {"callers": callers}


def find_allowed_caller(
    caller: str, allowed_callers: tuple[AllowedCaller, ...]
) -> AllowedCaller | None:
    normalized_caller = normalize_phone(caller)
    if not normalized_caller:
        return None
    for allowed in allowed_callers:
        if allowed.enabled and allowed.number == normalized_caller:
            return allowed
    return None


def parse_cidr_list(raw: str) -> tuple[IpNetwork, ...]:
    networks: list[IpNetwork] = []
    for value in raw.split(","):
        cleaned = value.strip()
        if not cleaned:
            continue
        try:
            network = ipaddress.ip_network(cleaned, strict=False)
        except ValueError as exc:
            raise ValueError(f"invalid dashboard CIDR '{cleaned}'") from exc
        networks.append(network)
    if not networks:
        raise ValueError("DASHBOARD_ALLOWED_CIDRS must contain at least one CIDR")
    return tuple(networks)


def is_ip_allowed(ip_text: str, networks: tuple[IpNetwork, ...]) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text.strip())
    except ValueError:
        return False
    for network in networks:
        if ip.version == network.version and ip in network:
            return True
    return False


def twiml_say(message: str, voice: str = "Polly.Joanna-Neural") -> bytes:
    safe = saxutils.escape(message)
    safe_voice = saxutils.escape(voice)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Say voice=\"{safe_voice}\">{safe}</Say>"
        "<Hangup/>"
        "</Response>"
    ).encode("utf-8")


def twiml_gather(
    prompt: str, action: str, voice: str = "Polly.Joanna-Neural"
) -> bytes:
    safe_prompt = saxutils.escape(prompt)
    safe_action = saxutils.escape(action)
    safe_voice = saxutils.escape(voice)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Gather input=\"dtmf\" numDigits=\"1\" action=\"{safe_action}\" method=\"POST\" timeout=\"5\">"
        f"<Say voice=\"{safe_voice}\">{safe_prompt}</Say>"
        "</Gather>"
        f"<Say voice=\"{safe_voice}\">No input received. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    ).encode("utf-8")


def build_twilio_signature(url: str, form: dict[str, list[str]], auth_token: str) -> str:
    payload = url
    for key in sorted(form.keys()):
        for value in form[key]:
            payload += key + value
    digest = hmac.new(
        auth_token.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def is_valid_twilio_signature(
    signature: str | None,
    url: str,
    form: dict[str, list[str]],
    auth_token: str,
) -> bool:
    if not signature:
        return False
    expected = build_twilio_signature(url, form, auth_token)
    return hmac.compare_digest(signature.strip(), expected)


def load_config_from_env() -> WebhookConfig:
    host = os.getenv("UNIFI_HOST")
    token = os.getenv("UNIFI_ACCESS_API_TOKEN")
    if not host:
        raise ValueError("UNIFI_HOST is required")
    if not token:
        raise ValueError("UNIFI_ACCESS_API_TOKEN is required")
    twilio_auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not twilio_auth_token:
        raise ValueError("TWILIO_AUTH_TOKEN is required")
    public_base_url = os.getenv("PUBLIC_BASE_URL")
    if not public_base_url:
        raise ValueError("PUBLIC_BASE_URL is required")
    dashboard_allowed_cidrs = os.getenv(
        "DASHBOARD_ALLOWED_CIDRS",
        "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
    )
    parse_cidr_list(dashboard_allowed_cidrs)

    insecure = os.getenv("UNIFI_INSECURE_TLS", "false").lower() in {"1", "true", "yes"}
    allowed_callers_file = os.getenv(
        "ALLOWED_CALLERS_FILE",
        "/etc/phone-gate-bridge/allowed-callers.toml",
    )
    if not os.path.isfile(allowed_callers_file):
        raise ValueError(f"ALLOWED_CALLERS_FILE does not exist: {allowed_callers_file}")
    load_allowed_callers(allowed_callers_file)

    return WebhookConfig(
        host=host,
        token=token,
        door_name=os.getenv("UNIFI_DOOR_NAME", "Gate"),
        access_port=int(os.getenv("UNIFI_ACCESS_PORT", "12445")),
        timeout=float(os.getenv("UNIFI_TIMEOUT_SECONDS", "5")),
        verify_tls=not insecure,
        actor_id=os.getenv("UNIFI_ACTOR_ID", "phone-gate-bridge"),
        actor_name=os.getenv("UNIFI_ACTOR_NAME", "Phone Gate Bridge"),
        bind_host=os.getenv("WEBHOOK_BIND_HOST", "127.0.0.1"),
        bind_port=int(os.getenv("WEBHOOK_BIND_PORT", "8080")),
        allowed_callers_file=allowed_callers_file,
        twilio_auth_token=twilio_auth_token,
        public_base_url=public_base_url.rstrip("/"),
        twilio_tts_voice=os.getenv("TWILIO_TTS_VOICE", "Polly.Joanna-Neural"),
        dashboard_allowed_cidrs=tuple(
            item.strip() for item in dashboard_allowed_cidrs.split(",") if item.strip()
        ),
        dashboard_recent_events_limit=int(os.getenv("DASHBOARD_RECENT_EVENTS_LIMIT", "100")),
        dashboard_db_path=os.getenv("DASHBOARD_DB_PATH", "var/activity.sqlite3"),
    )


class GateStatusProbe:
    """Reads the door's live status, with caching so polling stays cheap.

    The dashboard polls every few seconds; the controller should not see that
    rate. Successful reads are cached briefly and failures are cached for
    longer so a controller that is down is not hammered.
    """

    def __init__(
        self,
        config: WebhookConfig,
        *,
        ttl: float = 2.5,
        error_ttl: float = 20.0,
    ) -> None:
        self._config = config
        self._ttl = ttl
        self._error_ttl = error_ttl
        self._lock = threading.Lock()
        self._door: dict | None = None
        self._error = ""
        self._checked_at = 0.0

    def _client(self) -> AccessClient:
        return AccessClient(
            host=self._config.host,
            token=self._config.token,
            port=self._config.access_port,
            timeout=self._config.timeout,
            verify_tls=self._config.verify_tls,
        )

    def _read_door(self) -> tuple[dict | None, str]:
        try:
            # The doors listing carries live status, so one request covers both
            # finding the gate and reading its state.
            return self._client().find_door(self._config.door_name), ""
        except (AccessApiError, ValueError) as exc:
            return None, str(exc)

    def current(
        self,
        *,
        last_unlock: ActivityEvent | None,
        last_hold: ActivityEvent | None = None,
    ) -> GateStatus:
        with self._lock:
            now = time.time()
            ttl = self._error_ttl if self._door is None and self._error else self._ttl
            if now - self._checked_at > ttl:
                self._door, self._error = self._read_door()
                self._checked_at = now
            door, error = self._door, self._error

        return build_gate_status(
            door=door,
            door_error=error,
            last_unlock=last_unlock,
            hold_started_at=last_hold.ts if last_hold is not None else None,
        )


class TwilioWebhookHandler(BaseHTTPRequestHandler):
    config: WebhookConfig
    activity: ActivityStore
    dashboard_networks: tuple[IpNetwork, ...]
    gate_probe: GateStatusProbe

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/twilio/voice", "/twilio/voice/confirm"}:
            self._send_response(404, twiml_say("Not found.", self.config.twilio_tts_voice))
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        request_url = f"{self.config.public_base_url}{path}"
        signature = self.headers.get("X-Twilio-Signature")
        if not is_valid_twilio_signature(
            signature=signature,
            url=request_url,
            form=form,
            auth_token=self.config.twilio_auth_token,
        ):
            self.activity.record("signature_invalid", detail=path)
            self._send_plain(403, b"forbidden")
            return

        from_number = form.get("From", [""])[0]
        call_sid = form.get("CallSid", [""])[0]
        self.activity.record("twilio_request", detail=path, caller=from_number, call_sid=call_sid)
        try:
            allowed_callers = load_allowed_callers(self.config.allowed_callers_file)
        except (OSError, ValueError):
            self.activity.record(
                "allowed_callers_error",
                detail=self.config.allowed_callers_file,
                caller=from_number,
                call_sid=call_sid,
            )
            self._send_response(
                200,
                twiml_say(
                    "Unable to verify access right now. Please try again.",
                    self.config.twilio_tts_voice,
                ),
            )
            return
        allowed_caller = find_allowed_caller(from_number, allowed_callers)

        if allowed_caller is None:
            self.activity.record("caller_blocked", caller=from_number, call_sid=call_sid)
            self._send_response(
                200,
                twiml_say(
                    "This incoming number is not authorized for this gate.",
                    self.config.twilio_tts_voice,
                ),
            )
            return

        if path == "/twilio/voice":
            self.activity.record("caller_prompted", caller=from_number, call_sid=call_sid)
            self._send_response(
                200,
                twiml_gather(
                    "Press 1 now to open the gate.",
                    "/twilio/voice/confirm",
                    self.config.twilio_tts_voice,
                ),
            )
            return

        digit = form.get("Digits", [""])[0].strip()
        if digit != "1":
            self.activity.record(
                "invalid_digit",
                detail=digit or "empty",
                caller=from_number,
                call_sid=call_sid,
            )
            self._send_response(
                200,
                twiml_say("Invalid selection. Goodbye.", self.config.twilio_tts_voice),
            )
            return

        client = AccessClient(
            host=self.config.host,
            token=self.config.token,
            port=self.config.access_port,
            timeout=self.config.timeout,
            verify_tls=self.config.verify_tls,
        )

        try:
            door_id = client.find_door_id(self.config.door_name)
            client.unlock_door(
                door_id=door_id,
                actor_id=self.config.actor_id,
                actor_name=self.config.actor_name,
                extra={
                    "source": "twilio-voice",
                    "from": from_number,
                    "call_sid": call_sid,
                    "digit": digit,
                    "caller_name": allowed_caller.name,
                },
            )
            self._send_response(
                200,
                twiml_say("The gate is now open.", self.config.twilio_tts_voice),
            )
            self.activity.record(
                "unlock_success",
                detail=self.config.door_name,
                caller=from_number,
                call_sid=call_sid,
            )
        except (AccessApiError, ValueError):
            self.activity.record(
                "unlock_failed",
                detail=self.config.door_name,
                caller=from_number,
                call_sid=call_sid,
            )
            self._send_response(
                200,
                twiml_say(
                    "Unable to open the gate right now. Please try again.",
                    self.config.twilio_tts_voice,
                ),
            )

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if path in {"/dashboard", "/dashboard/api/state"}:
            remote_ip = self.client_address[0] if self.client_address else ""
            if not is_ip_allowed(remote_ip, self.dashboard_networks):
                self.activity.record("dashboard_denied", detail=remote_ip)
                self._send_plain(403, b"forbidden")
                return

            if path == "/dashboard":
                # Only page loads are audited. The state endpoint is polled
                # every few seconds and would bury the log.
                self.activity.record("dashboard_view", detail=remote_ip)

            state = self._dashboard_state()
            if path == "/dashboard":
                self._send_html(200, render_dashboard_html(state))
            else:
                self._send_json(200, render_dashboard_json(state))
            return
        self.send_response(404)
        self.end_headers()

    def _dashboard_state(self):
        try:
            caller_names = {
                caller.number: caller.name
                for caller in load_allowed_callers(self.config.allowed_callers_file)
                if caller.name
            }
        except (OSError, ValueError):
            caller_names = {}

        gate = self.gate_probe.current(
            last_unlock=self.activity.latest(("unlock_success",)),
            last_hold=self.activity.latest(("hold_open",)),
        )
        return build_dashboard_state(
            store=self.activity,
            door_name=self.config.door_name,
            gate=gate,
            caller_names=caller_names,
            recent_limit=self.config.dashboard_recent_events_limit,
        )

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_response(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_plain(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def run_server(config: WebhookConfig) -> None:
    dashboard_networks = parse_cidr_list(",".join(config.dashboard_allowed_cidrs))
    handler = type(
        "ConfiguredTwilioWebhookHandler",
        (TwilioWebhookHandler,),
        {
            "config": config,
            "activity": ActivityStore(config.dashboard_db_path),
            "dashboard_networks": dashboard_networks,
            "gate_probe": GateStatusProbe(config),
        },
    )
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server(load_config_from_env())
