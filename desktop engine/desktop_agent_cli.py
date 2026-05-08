#! python3
"""CLI wrapper for the local desktop agent."""

from __future__ import annotations

import argparse
from pathlib import Path

from desktop_agent import run_cli


def main() -> int:
    workspace_default = Path(__file__).resolve().parent.parent.parent
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="+", help="Natural-language desktop request")
    parser.add_argument("--cwd", default=str(workspace_default), help="Working directory")
    parser.add_argument("--broker-url", default=None, help="Optional desktop broker URL")
    parser.add_argument("--broker-token", default=None, help="Optional desktop broker token")
    parser.add_argument("--session-key", default="cli", help="Persistent task session key")
    parser.add_argument("--mode", choices=["fast", "safe"], default=None, help="Execution mode")
    args = parser.parse_args()
    return run_cli(
        Path(args.cwd),
        " ".join(args.request),
        broker_url=args.broker_url,
        broker_token=args.broker_token,
        session_key=args.session_key,
        execution_mode=args.mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
