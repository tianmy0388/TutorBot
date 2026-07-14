"""Uvicorn launcher — `python -m tutor api`."""

from __future__ import annotations

import sys

import uvicorn

from tutor.services.config.settings import get_settings


def run() -> None:
    """Run the FastAPI app via uvicorn.

    Note on ``reload``:
      Uvicorn's reloader on Windows (multiprocessing spawn) has a known
      bug where the parent reloader process holds the LISTEN socket
      but the worker fails to inherit it; the port then appears stuck
      (``OSError: [Errno 10048] Only one usage of each socket address``)
      even after the parent dies, blocking every subsequent bind.
      Disable reload on win32 to keep dev startup reliable.
    """
    settings = get_settings()
    reload_enabled = (settings.env == "development") and (sys.platform != "win32")
    uvicorn.run(
        "tutor.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=reload_enabled,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
