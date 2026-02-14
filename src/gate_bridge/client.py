from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class AccessApiError(RuntimeError):
    """Raised when the UniFi Access API call fails."""


@dataclass(frozen=True)
class AccessClient:
    host: str
    token: str
    port: int = 12445
    timeout: float = 5.0
    verify_tls: bool = True

    def list_doors(self) -> dict[str, Any]:
        url = f"https://{self.host}:{self.port}/api/v1/developer/doors"
        req = request.Request(
            url=url,
            method="GET",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        return self._send(req)

    def find_door_id(self, door_name: str = "Gate") -> str:
        if not door_name:
            raise ValueError("door_name is required")

        response = self.list_doors()
        doors = response.get("data")
        if not isinstance(doors, list):
            raise AccessApiError("Access API response missing doors data list")

        needle = door_name.strip().lower()
        exact_name_matches: list[dict[str, Any]] = []
        exact_full_matches: list[dict[str, Any]] = []
        contains_matches: list[dict[str, Any]] = []

        for door in doors:
            if not isinstance(door, dict):
                continue
            name = str(door.get("name", "")).strip()
            full_name = str(door.get("full_name", "")).strip()
            name_lower = name.lower()
            full_lower = full_name.lower()
            if name_lower == needle:
                exact_name_matches.append(door)
            elif full_lower == needle:
                exact_full_matches.append(door)
            elif needle in name_lower or needle in full_lower:
                contains_matches.append(door)

        matches = exact_name_matches or exact_full_matches or contains_matches
        if not matches:
            raise AccessApiError(f"No door matched name '{door_name}'")
        if len(matches) > 1:
            names = ", ".join(str(item.get("full_name") or item.get("name")) for item in matches)
            raise AccessApiError(
                f"Door name '{door_name}' is ambiguous. Matches: {names}"
            )

        door_id = matches[0].get("id")
        if not isinstance(door_id, str) or not door_id.strip():
            raise AccessApiError("Matched door is missing a valid id")
        return door_id

    def unlock_door(
        self,
        door_id: str,
        actor_id: str | None = None,
        actor_name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not door_id:
            raise ValueError("door_id is required")

        has_actor_id = actor_id is not None
        has_actor_name = actor_name is not None
        if has_actor_id != has_actor_name:
            raise ValueError("actor_id and actor_name must be provided together")

        payload: dict[str, Any] = {}
        if has_actor_id and has_actor_name:
            payload["actor_id"] = actor_id
            payload["actor_name"] = actor_name
        if extra is not None:
            payload["extra"] = extra

        url = (
            f"https://{self.host}:{self.port}"
            f"/api/v1/developer/doors/{door_id}/unlock"
        )

        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url=url,
            method="PUT",
            data=body,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )

        context = ssl.create_default_context()
        if not self.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        return self._send(req)

    def _send(self, req: request.Request) -> dict[str, Any]:
        context = ssl.create_default_context()
        if not self.verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        try:
            with request.urlopen(req, timeout=self.timeout, context=context) as resp:
                raw = resp.read().decode("utf-8").strip()
                if not raw:
                    return {}
                return json.loads(raw)
        except error.HTTPError as exc:
            body_text = ""
            if exc.fp is not None:
                body_text = exc.fp.read().decode("utf-8", errors="replace")
            raise AccessApiError(
                f"Access API HTTP {exc.code}: {body_text or exc.reason}"
            ) from exc
        except error.URLError as exc:
            raise AccessApiError(f"Access API network error: {exc.reason}") from exc
        except TimeoutError as exc:
            raise AccessApiError("Access API timeout") from exc
        except json.JSONDecodeError as exc:
            raise AccessApiError("Access API returned invalid JSON") from exc
