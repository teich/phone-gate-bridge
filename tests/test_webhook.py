import http.client
import json
import subprocess
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlencode

from gate_bridge.activity import ActivityStore
from gate_bridge.dashboard import GateStatus
from gate_bridge.webhook import (
    AllowedCaller,
    TwilioWebhookHandler,
    WebhookConfig,
    build_twilio_signature,
    find_allowed_caller,
    is_ip_allowed,
    is_valid_twilio_signature,
    load_allowed_callers,
    load_config_from_env,
    normalize_phone,
    parse_twilio_phone_number,
    parse_cidr_list,
    resolve_dashboard_db_path,
    twiml_gather,
    twiml_say,
)


class WebhookHelpersTests(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+1 (707) 555-1111"), "+17075551111")
        self.assertEqual(normalize_phone("707-555-1111"), "7075551111")

    def test_twilio_phone_number_is_validated_as_e164(self):
        self.assertEqual(parse_twilio_phone_number(""), "")
        self.assertEqual(
            parse_twilio_phone_number("+17075551111"),
            "+17075551111",
        )
        with self.assertRaisesRegex(ValueError, "E.164"):
            parse_twilio_phone_number("707-555-1111")

    def test_config_loads_the_optional_twilio_phone_number(self):
        with TemporaryDirectory() as tmp:
            callers_path = Path(tmp) / "allowed-callers.toml"
            callers_path.write_text(
                "\n".join(
                    [
                        "[[callers]]",
                        'number = "+17075551111"',
                        "enabled = true",
                    ]
                ),
                encoding="utf-8",
            )
            env = {
                "UNIFI_HOST": "192.168.2.1",
                "UNIFI_ACCESS_API_TOKEN": "access-token",
                "TWILIO_AUTH_TOKEN": "twilio-token",
                "TWILIO_PHONE_NUMBER": "+17075551111",
                "PUBLIC_BASE_URL": "https://gate.example.test",
                "ALLOWED_CALLERS_FILE": str(callers_path),
            }
            with patch.dict("os.environ", env, clear=True):
                config = load_config_from_env()
            self.assertEqual(config.twilio_phone_number, "+17075551111")

            del env["TWILIO_PHONE_NUMBER"]
            with patch.dict("os.environ", env, clear=True):
                config_without_number = load_config_from_env()
            self.assertEqual(config_without_number.twilio_phone_number, "")

    def test_deploy_validation_checks_optional_twilio_phone_number(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            callers_path = root / "allowed-callers.toml"
            callers_path.write_text(
                '[[callers]]\nnumber = "+17075551111"\nenabled = true\n',
                encoding="utf-8",
            )
            env_path = root / "phone-gate-bridge.env"
            base_env = "\n".join(
                [
                    "UNIFI_HOST=192.168.2.1",
                    "UNIFI_ACCESS_API_TOKEN=access-token",
                    "PUBLIC_BASE_URL=https://gate.example.test",
                    "TWILIO_AUTH_TOKEN=twilio-token",
                    f'ALLOWED_CALLERS_FILE="{callers_path}"',
                ]
            )
            script = Path(__file__).resolve().parents[1] / "deploy" / "validate-env.sh"

            for value, expected_status in (
                ("", 0),
                ("+17075551111", 0),
                ("707-555-1111", 1),
            ):
                phone_line = f"\nTWILIO_PHONE_NUMBER={value}" if value else ""
                env_path.write_text(base_env + phone_line + "\n", encoding="utf-8")
                result = subprocess.run(
                    ["bash", str(script), str(env_path)],
                    capture_output=True,
                    check=False,
                    text=True,
                )
                self.assertEqual(
                    result.returncode,
                    expected_status,
                    msg=result.stdout + result.stderr,
                )

    def test_allowed_caller_true(self):
        allowed = (
            AllowedCaller(number="+17075551111", name="Oren", enabled=True),
            AllowedCaller(number="+14155550000", name="Connie", enabled=True),
        )
        matched = find_allowed_caller("+1 (707) 555-1111", allowed)
        self.assertIsNotNone(matched)
        self.assertEqual(matched.name, "Oren")

    def test_allowed_caller_false(self):
        allowed = (AllowedCaller(number="+17075551111", enabled=True),)
        self.assertIsNone(find_allowed_caller("+17075552222", allowed))

    def test_load_allowed_callers_toml(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "allowed-callers.toml"
            path.write_text(
                "\n".join(
                    [
                        "[[callers]]",
                        'number = "+17075551111"',
                        'name = "Oren"',
                        'notes = "Owner"',
                        "enabled = true",
                        'actions = ["open", "hold_open"]',
                        "",
                        "[[callers]]",
                        'number = "+17075552222"',
                        'name = "Connie"',
                        "enabled = false",
                    ]
                ),
                encoding="utf-8",
            )

            callers = load_allowed_callers(str(path))
            self.assertEqual(len(callers), 2)
            self.assertEqual(callers[0].name, "Oren")
            self.assertTrue(callers[0].enabled)
            self.assertEqual(callers[0].actions, ("open", "hold_open"))
            self.assertFalse(callers[1].enabled)
            self.assertEqual(callers[1].actions, ("open",))

    def test_twiml_output(self):
        output = twiml_say("The gate is now open.").decode("utf-8")
        self.assertIn('<Say voice="Polly.Joanna-Neural">The gate is now open.</Say>', output)
        self.assertIn("<Hangup/>", output)

    def test_twiml_gather_output(self):
        output = twiml_gather("Press 1 now to open the gate.", "/twilio/voice/confirm")
        rendered = output.decode("utf-8")
        self.assertIn("<Gather", rendered)
        self.assertIn('numDigits="1"', rendered)
        self.assertIn('action="/twilio/voice/confirm"', rendered)
        self.assertIn(
            '<Say voice="Polly.Joanna-Neural">Press 1 now to open the gate.</Say>',
            rendered,
        )

    def test_twilio_signature_valid(self):
        url = "https://gate.teich.network/twilio/voice"
        form = {
            "CallSid": ["CA123"],
            "From": ["+17075551111"],
        }
        token = "auth-token"
        signature = build_twilio_signature(url=url, form=form, auth_token=token)
        self.assertTrue(
            is_valid_twilio_signature(
                signature=signature,
                url=url,
                form=form,
                auth_token=token,
            )
        )

    def test_twilio_signature_invalid(self):
        url = "https://gate.teich.network/twilio/voice"
        form = {
            "CallSid": ["CA123"],
            "From": ["+17075551111"],
        }
        self.assertFalse(
            is_valid_twilio_signature(
                signature="bad-signature",
                url=url,
                form=form,
                auth_token="auth-token",
            )
        )

    def test_parse_cidr_list_and_ip_allowed(self):
        networks = parse_cidr_list("127.0.0.1/32, 192.168.0.0/16")
        self.assertTrue(is_ip_allowed("127.0.0.1", networks))
        self.assertTrue(is_ip_allowed("192.168.2.25", networks))
        self.assertFalse(is_ip_allowed("8.8.8.8", networks))

    def test_parse_cidr_list_rejects_invalid(self):
        with self.assertRaises(ValueError):
            parse_cidr_list("192.168.0.0/16,not-a-cidr")

    def test_database_default_preserves_an_existing_legacy_store(self):
        with TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "var" / "activity.sqlite3"
            legacy.parent.mkdir()
            legacy.touch()

            self.assertEqual(
                resolve_dashboard_db_path(None, legacy_path=str(legacy)),
                str(legacy),
            )
            self.assertEqual(
                resolve_dashboard_db_path(
                    "/var/lib/phone-gate-bridge/custom.sqlite3",
                    legacy_path=str(legacy),
                ),
                "/var/lib/phone-gate-bridge/custom.sqlite3",
            )

    def test_activity_store_persists_records(self):
        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "activity.sqlite3"
            store_a = ActivityStore(str(db_path))
            store_a.record(
                "unlock_success",
                detail="Gate",
                caller="+17075551111",
                call_sid="CA555",
            )
            store_b = ActivityStore(str(db_path))
            counts, recent = store_b.snapshot(10)
            self.assertEqual(counts.get("unlock_success"), 1)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0].call_sid, "CA555")


class FakeAccessClient:
    unlock_calls = 0

    def __init__(self, **_kwargs):
        pass

    def find_door_id(self, _door_name):
        return "door-1"

    def unlock_door(self, **_kwargs):
        type(self).unlock_calls += 1
        return {"code": "SUCCESS"}


class FakeGateProbe:
    def current(self, **_kwargs):
        return GateStatus(state="secured", position="closed", relay="locked")


class HandlerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        root = Path(self.temp.name)
        callers_path = root / "allowed-callers.toml"
        callers_path.write_text(
            "\n".join(
                [
                    "[[callers]]",
                    'number = "+17075551111"',
                    'name = "Oren"',
                    'actions = ["open"]',
                    "enabled = true",
                ]
            ),
            encoding="utf-8",
        )
        self.store = ActivityStore(str(root / "activity.sqlite3"))
        self.config = WebhookConfig(
            host="192.168.2.1",
            token="token",
            allowed_callers_file=str(callers_path),
            twilio_auth_token="twilio-secret",
            twilio_phone_number="+17075551111",
            public_base_url="https://gate.example.test",
            dashboard_db_path=str(root / "activity.sqlite3"),
            max_webhook_body_bytes=128,
        )
        handler = type(
            "TestTwilioWebhookHandler",
            (TwilioWebhookHandler,),
            {
                "config": self.config,
                "activity": self.store,
                "dashboard_networks": parse_cidr_list("127.0.0.1/32"),
                "gate_probe": FakeGateProbe(),
                "access_client_class": FakeAccessClient,
            },
        )
        FakeAccessClient.unlock_calls = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, method, path, *, body=b"", headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1",
            self.server.server_address[1],
            timeout=2,
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        payload = response.read()
        result = response.status, dict(response.getheaders()), payload
        connection.close()
        return result

    def signed_post(self, path, values):
        form = {key: [value] for key, value in values.items()}
        signature = build_twilio_signature(
            url=f"{self.config.public_base_url}{path}",
            form=form,
            auth_token=self.config.twilio_auth_token,
        )
        body = urlencode(values).encode("utf-8")
        return self.request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Twilio-Signature": signature,
            },
        )

    def test_dashboard_page_state_and_security_headers(self):
        page_status, page_headers, page = self.request("GET", "/dashboard")
        state_status, state_headers, raw_state = self.request(
            "GET",
            "/dashboard/api/state",
        )

        self.assertEqual(page_status, 200)
        self.assertIn(b"/dashboard/assets/", page)
        self.assertEqual(page_headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", page_headers["Content-Security-Policy"])
        self.assertEqual(state_status, 200)
        self.assertEqual(state_headers["Cache-Control"], "no-store")
        state = json.loads(raw_state)
        self.assertEqual(state["schema_version"], 1)
        self.assertEqual(state["phone_number"], "+17075551111")

        counts, _ = self.store.snapshot(20)
        self.assertEqual(counts["dashboard_view"], 1)

    def test_duplicate_confirm_actuates_gate_once(self):
        values = {
            "From": "+17075551111",
            "CallSid": "CA-duplicate",
            "Digits": "1",
        }
        first_status, _, first = self.signed_post("/twilio/voice/confirm", values)
        second_status, _, second = self.signed_post("/twilio/voice/confirm", values)

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertIn(b"The gate is now open", first)
        self.assertIn(b"The gate is now open", second)
        self.assertEqual(FakeAccessClient.unlock_calls, 1)
        counts, _ = self.store.snapshot(50)
        self.assertEqual(counts["unlock_success"], 1)

    def test_oversized_body_is_rejected_before_reading_or_signature_work(self):
        status, _, body = self.request(
            "POST",
            "/twilio/voice",
            body=b"x",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": "129",
            },
        )
        self.assertEqual(status, 413)
        self.assertEqual(body, b"request too large")


if __name__ == "__main__":
    unittest.main()
