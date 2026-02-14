from __future__ import annotations

import os
import xml.sax.saxutils as saxutils
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from gate_bridge.client import AccessApiError, AccessClient


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
    allowed_callers: tuple[str, ...] = ()


def normalize_phone(value: str) -> str:
    value = value.strip()
    if value.startswith("+"):
        return "+" + "".join(ch for ch in value[1:] if ch.isdigit())
    return "".join(ch for ch in value if ch.isdigit())


def is_allowed_caller(caller: str, allowed_callers: tuple[str, ...]) -> bool:
    normalized_caller = normalize_phone(caller)
    if not normalized_caller:
        return False
    normalized_allowed = {normalize_phone(item) for item in allowed_callers}
    return normalized_caller in normalized_allowed


def twiml_say(message: str) -> bytes:
    safe = saxutils.escape(message)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Say>{safe}</Say>"
        "<Hangup/>"
        "</Response>"
    ).encode("utf-8")


def twiml_gather(prompt: str, action: str) -> bytes:
    safe_prompt = saxutils.escape(prompt)
    safe_action = saxutils.escape(action)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        f"<Gather input=\"dtmf\" numDigits=\"1\" action=\"{safe_action}\" method=\"POST\" timeout=\"5\">"
        f"<Say>{safe_prompt}</Say>"
        "</Gather>"
        "<Say>No input received. Goodbye.</Say>"
        "<Hangup/>"
        "</Response>"
    ).encode("utf-8")


def load_config_from_env() -> WebhookConfig:
    host = os.getenv("UNIFI_HOST")
    token = os.getenv("UNIFI_ACCESS_API_TOKEN")
    if not host:
        raise ValueError("UNIFI_HOST is required")
    if not token:
        raise ValueError("UNIFI_ACCESS_API_TOKEN is required")

    allowed_raw = os.getenv("ALLOWED_CALLERS", "")
    allowed = tuple(
        normalize_phone(item)
        for item in (piece.strip() for piece in allowed_raw.split(","))
        if item
    )
    if not allowed:
        raise ValueError("ALLOWED_CALLERS is required (comma-separated list)")

    insecure = os.getenv("UNIFI_INSECURE_TLS", "false").lower() in {"1", "true", "yes"}

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
        allowed_callers=allowed,
    )


class TwilioWebhookHandler(BaseHTTPRequestHandler):
    config: WebhookConfig

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path not in {"/twilio/voice", "/twilio/voice/confirm"}:
            self._send_response(404, twiml_say("Not found."))
            return

        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        form = parse_qs(body, keep_blank_values=True)
        from_number = form.get("From", [""])[0]
        call_sid = form.get("CallSid", [""])[0]

        if not is_allowed_caller(from_number, self.config.allowed_callers):
            self._send_response(
                200,
                twiml_say("This incoming number is not authorized for this gate."),
            )
            return

        if path == "/twilio/voice":
            self._send_response(
                200,
                twiml_gather(
                    "Press 1 now to open the gate.",
                    "/twilio/voice/confirm",
                ),
            )
            return

        digit = form.get("Digits", [""])[0].strip()
        if digit != "1":
            self._send_response(200, twiml_say("Invalid selection. Goodbye."))
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
                },
            )
            self._send_response(200, twiml_say("The gate is now open."))
        except (AccessApiError, ValueError):
            self._send_response(
                200,
                twiml_say("Unable to open the gate right now. Please try again."),
            )

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _send_response(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run_server(config: WebhookConfig) -> None:
    handler = type(
        "ConfiguredTwilioWebhookHandler",
        (TwilioWebhookHandler,),
        {"config": config},
    )
    server = ThreadingHTTPServer((config.bind_host, config.bind_port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server(load_config_from_env())
