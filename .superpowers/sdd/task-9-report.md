# Task 9 Report — User-triggered full Manim regeneration backend

## Outcome

Implemented a durable, owner-scoped `video_repair_render` follow-up that regenerates a complete `MainScene`, validates at most two LLM candidates, and performs exactly one real Manim render. Initial rendering is now terminal after one executor invocation and never enters the legacy LLM patch loop.

The existing retry URL remains compatible. Enqueueing a repair preserves the failed source, failed render status, visible error, failure record, prior video fields, and log artifacts while setting only `repair_status=pending` and `repair_job_id`.

## Scope

Created:

- `backend/tutor/agents/resource/manim_repair.py`
- `backend/tutor/prompts/resource/zh/manim_repair.yaml`
- `backend/tutor/services/manim_render/candidate_validation.py`
- `backend/tests/agents/resource/test_manim_repair.py`
- `backend/tests/services/manim_render/test_candidate_validation.py`

Modified brief files:

- `backend/tutor/services/manim_render/service.py`
- `backend/tutor/services/manim_render/code_retry.py`
- `backend/tutor/services/jobs/follow_up.py`
- `backend/tutor/api/routers/resources.py`
- `backend/tutor/services/resource_package/schema.py`
- `backend/tests/services/manim_render/test_service.py`
- `backend/tests/services/manim_render/test_code_retry.py`
- `backend/tests/capabilities/test_video_render_fire_and_forget.py`
- `backend/tests/api/test_resources_artifact_endpoint.py`

Authorized adjacent compatibility test update:

- `backend/tests/api/test_video_render_retry.py`

Unrelated `frontend/next-env.d.ts` was pre-existing, left untouched, and excluded from staging.

## TDD evidence

### Initial render and legacy patch utility

RED command:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render/test_service.py backend/tests/services/manim_render/test_code_retry.py -q
```

RED result: `2 failed, 23 passed`.

- Initial runtime failure called the executor 3 times instead of once.
- Legacy `run_time=0` search incorrectly changed `run_time=0.5` to `run_time=1.5`.

GREEN result after the minimal implementation: `25 passed`.

Implementation:

- StaticGuard runs before execution.
- Executor is invoked exactly once.
- Runtime failure returns its structured `RenderFailure` and log key without calling `CodeRetry` or an LLM.
- Legacy `_apply_patches` remains available but requires one unique exact match aligned to token boundaries.

### Repair agent and deterministic validation

Initial RED collection result: 2 missing target modules. Importable API skeletons were then added so behavior-level RED could be observed.

Behavior RED result: `8 failed` from explicit `NotImplementedError`/missing prompt behavior.

GREEN command:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render/test_candidate_validation.py -q
```

GREEN result: `8 passed`.

Later self-review added a stricter complete-scene requirement:

- RED: `2 failed` because `MainScene` without a `Scene` base was accepted.
- GREEN: `2 passed` after both agent and validator required `MainScene(Scene)` plus `construct()`.

Coverage includes:

- Full failed source in the repair request.
- Stable failure code and summary.
- Sanitized, bounded traceback tail and Python/Manim versions.
- Strict one-field `{ "manim_code": "..." }` JSON response; no SEARCH/REPLACE/diff fallback.
- Syntax/compile/AST/StaticGuard checks.
- Bound method supplied to `VGroup`.
- Non-positive `run_time`.
- Unavailable uppercase Manim runtime symbol.
- Missing/dynamic external assets.
- Valid native-shape scene acceptance.

### Durable child job and API semantics

RED results:

- Capability tests: `2 failed` because `VideoRepairFollowUpCapability` did not exist.
- Endpoint test: `1 failed` because the URL still enqueued `video_render`.

First GREEN results:

- Repair capability tests: `2 passed`.
- Endpoint preservation/idempotency test: `1 passed`.
- Refresh/resume and owner isolation additions: `4 passed` for all repair-focused capability tests.

Security self-review found legacy repair-history records were count-bounded but not field-bounded/sanitized:

- RED: `1 failed` with a 1,029-character secret-bearing summary and Windows host path.
- GREEN: `1 passed` after normalizing the retained last 10 records, sanitizing/bounding fields, and accepting only safe `manim_logs/...` artifact keys.

Durability and mutation semantics:

- Payload is exactly `{package_id, resource_id, user_id, failed_revision}`.
- Active repair child is reused; terminal retries use a new dedupe attempt while preserving `failed_revision`.
- Existing JobRunner claim validation and `run_if_current_claim` fence every resource mutation.
- Owner mismatch fails before LLM or render work.
- Pending children resume through `JobRunner.resume_pending()` after a process refresh.
- First candidate validation failure permits exactly one second full regeneration using the validation issues.
- At most one real render occurs.
- Success atomically replaces source/video fields under the current child claim, sets `MainScene`, increments `source_revision`, and clears the old visible render failure.
- Failure preserves original source, render error/failure/status and video fields; only repair state/history/log manifest is appended.
- Repair history is limited to 10 normalized records; summaries are at most 200 characters and no tracebacks are persisted there.

The adjacent old retry test was initially `1 failed, 3 passed` because it asserted the removed reset-to-pending behavior. After explicit authorization, only its expectations were updated to the new invariant (`render_status=failed`, `repair_status=pending`, original code/error retained, `video_repair_render` child).

## Focused verification

Brief focused command:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/api/test_resources_artifact_endpoint.py -q
```

Result before the authorized adjacent test update: `101 passed`.

Expanded focused command including the adjacent retry compatibility tests:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/api/test_resources_artifact_endpoint.py backend/tests/api/test_video_render_retry.py -q
```

Result: `105 passed, 106 warnings`. Warnings were existing pytest-asyncio fixture deprecations and Starlette/httpx deprecation notices; no test failures or runtime warnings from this feature.

After the final history-sanitization change, its focused regression passed (`1 passed`) and Ruff remained clean.

## Ruff, compile, and runtime smoke

Initial Ruff result: 4 findings (two import-order findings, `typing` versus `collections.abc`, and exception naming). All were corrected.

Final Ruff command covered every implementation/test file in scope, including the authorized adjacent test. Result:

```text
All checks passed!
```

Compile smoke:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m compileall -q backend/tutor/agents/resource/manim_repair.py backend/tutor/services/manim_render/candidate_validation.py backend/tutor/services/jobs/follow_up.py backend/tutor/api/routers/resources.py
```

Result: exit 0.

Runtime namespace/validator smoke used the installed Manim module:

```text
manim=0.20.1 candidate_valid=True
```

Real render smoke:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render/test_service.py::test_real_render_full_pipeline -q
```

Result: `1 passed` using the installed Manim 0.20.1 runtime.

## Self-review

- Confirmed no initial-render call reaches `_ask_llm`/`fix_until_renderable`.
- Confirmed repair LLM receives complete source and sanitized bounded diagnostics, never raw operator traceback.
- Confirmed no repaired code is persisted before deterministic validation and successful render.
- Confirmed validation issues can trigger only one internal regeneration and no render of the rejected first candidate.
- Confirmed success and failure writes refetch the resource and verify both `repair_job_id` and `failed_revision` inside the current child claim guard.
- Confirmed enqueue does not delete original failure, error, code, video, or render-log artifacts.
- Confirmed public repair history contains no unbounded traceback, secret-bearing legacy summary, absolute host path, or unsafe artifact key.
- Confirmed `git diff --check` exits 0.
- Confirmed all changes are scoped to Task 9 plus the explicitly authorized adjacent retry test and this report.
- Confirmed `frontend/next-env.d.ts` remains unstaged and untouched.

## Commit

Commit message: `feat: regenerate failed Manim videos on demand`
