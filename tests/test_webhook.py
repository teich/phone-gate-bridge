import unittest

from gate_bridge.webhook import (
    is_allowed_caller,
    normalize_phone,
    twiml_gather,
    twiml_say,
)


class WebhookHelpersTests(unittest.TestCase):
    def test_normalize_phone(self):
        self.assertEqual(normalize_phone("+1 (707) 555-1111"), "+17075551111")
        self.assertEqual(normalize_phone("707-555-1111"), "7075551111")

    def test_allowed_caller_true(self):
        allowed = ("+17075551111", "+14155550000")
        self.assertTrue(is_allowed_caller("+1 (707) 555-1111", allowed))

    def test_allowed_caller_false(self):
        allowed = ("+17075551111",)
        self.assertFalse(is_allowed_caller("+17075552222", allowed))

    def test_twiml_output(self):
        output = twiml_say("The gate is now open.").decode("utf-8")
        self.assertIn("<Say>The gate is now open.</Say>", output)
        self.assertIn("<Hangup/>", output)

    def test_twiml_gather_output(self):
        output = twiml_gather("Press 1 now to open the gate.", "/twilio/voice/confirm")
        rendered = output.decode("utf-8")
        self.assertIn("<Gather", rendered)
        self.assertIn('numDigits="1"', rendered)
        self.assertIn('action="/twilio/voice/confirm"', rendered)
        self.assertIn("<Say>Press 1 now to open the gate.</Say>", rendered)


if __name__ == "__main__":
    unittest.main()
