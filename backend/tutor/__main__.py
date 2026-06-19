"""Tutor CLI entry point — `python -m tutor`."""

from __future__ import annotations

import sys


def main() -> int:
    """Dispatch to subcommands: api, cli."""
    if len(sys.argv) < 2:
        from tutor.cli.main import app

        app()
        return 0

    subcommand = sys.argv[1]
    if subcommand == "api":
        from tutor.api.run_server import run

        run()
        return 0
    elif subcommand == "cli":
        from tutor.cli.main import app

        # typer's `app` consumes sys.argv directly; remove the subcommand token.
        sys.argv.pop(1)
        app()
        return 0
    else:
        print(f"Unknown subcommand: {subcommand}", file=sys.stderr)
        print("Usage: python -m tutor [api|cli]", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
