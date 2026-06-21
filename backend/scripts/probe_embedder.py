"""One-shot probe: does the configured embedder actually return
vectors for a tiny input? Run with the tutor conda env.

    python scripts/probe_embedder.py
"""

from __future__ import annotations

import asyncio
import sys

from tutor.services.config.settings import get_settings
from tutor.services.embeddings.base import EmbedRequest
from tutor.services.embeddings.embedder_factory import get_runtime_embedder


async def go() -> int:
    s = get_settings()
    print(f"[probe] model={s.embed_model!r} provider={s.embed_provider!r}")
    try:
        embedder = get_runtime_embedder(s)
    except Exception as e:
        print(f"[probe] build failed: {type(e).__name__}: {e}")
        return 2

    try:
        resp = await embedder.embed(EmbedRequest(input=["hello world"]))
    except Exception as e:
        print(f"[probe] embed failed: {type(e).__name__}: {e}")
        body = getattr(e, "body", None) or getattr(e, "response", None)
        if body is not None:
            print(f"[probe] upstream body: {body}")
        return 3

    if not resp.vectors:
        print("[probe] no vectors returned")
        return 4
    dim = len(resp.vectors[0])
    print(f"[probe] OK: {len(resp.vectors)} vector(s), dim={dim}, model={resp.model!r}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(go()))
