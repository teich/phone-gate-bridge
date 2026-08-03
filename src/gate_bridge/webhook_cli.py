from __future__ import annotations

import sys

from gate_bridge.webhook import load_config_from_env, run_server


def main() -> int:
    try:
        config = load_config_from_env()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    try:
        run_server(config)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
