"""Regression: matplotlib figures that the user code leaves open
after ``plt.show()`` must end up as artifacts on the resource.

Pre-fix, the sandbox used ``MPLBACKEND=Agg`` so ``plt.show()`` was
a no-op that printed::

    UserWarning: FigureCanvasAgg is non-interactive, and thus
    cannot be shown

…leaving the figure only in memory. The artifact picker walked the
scratch dir and found nothing. The user's "反向传播训练XOR" snippet
hit exactly this: loss curve was drawn, ``plt.show()`` did nothing,
right pane was empty.

The fix drains all open figures to ``scratch/figure_N.png`` before
the artifact picker runs.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.core.context import UnifiedContext
from tutor.services.llm.base import LLMResponse


def _mock_llm(*responses: str):
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048
    queue = list(responses)

    async def call(req):
        c = queue.pop(0) if queue else "{}"
        return LLMResponse(content=c, model="mock", finish_reason="stop")

    llm.call = call
    return llm


@pytest.mark.asyncio
async def test_code_sandbox_drains_matplotlib_figures_to_artifacts():
    # XOR training snippet — the exact one the user reported broken.
    snippet = (
        "import numpy as np\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "x = np.arange(10)\n"
        "y = x ** 2\n"
        "plt.plot(x, y)\n"
        "plt.title('loss curve')\n"
        "plt.show()\n"  # no-op under Agg; figure stays in memory
    )
    llm = _mock_llm(json.dumps({
        "title": "XOR 训练",
        "language": "python",
        "code": snippet,
        "explanation": "示例",
        "difficulty": 2,
    }, ensure_ascii=False))
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext(user_message="XOR")
    resource = await agent.process(ctx, topic="XOR")
    artifacts = resource.format_specific.get("artifacts") or []
    # At least one PNG artifact must have been produced by the drain.
    pngs = [a for a in artifacts if a.get("kind") == "png"]
    assert pngs, f"expected drained PNG artifacts, got: {artifacts!r}"
    # The file must actually exist on disk.
    from pathlib import Path
    for art in pngs:
        p = Path(art["path"])
        assert p.exists(), f"artifact path missing: {p}"
        assert p.stat().st_size > 0, f"artifact is empty: {p}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))