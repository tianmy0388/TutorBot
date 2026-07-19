"""Stable, non-sensitive reporting for caught capability failures."""

from __future__ import annotations

from loguru import logger

from tutor.core.stream_bus import StreamBus


def log_degraded(*, code: str, source: str, stage: str = "") -> None:
    """Log a caught/degraded failure without exception context or payloads."""
    logger.warning(
        "Capability degraded code={code} source={source} stage={stage}",
        code=code,
        source=source,
        stage=stage,
    )


async def report_degraded(
    stream: StreamBus,
    *,
    code: str,
    summary: str,
    source: str,
    stage: str = "",
) -> None:
    """Log and stream only a stable code plus a generic public summary."""
    log_degraded(code=code, source=source, stage=stage)
    await stream.observation(
        summary,
        source=source,
        stage=stage,
        metadata={"code": code},
    )


__all__ = ["log_degraded", "report_degraded"]
