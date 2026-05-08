#! python3
"""Windowless service bootstrap for Telegram bridge scripts."""

from __future__ import annotations

import argparse
import runpy
import sys
import traceback
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script")
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    args = parser.parse_args()

    script = Path(args.script).resolve()
    stdout_path = Path(args.stdout).resolve()
    stderr_path = Path(args.stderr).resolve()
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)

    with stdout_path.open("a", encoding="utf-8", buffering=1) as stdout, stderr_path.open("a", encoding="utf-8", buffering=1) as stderr:
        sys.stdout = stdout
        sys.stderr = stderr
        try:
            sys.argv = [str(script)]
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
            if code:
                print(f"Service exited with code {code}", file=stderr)
            return int(code or 0)
        except Exception:
            traceback.print_exc(file=stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
