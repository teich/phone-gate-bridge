import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import patch

from gate_bridge.client import AccessApiError, AccessClient


class FakeResponse:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class AccessClientTests(unittest.TestCase):
    def test_find_door_id_exact_name(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch("gate_bridge.client.request.urlopen") as mocked:
            mocked.return_value = FakeResponse(
                json.dumps(
                    {
                        "code": "SUCCESS",
                        "data": [
                            {"id": "1", "name": "Side Door", "full_name": "Site - Side Door"},
                            {"id": "2", "name": "Gate", "full_name": "Site - Gate"},
                        ],
                    }
                )
            )

            door_id = client.find_door_id("gate")
            self.assertEqual(door_id, "2")

    def test_find_door_id_ambiguous(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch("gate_bridge.client.request.urlopen") as mocked:
            mocked.return_value = FakeResponse(
                json.dumps(
                    {
                        "code": "SUCCESS",
                        "data": [
                            {"id": "1", "name": "Gate East", "full_name": "Site - Gate East"},
                            {"id": "2", "name": "Gate West", "full_name": "Site - Gate West"},
                        ],
                    }
                )
            )

            with self.assertRaises(AccessApiError) as ctx:
                client.find_door_id("gate")
            self.assertIn("ambiguous", str(ctx.exception))

    def test_find_door_id_not_found(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch("gate_bridge.client.request.urlopen") as mocked:
            mocked.return_value = FakeResponse(
                json.dumps(
                    {
                        "code": "SUCCESS",
                        "data": [
                            {"id": "1", "name": "Side Door", "full_name": "Site - Side Door"},
                        ],
                    }
                )
            )

            with self.assertRaises(AccessApiError) as ctx:
                client.find_door_id("gate")
            self.assertIn("No door matched", str(ctx.exception))

    def test_list_doors_request_contains_expected_fields(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch("gate_bridge.client.request.urlopen") as mocked:
            mocked.return_value = FakeResponse(
                '{"code":"SUCCESS","data":[{"id":"door-1","name":"Gate"}]}'
            )

            response = client.list_doors()

            self.assertEqual(response["code"], "SUCCESS")
            req = mocked.call_args.args[0]
            self.assertEqual(req.get_method(), "GET")
            self.assertIn("/api/v1/developer/doors", req.full_url)
            self.assertEqual(req.get_header("Authorization"), "Bearer token-abc")
            self.assertEqual(req.get_header("Accept"), "application/json")

    def test_unlock_request_contains_expected_fields(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch("gate_bridge.client.request.urlopen") as mocked:
            mocked.return_value = FakeResponse('{"code":"SUCCESS","data":"success"}')

            response = client.unlock_door(
                door_id="door-123",
                actor_id="actor-1",
                actor_name="Gate Bridge",
                extra={"source": "unit-test"},
            )

            self.assertEqual(response["code"], "SUCCESS")
            req = mocked.call_args.args[0]
            self.assertEqual(req.get_method(), "PUT")
            self.assertIn("/api/v1/developer/doors/door-123/unlock", req.full_url)
            self.assertEqual(req.get_header("Authorization"), "Bearer token-abc")
            self.assertEqual(req.get_header("Content-type"), "application/json")
            sent = json.loads(req.data.decode("utf-8"))
            self.assertEqual(sent["actor_id"], "actor-1")
            self.assertEqual(sent["actor_name"], "Gate Bridge")
            self.assertEqual(sent["extra"]["source"], "unit-test")

    def test_actor_pair_validation(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with self.assertRaises(ValueError):
            client.unlock_door(door_id="door-123", actor_id="only-id")

        with self.assertRaises(ValueError):
            client.unlock_door(door_id="door-123", actor_name="only-name")

    def test_http_error_is_wrapped(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")
        http_error = HTTPError(
            url="https://192.168.1.1",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"code":"FORBIDDEN"}'),
        )

        with patch("gate_bridge.client.request.urlopen", side_effect=http_error):
            with self.assertRaises(AccessApiError) as ctx:
                client.unlock_door(door_id="door-123")

        self.assertIn("HTTP 403", str(ctx.exception))

    def test_network_error_is_wrapped(self):
        client = AccessClient(host="192.168.1.1", token="token-abc")

        with patch(
            "gate_bridge.client.request.urlopen",
            side_effect=URLError("connection refused"),
        ):
            with self.assertRaises(AccessApiError) as ctx:
                client.unlock_door(door_id="door-123")

        self.assertIn("network error", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
