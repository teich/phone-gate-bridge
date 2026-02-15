import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gate_bridge.webhook import (
    AllowedCaller,
    build_twilio_signature,
    find_allowed_caller,
    is_valid_twilio_signature,
    load_allowed_callers,
    normalize_phone,
    twiml_gather,
    twiml_say,
)


class WebhookHelpersTests(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+1 (707) 555-1111"), "+17075551111")
        self.assertEqual(normalize_phone("707-555-1111"), "7075551111")

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
            self.assertFalse(callers[1].enabled)

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


if __name__ == "__main__":
    unittest.main()
