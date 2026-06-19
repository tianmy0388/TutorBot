"""Uvicorn launcher — `python -m tutor api`."""

from __future__ import annotations

import uvicorn

from tutor.services.config.settings import get_settings


def run() -> None:
    """Run the FastAPI app via uvicorn."""
    settings = get_settings()
    uvicorn.run(
        "tutor.api.main:app",
        host=settings.host,
        port=settings.port,
        reload=(settings.env == "development"),
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
