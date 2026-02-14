from __future__ import annotations

import sys

from gate_bridge.webhook import load_config_from_env, run_server


def main() -> int:
    try:
        config = load_config_from_env()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    run_server(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
