# 对话式学习画像构建 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TutorBot 在普通对话中自动抽取学生特征（专业/目标/历史）构建并动态更新 6 维学习画像，且专业进入生成提示词、面板可见、路径随画像版本重建。

**Architecture:** 启发式门控（纯函数）→ 命中后复用现有 `FeatureExtractorAgent` 在答疑/生成任务的后置步骤异步抽取 → `ProfileBuilder.ingest_signal` 合并 → 发 `profile_updated` 观察事件（前端据此刷新面板）→ 按新画像版本调度 `path_rebuild` 跟随任务。

**Tech Stack:** Python 3.11（FastAPI/pydantic v2/SQLAlchemy async）、Next.js 15 + React 19 + Zustand + Vitest。

## Global Constraints

- 工作地点：主检出 `E:\github\TutorBot`，直接在 `main` 分支上提交（用户明确指定）。
- Python 解释器（一切后端命令）：`E:\Anaconda3\anaconda\envs\tutor\python.exe`
- 前端命令工作目录：`E:\github\TutorBot\frontend`
- **永不 stage/commit `frontend/next-env.d.ts` 与 `package-lock.json`**（前者是 dev server 改写的脏文件，后者为 stat-dirt）。
- 不修改、不提交两个未跟踪文件：`docs/superpowers/plans/2026-07-19-learning-experience-persistence.md`、`docs/superpowers/specs/2026-07-19-learning-experience-persistence-design.md`（另一项并行工作）。
- `backend/tests` 有 5 个已确认预存在的 Windows 环境失败，与本次无关：`agents/resource/test_code_sandbox_cjk_font.py::test_cjk_prelude_runs_before_user_plotting_and_warm_cache_is_reused`、`api/test_health_runtime.py::test_create_app_injected_settings_reach_real_code_sandbox_execution`、`api/test_health_runtime.py::test_agent_preparation_failure_returns_private_typed_resource`、`api/test_health_runtime.py::test_dependency_probe_failure_stays_private_in_resource_markdown`、`api/test_learning_router.py::test_reconcile_all_recovers_course_from_durable_event`。
- `FollowUpTaskSpec.kind` 的取值集合封闭为 `Literal["video_render", "profile_update", "path_rebuild"]`（`backend/tutor/core/capability_result.py:20`）——本计划只复用 `path_rebuild`，不新增 kind。
- JobRunner 会把不在 `{progress, stage_start, stage_end, resource, sources}` 的事件类型归一化为 `type: "progress"` 并保留原 metadata（`backend/tutor/services/jobs/runner.py` `_normalize_capability_event`）——前后端的事件契约因此是：`type=="progress"` 且 `metadata.profile_updated === true`。
- 摄取一律 best-effort：任何 LLM/存储失败仅记 WARNING 日志，绝不抛出影响答疑/生成主流程。
- 前端改动只做**纯新增**（新 case、新卡片），不改既有 case/组件语义——另一会话可能正在编辑这些文件。

---

### Task 1: 画像信号检测器（纯函数）

**Files:**
- Create: `backend/tutor/services/learner_profile/signal_detector.py`
- Test: `backend/tests/services/learner_profile/test_signal_detector.py`

**Interfaces:**
- Consumes: 无（纯函数）。
- Produces: `detect_profile_signal(message: str, *, has_profile: bool) -> bool` —— Task 3 的摄取器用它做门控。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/services/learner_profile/test_signal_detector.py`：

```python
"""Signal detector gate: cheap heuristic before the LLM extractor."""

from __future__ import annotations

import pytest

from tutor.services.learner_profile.signal_detector import detect_profile_signal


@pytest.mark.parametrize(
    "message",
    [
        "我是CS研一，想学LSTM",
        "我现在是本科生，计算机专业",
        "我的专业是软件工程",
        "I'm a graduate student",
        "my major is computer science",
        "我是博士生，研究方向是NLP",
    ],
)
def test_strong_identity_always_triggers(message: str) -> None:
    assert detect_profile_signal(message, has_profile=True) is True
    assert detect_profile_signal(message, has_profile=False) is True


@pytest.mark.parametrize(
    "message",
    [
        "我想学LSTM，之前学过基础NN但对RNN不太熟",
        "我要学反向传播，以前学过梯度下降",
        "I want to learn transformers, I've studied basic NN",
    ],
)
def test_goal_plus_history_triggers(message: str) -> None:
    assert detect_profile_signal(message, has_profile=True) is True


def test_goal_only_triggers_only_without_profile() -> None:
    assert detect_profile_signal("我想学反向传播", has_profile=False) is True
    assert detect_profile_signal("我想学反向传播", has_profile=True) is False


@pytest.mark.parametrize(
    "message",
    [
        "什么是反向传播？",
        "帮我生成反向传播的讲解",
        "RNN 和 LSTM 有什么区别",
        "",
        "   ",
    ],
)
def test_plain_questions_never_trigger(message: str) -> None:
    assert detect_profile_signal(message, has_profile=False) is False
    assert detect_profile_signal(message, has_profile=True) is False
```

- [ ] **Step 2: 运行确认失败**

```powershell
cd E:\github\TutorBot
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/learner_profile/test_signal_detector.py -q
```

预期：FAIL（`ModuleNotFoundError: tutor.services.learner_profile.signal_detector`）。

- [ ] **Step 3: 实现检测器**

创建 `backend/tutor/services/learner_profile/signal_detector.py`：

```python
"""Heuristic gate: does a learner message carry profile-building signal?

Pure, cheap and deterministic — the LLM feature extractor only runs when
this returns True. Three pattern families from the approved design
(docs/superpowers/specs/2026-07-19-conversational-profile-building-design.md):
identity/major (strong), learning goal (weak), learning history (weak).
"""

from __future__ import annotations

import re

_STRONG_IDENTITY = re.compile(
    r"我是|我现在是|我就读|我的专业|专业是|研[一二三]|大[一二三四]"
    r"|本科生|硕士生|博士生|i'?m a|i am a|my major|i study",
    re.IGNORECASE,
)
_WEAK_GOAL = re.compile(
    r"我想学|我要学|我想了解|目标是|打算学|准备(考试|面试|考研|求职|期末)"
    r"|i want to learn|my goal",
    re.IGNORECASE,
)
_WEAK_HISTORY = re.compile(
    r"之前学过|以前学过|没学过|零基础|有[^，。,.]{0,6}基础|不太熟|比较熟"
    r"|熟悉|了解过|自学过|i'?ve studied|new to|familiar with",
    re.IGNORECASE,
)


def detect_profile_signal(message: str, *, has_profile: bool) -> bool:
    """Return True when `message` is worth an LLM profile-extraction call."""
    text = (message or "").strip()
    if not text:
        return False
    if _STRONG_IDENTITY.search(text):
        return True
    goal = bool(_WEAK_GOAL.search(text))
    history = bool(_WEAK_HISTORY.search(text))
    if goal and history:
        return True
    if not has_profile and (goal or history):
        return True
    return False


__all__ = ["detect_profile_signal"]
```

- [ ] **Step 4: 运行确认通过**

同 Step 2 命令；预期：全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/tutor/services/learner_profile/signal_detector.py backend/tests/services/learner_profile/test_signal_detector.py
git commit -m "feat: add heuristic learner-profile signal detector"
```

---

### Task 2: `to_summary()` 携带 major/level

**Files:**
- Modify: `backend/tutor/services/learner_profile/schema.py:287-305`（`LearnerProfile.to_summary`）
- Test: `backend/tests/services/learner_profile/test_schema.py`（追加）

**Interfaces:**
- Consumes: `LearnerProfile.metadata`（`schema.py:271`，`FeatureExtractorAgent` 已把 major/level 注入其中）。
- Produces: `to_summary()` 返回 dict 新增 `"major"`、`"level"` 键（无 metadata 时为空串）—— Task 5 断言资源生成 prompt snapshot 携带专业即依赖此。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/services/learner_profile/test_schema.py` 末尾追加：

```python
def test_summary_carries_major_and_level_from_metadata() -> None:
    from tutor.services.learner_profile.schema import empty_profile

    profile = empty_profile("user-major")
    profile.metadata["major"] = "计算机科学"
    profile.metadata["level"] = "graduate"
    summary = profile.to_summary()
    assert summary["major"] == "计算机科学"
    assert summary["level"] == "graduate"


def test_summary_major_level_default_to_empty_string() -> None:
    from tutor.services.learner_profile.schema import empty_profile

    summary = empty_profile("user-plain").to_summary()
    assert summary["major"] == ""
    assert summary["level"] == ""
```

（先确认 `empty_profile` 在 `schema.py` 中存在——`capabilities/profile.py` 已 import 它；若该测试文件已有 import 风格，跟随之。）

- [ ] **Step 2: 运行确认失败**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/learner_profile/test_schema.py -q
```

预期：FAIL（`KeyError: 'major'`）。

- [ ] **Step 3: 实现**

`backend/tutor/services/learner_profile/schema.py` 的 `to_summary()` 返回 dict 中，在 `"modality_dominant"` 一行之后插入两行：

```python
            "modality_dominant": self.modality.dominant(),
            "major": str(self.metadata.get("major") or ""),
            "level": str(self.metadata.get("level") or ""),
```

- [ ] **Step 4: 运行确认通过**

同 Step 2 命令；预期：全部 PASS（含该文件既有测试）。

- [ ] **Step 5: 提交**

```powershell
git add backend/tutor/services/learner_profile/schema.py backend/tests/services/learner_profile/test_schema.py
git commit -m "feat: carry major and level in learner profile summary"
```

---

### Task 3: 对话摄取器 `dialogue_ingest`

**Files:**
- Create: `backend/tutor/services/learner_profile/dialogue_ingest.py`
- Test: `backend/tests/services/learner_profile/test_dialogue_ingest.py`

**Interfaces:**
- Consumes: `detect_profile_signal`（Task 1）；`FeatureExtractorAgent`（`backend/tutor/agents/profile/feature_extractor.py:104`，`process(context, stream)` → `DialogueSignal`）；`ProfileBuilder.ingest_signal(user_id, signal)` → `(LearnerProfile, ProfileDiff)`（`builder.py:230`）；`ProfileStore.get/get_path`；`FollowUpTaskSpec`（`backend/tutor/core/capability_result.py:17`）。
- Produces: `async def ingest_dialogue_signal(context: UnifiedContext, stream: StreamBus, *, builder=None, extractor=None) -> tuple[bool, tuple[FollowUpTaskSpec, ...]]` —— Task 4/5 的两个能力在 `run()` 末尾调用它并把返回的 specs 并入自己的 `CapabilityResult.follow_up_tasks`。返回 `(是否摄取, follow_up specs)`；任何失败返回 `(False, ())`。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/services/learner_profile/test_dialogue_ingest.py`（单例重置与 TUTOR_DATA_DIR 隔离模式复用 `backend/tests/capabilities/test_profile_capability.py` 的 `fresh_builder` fixture 写法；事件收集用 `StreamBus` + `bus.subscribe()` 队列）：

```python
"""Dialogue-driven profile ingestion (conversational profile building)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import ProfileBuilder
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal
from tutor.services.learner_profile.store import ProfileStore, get_profile_store


def _mock_llm(payload: dict):
    from tutor.services.llm.base import LLMResponse

    llm = MagicMock()
    llm.model = "mock-model"
    llm.default_temperature = 0.3
    llm.default_max_tokens = 2048

    async def call(req):
        return LLMResponse(
            content=json.dumps(payload), model="mock-model", finish_reason="stop"
        )

    llm.call = call
    return llm


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Isolated ProfileStore backed by tmp data dir + reset singletons."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    # Follow the singleton-reset pattern used by
    # backend/tests/capabilities/test_profile_capability.py::fresh_builder
    # (reset whatever module-level singletons that fixture resets).
    store = ProfileStore(tmp_path / "profiles.db")
    monkeypatch.setattr(
        "tutor.services.learner_profile.dialogue_ingest.get_profile_store",
        lambda: store,
        raising=False,
    )
    return store


def _context(message: str) -> UnifiedContext:
    return UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message=message,
        language="zh",
        capability="tutoring",
    )


@pytest.mark.asyncio
async def test_self_intro_ingests_profile_and_schedules_path_rebuild(
    isolated_store,
):
    builder = ProfileBuilder(store=isolated_store)
    extractor = FeatureExtractorAgent(
        llm=_mock_llm(
            {
                "major": "计算机科学",
                "level": "graduate",
                "knowledge": {"neural_networks": 0.6},
                "motivation": {"goal_type": "exam_prep", "goal_description": "期末"},
                "confidence": 0.9,
            }
        )
    )
    bus = StreamBus()
    queue = bus.subscribe()

    ingested, follow_ups = await ingest_dialogue_signal(
        _context("我是CS研一，想学LSTM，之前学过基础NN但对RNN不太熟"),
        bus,
        builder=builder,
        extractor=extractor,
    )

    assert ingested is True
    profile = await isolated_store.get("user-1")
    assert profile is not None
    assert profile.metadata.get("major") == "计算机科学"
    assert profile.version >= 2
    assert len(follow_ups) == 1
    spec = follow_ups[0]
    assert spec.kind == "path_rebuild"
    assert spec.dedupe_key == f"path_rebuild:{profile.version}"
    assert spec.payload["user_id"] == "user-1"
    assert spec.payload["profile_version"] == profile.version
    assert spec.payload["profile"]["metadata"]["major"] == "计算机科学"
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(
        (getattr(e, "metadata", None) or {}).get("profile_updated") is True
        for e in events
    )


@pytest.mark.asyncio
async def test_plain_question_skips_extractor(isolated_store):
    extractor = FeatureExtractorAgent(llm=_mock_llm({"major": "不应出现"}))
    ingested, follow_ups = await ingest_dialogue_signal(
        _context("什么是反向传播？"),
        StreamBus(),
        builder=ProfileBuilder(store=isolated_store),
        extractor=extractor,
    )
    assert ingested is False
    assert follow_ups == ()
    assert await isolated_store.get("user-1") is None


@pytest.mark.asyncio
async def test_extractor_failure_degrades_to_noop(isolated_store):
    class _BoomExtractor:
        async def process(self, context, stream=None):
            raise RuntimeError("llm down")

    ingested, follow_ups = await ingest_dialogue_signal(
        _context("我是CS研一"),
        StreamBus(),
        builder=ProfileBuilder(store=isolated_store),
        extractor=_BoomExtractor(),
    )
    assert ingested is False
    assert follow_ups == ()
```

注意：`UnifiedContext`/`StreamBus` 的精确构造与事件出队方式以 `backend/tests/capabilities/test_profile_capability.py` 的既有写法为准（例如是否需要 `await bus.close()` 后 drain）；如 `ProfileStore(tmp_path / "profiles.db")` 构造签名不同，按 `test_store.py` 的实际签名调整。

- [ ] **Step 2: 运行确认失败**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/learner_profile/test_dialogue_ingest.py -q
```

预期：FAIL（`ModuleNotFoundError: tutor.services.learner_profile.dialogue_ingest`）。

- [ ] **Step 3: 实现摄取器**

创建 `backend/tutor/services/learner_profile/dialogue_ingest.py`：

```python
"""Dialogue-driven profile ingestion (conversational profile building).

Post-answer, best-effort companion of the answering capabilities: when the
student's message carries profile signal (see ``signal_detector``), run the
LLM feature extractor, merge the diff, emit a visible ``profile_updated``
observation (the job runner normalizes it to a ``progress`` event whose
metadata keeps the marker — the frontend refreshes the profile panel on it)
and schedule ``path_rebuild`` for the new profile version.

Never raises: every failure degrades to a WARNING log line so the answering
pipeline is never disturbed. See
docs/superpowers/specs/2026-07-19-conversational-profile-building-design.md
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from tutor.core.capability_result import FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.signal_detector import detect_profile_signal
from tutor.services.learner_profile.store import get_profile_store

INGEST_TIMEOUT_SECONDS = 20.0


async def ingest_dialogue_signal(
    context: UnifiedContext,
    stream: StreamBus,
    *,
    builder: Any = None,
    extractor: Any = None,
) -> tuple[bool, tuple[FollowUpTaskSpec, ...]]:
    """Best-effort wrapper: swallow every failure, bound total latency."""
    try:
        return await asyncio.wait_for(
            _ingest(context, stream, builder=builder, extractor=extractor),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - best effort by design
        logger.warning(
            "dialogue profile ingest failed; skipped user={user}",
            user=context.user_id,
        )
        return False, ()


async def _ingest(
    context: UnifiedContext,
    stream: StreamBus,
    *,
    builder: Any,
    extractor: Any,
) -> tuple[bool, tuple[FollowUpTaskSpec, ...]]:
    from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
    from tutor.services.learner_profile.builder import ProfileBuilder

    store = get_profile_store()
    builder = builder or ProfileBuilder(store=store)
    existing = await store.get(context.user_id)
    if not detect_profile_signal(
        context.user_message, has_profile=existing is not None
    ):
        return False, ()

    extractor = extractor or FeatureExtractorAgent()
    signal = await extractor.process(context, stream=stream)
    before_version = existing.version if existing is not None else 0
    updated, diff = await builder.ingest_signal(context.user_id, signal)
    if diff.is_empty() or updated.version <= before_version:
        return False, ()

    await stream.observation(
        "已从对话更新学习画像",
        source="profile_dialogue_ingest",
        stage="profile_dialogue_ingest",
        metadata={
            "profile_updated": True,
            "version": updated.version,
            "major": str(updated.metadata.get("major") or ""),
            "goal_type": updated.motivation.goal_type.value,
        },
    )

    follow_ups: list[FollowUpTaskSpec] = []
    if await store.get_path(context.user_id, updated.version) is None:
        follow_ups.append(
            FollowUpTaskSpec(
                kind="path_rebuild",
                dedupe_key=f"path_rebuild:{updated.version}",
                payload={
                    "user_id": context.user_id,
                    "profile_version": updated.version,
                    "profile": updated.model_dump(mode="json"),
                },
            )
        )
    return True, tuple(follow_ups)


__all__ = ["INGEST_TIMEOUT_SECONDS", "ingest_dialogue_signal"]
```

- [ ] **Step 4: 运行确认通过**

同 Step 2 命令；预期：3 个测试全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/tutor/services/learner_profile/dialogue_ingest.py backend/tests/services/learner_profile/test_dialogue_ingest.py
git commit -m "feat: ingest dialogue signals into the learner profile"
```

---

### Task 4: tutoring 能力接线

**Files:**
- Modify: `backend/tutor/capabilities/tutoring.py`（`run()` 末尾，payload/return 之前）
- Test: `backend/tests/capabilities/test_tutoring_dialogue_ingest.py`（新建）

**Interfaces:**
- Consumes: `ingest_dialogue_signal`（Task 3）。
- Produces: `TutoringCapability.run()` 返回的 `CapabilityResult.follow_up_tasks` 携带摄取器返回的 specs（此前 tutoring 无 follow_up_tasks）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/capabilities/test_tutoring_dialogue_ingest.py`。用最小 canned fakes 满足 `run()` 的下游消费（payload 构造见 `tutoring.py` 尾部：`understanding.to_dict()`、`answer.to_dict()`、`answer.tldr`、`enrichments` 列表、`tutor_service.record_interaction/get_history`、profile snapshot 读取、`web_outcome.search_used`/`web_sources`）：

```python
"""Tutoring capability: dialogue ingest wiring (post-answer, best-effort)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tutor.capabilities.tutoring import TutoringCapability
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


def _canned_agents():
    understanding = SimpleNamespace(
        to_dict=lambda: {"question_type": "concept"},
        follow_up_questions=[],
    )
    answer = SimpleNamespace(
        to_dict=lambda: {"tldr": "答"},
        tldr="答",
    )
    question_agent = SimpleNamespace(
        process=AsyncMock(return_value=understanding)
    )
    tutoring_agent = SimpleNamespace(process=AsyncMock(return_value=answer))
    enrichment_agent = SimpleNamespace(process=AsyncMock(return_value=[]))
    tutor_service = SimpleNamespace(
        record_interaction=lambda **kw: None,
        get_history=lambda user_id: [],
    )
    return question_agent, tutoring_agent, enrichment_agent, tutor_service


@pytest.mark.asyncio
async def test_follow_up_specs_from_ingest_reach_result(monkeypatch):
    spec = FollowUpTaskSpec(
        kind="path_rebuild",
        dedupe_key="path_rebuild:2",
        payload={"user_id": "user-1", "profile_version": 2, "profile": {}},
    )
    calls = []

    async def fake_ingest(context, stream):
        calls.append(context.user_message)
        return True, (spec,)

    monkeypatch.setattr(
        "tutor.capabilities.tutoring.ingest_dialogue_signal", fake_ingest
    )
    qa, ta, ea, ts = _canned_agents()
    capability = TutoringCapability(
        question_agent=qa,
        tutoring_agent=ta,
        enrichment_agent=ea,
        tutor_service=ts,
    )
    context = UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message="什么是反向传播？",
        language="zh",
        capability="tutoring",
    )
    result = await capability.run(context, StreamBus())
    assert calls == ["什么是反向传播？"]
    assert spec in result.follow_up_tasks


@pytest.mark.asyncio
async def test_no_signal_leaves_follow_ups_empty(monkeypatch):
    async def fake_ingest(context, stream):
        return False, ()

    monkeypatch.setattr(
        "tutor.capabilities.tutoring.ingest_dialogue_signal", fake_ingest
    )
    qa, ta, ea, ts = _canned_agents()
    capability = TutoringCapability(
        question_agent=qa,
        tutoring_agent=ta,
        enrichment_agent=ea,
        tutor_service=ts,
    )
    context = UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message="什么是反向传播？",
        language="zh",
        capability="tutoring",
    )
    result = await capability.run(context, StreamBus())
    assert result.follow_up_tasks == ()
```

实现提示：若 `run()` 还调用检索/联网（`self._retrieval`、`self._search`），给构造器传 `retrieval_service=`/`search_executor=` 的轻量 fake（按 `tutoring.py` 中它们的实际调用面：返回带 `search_used=False`、`sources=[]`、`snippets=[]` 之类属性的 SimpleNamespace，以 `tutoring.py` 实际读取的字段为准）；profile snapshot 由注入的 `builder`（tmp store 的 `ProfileBuilder`）提供或让其走 `get_profile_builder()` 的 tmp 单例（`monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))` + 单例重置，同 Task 3 fixture 模式）。

- [ ] **Step 2: 运行确认失败**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/capabilities/test_tutoring_dialogue_ingest.py -q
```

预期：FAIL（`AttributeError: module 'tutor.capabilities.tutoring' has no attribute 'ingest_dialogue_signal'`）。

- [ ] **Step 3: 接线**

`backend/tutor/capabilities/tutoring.py`：模块顶部 import 区加

```python
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal
```

`run()` 中"Emit final result"段之前（session recording 之后）插入：

```python
        # Conversational profile building: best-effort, post-answer. The
        # student's answer has already streamed; extraction latency only
        # delays job terminalisation by a bounded (20s) margin.
        _, profile_follow_ups = await ingest_dialogue_signal(context, stream)
```

并把 `return CapabilityResult(...)` 改为携带 `follow_up_tasks=profile_follow_ups`。

- [ ] **Step 4: 运行确认通过**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/capabilities/test_tutoring_dialogue_ingest.py backend/tests/capabilities -q
```

预期：新测试 PASS，`backend/tests/capabilities` 全绿。

- [ ] **Step 5: 提交**

```powershell
git add backend/tutor/capabilities/tutoring.py backend/tests/capabilities/test_tutoring_dialogue_ingest.py
git commit -m "feat: ingest dialogue profile signals after tutoring answers"
```

---

### Task 5: resource_generation 接线 + 专业进 prompt snapshot

**Files:**
- Modify: `backend/tutor/capabilities/resource_generation.py`（`run()` 返回 `CapabilityResult` 前；`follow_up_tasks` 构造处，约 `:1117-1161`）
- Test: `backend/tests/capabilities/test_resource_generation_capability.py`（追加，复用该文件既有 fixture/scaffolding）

**Interfaces:**
- Consumes: `ingest_dialogue_signal`（Task 3）；Task 2 的 `to_summary()` major/level。
- Produces: `ResourceGenerationCapability.run()` 返回的 `follow_up_tasks` = 既有 video_render specs + 摄取器 specs；`profile_snapshot`（`to_summary()`）含 `major` 键流入各生成 Agent 提示词的 `## 学生画像` JSON 段。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/capabilities/test_resource_generation_capability.py` 末尾追加两个测试（沿用该文件构造 capability/context 的既有 helper；先读该文件确认 fixture 名称）：

```python
@pytest.mark.asyncio
async def test_dialogue_ingest_follow_ups_appended(monkeypatch, ...):
    """Self-intro turns attach path_rebuild after the video follow-ups."""
    from tutor.core.capability_result import FollowUpTaskSpec

    spec = FollowUpTaskSpec(
        kind="path_rebuild",
        dedupe_key="path_rebuild:2",
        payload={"user_id": "user-1", "profile_version": 2, "profile": {}},
    )

    async def fake_ingest(context, stream):
        return True, (spec,)

    monkeypatch.setattr(
        "tutor.capabilities.resource_generation.ingest_dialogue_signal",
        fake_ingest,
    )
    # …沿用本文件既有方式构建 capability + context 并 await run(...)…
    assert spec in result.follow_up_tasks
    # 既有 video_render follow-ups 仍然保留
    assert any(s.kind == "video_render" for s in result.follow_up_tasks)


@pytest.mark.asyncio
async def test_profile_snapshot_carries_major_into_generation(...):
    """`to_summary()` major flows into the snapshot handed to the agents."""
    # …沿用本文件既有方式：预置 metadata.major 的画像 → run →
    # 断言传入生成阶段（或 context.metadata["profile_snapshot"]）的 dict 中
    # snapshot["major"] == "计算机科学"…
```

- [ ] **Step 2: 运行确认失败**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/capabilities/test_resource_generation_capability.py -q -k "dialogue_ingest or snapshot"
```

预期：FAIL（`AttributeError: ... no attribute 'ingest_dialogue_signal'` / snapshot 断言失败）。

- [ ] **Step 3: 接线**

`backend/tutor/capabilities/resource_generation.py`：模块顶部 import 区加

```python
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal
```

在构造最终 `CapabilityResult`（约 `:1140` 起）之前插入：

```python
        # Conversational profile building: best-effort, post-package. This
        # run intentionally still used the pre-turn profile (same-turn async
        # decision in the approved design).
        _, profile_follow_ups = await ingest_dialogue_signal(context, stream)
        follow_up_tasks = (*follow_up_tasks, *profile_follow_ups)
```

（确认该处 `follow_up_tasks` 变量名；若实际不同，按实际变量拼接后传入 `follow_up_tasks=...。`）

- [ ] **Step 4: 运行确认通过**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/capabilities/test_resource_generation_capability.py -q
```

预期：全部 PASS。

- [ ] **Step 5: 提交**

```powershell
git add backend/tutor/capabilities/resource_generation.py backend/tests/capabilities/test_resource_generation_capability.py
git commit -m "feat: ingest dialogue profile signals after resource generation"
```

---

### Task 6: 前端——`profile_updated` 事件刷新 + 面板身份卡

**Files:**
- Modify: `frontend/lib/event-handler.ts`（`dispatchStreamEvent` 内，`applyStreamEvent` 调用之后，约 `:212` 附近）
- Modify: `frontend/components/profile/ProfilePanel.tsx`（概览 Tab，认知风格卡片之前，约 `:246`）
- Test: `frontend/lib/event-handler.test.ts`（追加）
- Test: `frontend/components/profile/ProfilePanel.test.tsx`（追加）

**Interfaces:**
- Consumes: 后端事件契约 `type=="progress" && metadata.profile_updated===true`（Task 3 的 observation 经 runner 归一化后的形态）；`refreshLearningState(userId, course)`（`frontend/lib/learning-state.ts`，course 参数未用，传 `""`）；`LearnerProfileDetail.metadata`（`frontend/lib/types.ts:190`）。
- Produces: 收到 `profile_updated` 事件时自动重新拉取画像+路径；概览页显示"专业与层次"卡片。

- [ ] **Step 1: 写失败测试**

`frontend/lib/event-handler.test.ts` 追加（沿用该文件既有的 dispatch 入口与 `vi.mock("./api", …)` mock 方式，补 mock `getProfile`、`getLearningPath`）：

```ts
it("refreshes learning state when a profile_updated marker event arrives", async () => {
  // …沿用本文件既有 mock/store 初始化…
  dispatchStreamEvent(
    {
      type: "progress",
      source: "profile_dialogue_ingest",
      stage: "profile_dialogue_ingest",
      content: "已从对话更新学习画像",
      metadata: { job_id: "job-1", profile_updated: true, version: 2 },
      session_id: "s",
      turn_id: "t",
      seq: 1,
      timestamp: Date.now() / 1000,
      event_id: "e1",
    } as any,
    { sessionId: "s", userId: "local-user" },
  );
  await vi.waitFor(() => {
    expect(getProfile).toHaveBeenCalledWith("local-user");
    expect(getLearningPath).toHaveBeenCalledWith("local-user");
  });
});

it("ignores progress events without the profile_updated marker", async () => {
  // 同上但不带 profile_updated → expect(getProfile).not.toHaveBeenCalled()
});
```

`frontend/components/profile/ProfilePanel.test.tsx` 追加：

```tsx
it("shows major and level from profile metadata", () => {
  // …沿用本文件既有方式渲染带
  // metadata: { major: "计算机科学", level: "graduate" } 的画像…
  expect(screen.getByText("专业与层次")).toBeInTheDocument();
  expect(screen.getByText(/计算机科学 · 硕士/)).toBeInTheDocument();
});
```

- [ ] **Step 2: 运行确认失败**

```powershell
cd E:\github\TutorBot\frontend
npx vitest run lib/event-handler.test.ts components/profile/ProfilePanel.test.tsx
```

预期：两个新测试 FAIL。

- [ ] **Step 3a: event-handler 新增事件分支**

`frontend/lib/event-handler.ts`：import 区加

```ts
import { refreshLearningState } from "./learning-state";
```

在 `useTutorStore.getState().applyStreamEvent(streamEv);`（`dispatchStreamEvent` 第 1 步）之后、`switch` 之前插入：

```ts
  // Conversational profile building: the backend marks a dialogue-driven
  // profile update with metadata.profile_updated (the job runner
  // normalizes the observation to a "progress" event but keeps metadata).
  // Refresh profile + path so the panel reflects the new version without
  // a manual reload.
  if (
    (streamEv.metadata as Record<string, unknown> | undefined)
      ?.profile_updated === true
  ) {
    void refreshLearningState(context.userId || useTutorStore.getState().userId, "");
  }
```

- [ ] **Step 3b: ProfilePanel 身份卡**

`frontend/components/profile/ProfilePanel.tsx`：lucide import 块加 `GraduationCap`；在概览 Tab 的"认知风格"`DimensionCard` 之前插入（先在该组件内解析 metadata）：

```tsx
      {(() => {
        const metadata = (profile.metadata ?? {}) as Record<string, unknown>;
        const major = typeof metadata.major === "string" ? metadata.major : "";
        const levelKey = typeof metadata.level === "string" ? metadata.level : "";
        const levelLabel =
          (
            {
              high_school: "高中",
              undergraduate: "本科",
              graduate: "硕士",
              phd: "博士",
              professional: "职场",
            } as Record<string, string>
          )[levelKey] ?? "";
        return (
          <DimensionCard
            icon={GraduationCap}
            name="专业与层次"
            value={[major, levelLabel].filter(Boolean).join(" · ")}
            detail=""
          />
        );
      })()}
```

（插入位置：概览 Tab 渲染 `认知风格` DimensionCard 的紧邻上方；若概览 Tab 是独立函数组件，把 metadata 解析放在该函数体顶部，卡片 JSX 保持同构。）

- [ ] **Step 4: 运行确认通过**

```powershell
cd E:\github\TutorBot\frontend
npx vitest run lib/event-handler.test.ts components/profile/ProfilePanel.test.tsx
npm test -- --run
npm run type-check
```

预期：全部 PASS，type-check 干净。

- [ ] **Step 5: 提交**

```powershell
git add frontend/lib/event-handler.ts frontend/lib/event-handler.test.ts frontend/components/profile/ProfilePanel.tsx frontend/components/profile/ProfilePanel.test.tsx
git commit -m "feat: refresh profile panel on dialogue updates and show major"
```

---

### Task 7: 集成测试 + 全量验证

**Files:**
- Create: `backend/tests/integration/test_conversational_profile.py`

**Interfaces:**
- Consumes: Task 1-5 的全部产物；`PathRebuildFollowUpCapability`（`backend/tutor/services/jobs/follow_up.py:956`）消费 Task 3 产生的 spec payload。

- [ ] **Step 1: 写集成测试**

创建 `backend/tests/integration/test_conversational_profile.py`（fixture 模式同 Task 3 的 `isolated_store`，KG 服务用仓库自带 `ai_introduction` 课程或测试替身）：

```python
"""Self-introduction → profile built → path rebuilt for the new version."""

from __future__ import annotations

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.follow_up import PathRebuildFollowUpCapability
from tutor.services.learner_profile.builder import ProfileBuilder
from tutor.services.learner_profile.dialogue_ingest import ingest_dialogue_signal


@pytest.mark.asyncio
async def test_self_intro_builds_profile_and_path(isolated_store, ...):
    # 1) 自我介绍经摄取器（mock LLM 返回 major/level/knowledge/motivation）
    # 2) 画像含 major、版本 ≥2、to_summary()["major"] 非空
    # 3) follow_ups 含一条 path_rebuild spec
    # 4) 用 spec.payload 构造 UnifiedContext 跑 PathRebuildFollowUpCapability
    #    → store.get_path(user, version) 非 None，path.profile_version == 新版本
```

断言要点：画像 `metadata.major` 持久化；`to_summary()` 含专业；`path_rebuild` 幂等（同版本重复 spec → 返回已有路径不报错）。

- [ ] **Step 2: 运行集成测试**

```powershell
cd E:\github\TutorBot
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/integration/test_conversational_profile.py -q
```

预期：PASS。

- [ ] **Step 3: 全量回归**

```powershell
cd E:\github\TutorBot
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q
cd frontend
npm test -- --run
npm run type-check
```

预期：后端仅有 Global Constraints 列出的 5 个预存在环境失败；前端全绿；type-check 干净。`git status --short` 只允许看到 `frontend/next-env.d.ts`、`package-lock.json` 与两个未跟踪的 persistence 文档。

- [ ] **Step 4: 提交**

```powershell
git add backend/tests/integration/test_conversational_profile.py
git commit -m "test: cover conversational profile building end to end"
```

---

## Self-Review 记录

- Spec 覆盖：检测器(1)→T1；摄取器(2)→T3；tutoring/resource 接线(3)→T4/T5；专业进提示词(4)→T2+T5；面板显示+事件刷新(5)→T6；路径联动(6)→T3 内嵌+T7 验证。错误处理与 best-effort → T3 实现与测试。无缺口。
- 类型一致性：`ingest_dialogue_signal` 签名在 T3 定义、T4/T5 消费一致；`FollowUpTaskSpec` 字段与 `PathRebuildFollowUpCapability` payload 键（`profile_version`/`profile`）与 `follow_up.py:975-976` 的实际读取一致；前端 marker 契约 `metadata.profile_updated` 与 T3 observation 一致。
- 占位符扫描：T5 两个测试中含"沿用本文件既有方式"的脚手架指引——这是有意的：该文件 fixture 名称需实现者现场确认，测试断言本体已给出。其余代码均为完整可落地内容。
