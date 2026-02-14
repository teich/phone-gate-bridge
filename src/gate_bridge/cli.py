from __future__ import annotations

import argparse
import json
import os
import sys

from gate_bridge.client import AccessApiError, AccessClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gate-open",
        description="UniFi Access developer API helper.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_shared_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", required=True, help="UDM Pro hostname or IP")
        p.add_argument("--port", type=int, default=12445, help="Access API port")
        p.add_argument(
            "--token",
            help="API token (prefer --token-env with a protected environment variable)",
        )
        p.add_argument(
            "--token-env",
            default="UNIFI_ACCESS_API_TOKEN",
            help="Environment variable that stores API token",
        )
        p.add_argument(
            "--timeout",
            type=float,
            default=5.0,
            help="HTTP timeout in seconds",
        )
        p.add_argument(
            "--insecure",
            action="store_true",
            help="Disable TLS certificate verification",
        )

    list_parser = subparsers.add_parser(
        "list-doors", help="List doors available to this API token"
    )
    add_shared_args(list_parser)

    unlock_parser = subparsers.add_parser("unlock", help="Unlock a door")
    add_shared_args(unlock_parser)
    unlock_parser.add_argument("--door-id", help="UniFi Access door UUID")
    unlock_parser.add_argument(
        "--door-name",
        default="Gate",
        help="Door name (or full_name substring) to resolve when --door-id is not set",
    )
    unlock_parser.add_argument("--actor-id", help="Actor ID for Access logs/webhooks")
    unlock_parser.add_argument("--actor-name", help="Actor name for Access logs/webhooks")
    unlock_parser.add_argument(
        "--extra-json",
        help="JSON object passed through as extra payload",
    )

    return parser


def _load_token(args: argparse.Namespace) -> str:
    if args.token:
        return args.token
    token = os.getenv(args.token_env)
    if token:
        return token
    raise ValueError(
        "API token not found. Pass --token or set the environment variable "
        f"{args.token_env}."
    )


def _load_extra(extra_json: str | None) -> dict | None:
    if extra_json is None:
        return None
    try:
        parsed = json.loads(extra_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid --extra-json: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("--extra-json must decode to a JSON object")
    return parsed


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        token = _load_token(args)
        client = AccessClient(
            host=args.host,
            token=token,
            port=args.port,
            timeout=args.timeout,
            verify_tls=not args.insecure,
        )

        if args.command == "list-doors":
            response = client.list_doors()
        elif args.command == "unlock":
            door_id = args.door_id or client.find_door_id(args.door_name)
            response = client.unlock_door(
                door_id=door_id,
                actor_id=args.actor_id,
                actor_name=args.actor_name,
                extra=_load_extra(args.extra_json),
            )
        else:
            raise ValueError(f"Unknown command: {args.command}")
    except (ValueError, AccessApiError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if response:
        print(json.dumps(response, indent=2, sort_keys=True))
    else:
        print("Request accepted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
