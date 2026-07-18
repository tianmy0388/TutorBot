# Task 12 report — Manim preflight, meaningful retries, and complete failures

- status: `DONE_WITH_CONCERNS`
- base commit: `d9319e1`
- implementation commit: pending at report draft time; populated after the implementation commit

## Root-cause and data-flow findings

1. `StaticGuard` parsed source but had no declared render work directory and did not inspect `SVGMobject` or `ImageMobject`, so missing, absolute, traversal, and dynamic asset references reached Manim.
2. `CodeRetry` accepted empty/non-matching LLM patches and launched Manim again with byte-identical source. Its feedback used arbitrary leading string slices, which could omit the traceback root cause at the end.
3. The executor captured streams, but timeout returned before retaining post-kill output, cancellation was projected as a generic process failure, and service failures exposed only short character tails. Executor-local Manim paths also repeated long durable child IDs enough to exceed the Windows legacy path boundary.
4. The durable `video_render` child and claim guard already provided the correct execution/fencing boundary. The missing link was structured failure persistence on the resource: the child terminalized, but the matching resource retained generic/incomplete state, leaving the viewer vulnerable to stale rendering UI.
5. `VideoViewer` selected the first matching child, treated unknown state as rendering, and had no structured details, protected log link, or durable retry action.

The corrected flow is now:

`video_render child claim -> AST/static asset preflight -> executor attempt -> complete app-owned log -> structured RenderFailure -> claim-guarded resource update -> normal terminal child event`. A user retry creates/reuses a distinct active durable child first, then resets the resource to pending and resumes the runner.

## RED evidence

All Python commands used `E:\Anaconda3\anaconda\envs\tutor\python.exe` with `PYTHONPATH=backend`; no packages were installed.

### Initial core regressions

- Result: `10 failed, 27 passed, 1 skipped`.
- Expected failures:
  - five asset tests raised `TypeError: StaticGuard.check() got an unexpected keyword argument 'workdir'`;
  - three unchanged-retry tests observed three render calls/attempts instead of one;
  - fenced/CRLF source was not normalized before hashing;
  - `RenderedVideo` had no `failure` field.

### Durable resource/API regressions

- Result: `3 failed, 1 passed`.
- Expected failures:
  - persisted failed video resource raised `KeyError: 'render_failure'`;
  - two retry endpoint requests returned HTTP `404` because the route did not exist.

### Frontend/API regressions

- Result: `4 failed, 8 passed`.
- Expected failures:
  - `retryVideoRender` was missing;
  - structured failure summary/details/log UI was absent;
  - the durable retry button/action was absent in success and request-failure cases.

### Prompt/schema/module fallback regressions

- Result: `3 failed, 26 passed, 3 skipped`.
- Expected failures: the prompt lacked the self-contained native-primitives rule, `VideoResource` rejected structured failure/artifact fields, and the Python-module Manim fallback was not represented as a tokenized command.

### Executor capture regressions

Command selected the timeout and cancellation cases in `test_executor.py`.

- Result: `2 failed, 9 deselected`.
- Expected output:
  - timeout returned `stdout == ''` instead of `complete stdout �` and lost stderr;
  - cancellation returned `RenderStatus.FAILED` instead of `RenderStatus.CANCELLED`.
- GREEN after the minimal capture/state fix: `2 passed, 9 deselected`.

### Public projection hygiene regressions

- Absolute-path case: `1 failed, 11 deselected`; traceback tail retained `C:\private\render\scene.py`. GREEN together with capture cases: `3 passed, 24 deselected`.
- Internal-exception case: `1 failed, 12 deselected`; summary exposed `provider-token=private-value`. GREEN after using a stable generic internal projection: `2 passed, 11 deselected` together with the path case.

## Implementation summary by interface

### Static guard and prompt

- Added `StaticGuardResult.external_assets: tuple[str, ...]`, `error_code`, and `summary`.
- Clean and parse the AST once, then inspect direct and qualified `SVGMobject`/`ImageMobject` calls.
- Literal references are deduplicated in source order. A reference is allowed only when relative, contained by the declared work directory after resolution, and an existing file. Absolute, traversal, missing, and dynamic references terminalize preflight with stable codes.
- Public preflight details report counts rather than host paths.
- The Chinese generation prompt now requires native Manim primitives unless a package actually supplies an asset and forbids invented filenames.

### Retry contract

- Added canonical CRLF/fence/trailing-whitespace normalization and SHA-256 source hashing before each render and after each proposed patch.
- Empty, no-op, non-matching, or previously rendered source stops before another subprocess launch with `unchanged_retry`, while retaining the prior root-cause tail and log artifact key.
- Genuinely changed source preserves the existing maximum-attempt behavior.
- Retry feedback now supplies `error_code`, the final 120 diagnostic lines in order, and an explicit correction requirement.

### Executor, logs, and failure model

- Added frozen `RenderFailure(error_code, summary, traceback_tail, log_artifact_key)` and mapped preflight, timeout, cancellation, unavailable runtime, process exit, missing output, unchanged retry, and internal errors.
- The executor uses tokenized executable/module commands, UTF-8 replacement decoding, post-timeout stream collection, and explicit cancelled-job state.
- Public summaries are bounded to 200 characters; Windows/common temporary POSIX paths are redacted from summaries and tails. Unexpected internals use a generic projection.
- Each attempt writes complete stdout/stderr to `data_dir/manim_logs/<durable-id>/...` and exposes only a portable artifact key. A short deterministic executor-local ID avoids Windows path overflow while the durable ID remains the log owner.

### Durable child/resource and retry API

- `ResourceGenerationCapability` passes the durable child ID to the service and persists structured failure, stable code/summary, and a `render_log` manifest under the existing claim guard.
- Successful children still persist `ready`, a portable MP4 artifact key, and a non-empty video; failed children persist terminal `failed` before the runner emits its normal terminal event.
- Added `POST /api/v1/resources/packages/{user_id}/{package_id}/resources/{resource_id}/retry-video` with identity/package/resource/type/originating-job checks.
- The endpoint uses `FollowUpScheduler`/`create_child_if_absent`; repeated clicks reuse an active matching child. A terminal retry receives a new dedupe revision/job ID. Resource state is reset only after durable child creation succeeds.

### Frontend

- Added the typed `retryVideoRender` client.
- `VideoViewer` uses the latest matching child and resource terminal state to avoid stale spinners. Spinner rendering is limited to pending/rendering/running.
- Failed state includes safe summary, expandable traceback tail, protected log artifact link, and durable retry success/failure feedback without mutating the terminal child.

## Verification evidence

### Focused backend

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/manim_render backend/tests/agents/resource/test_manim_video_inline_resource.py -q
```

- Final result: `54 passed` in 42.99s.
- The earlier exact `-v` command from the brief passed `53` tests before the final privacy regression was added.

### Durable job/resource/API

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_video_render_follow_up.py backend/tests/services/jobs/test_follow_up.py backend/tests/api/test_video_render_retry.py backend/tests/api/test_resources_artifact_endpoint.py backend/tests/services/resource_package -q
```

- Final result: `70 passed` in 32.06s.
- Covers terminal event/resource state, success/failure artifacts, retry idempotency/ownership, artifact serving, and package persistence.

### Frontend

```powershell
npm test -- --run components/resources/VideoViewer.test.tsx lib/api.test.ts components/resources/ResourceCard.test.tsx lib/job-reducer.test.ts lib/job-reducer-stage-lifecycle.test.ts lib/store.test.ts
```

- Final result: 6 files, `52 passed` in 10.60s.

### TypeScript

```powershell
npm run type-check
```

- First run found one Task 12 narrowing error in `VideoViewer.tsx` plus unrelated repository errors.
- After the fix, no Task 12 file appears in the error output.
- The repository-wide command remains non-zero because of pre-existing errors in `components/layout/Sidebar.tsx`, `hooks/useJobQueue.ts`, `lib/event-handler.ts`, `lib/event-handler.test.ts`, and `lib/job-reducer-stage-lifecycle.test.ts`.

### Ruff and diff hygiene

```powershell
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m ruff check <all changed backend and backend-test paths>
git diff --check
```

- Ruff: `All checks passed!`.
- Diff check: passed; Git emitted only LF-to-CRLF working-copy notices.

### Real Manim and durable child evidence

```powershell
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m manim --version
```

- Version: `Manim Community v0.20.1`.
- Real minimal native-primitives `ManimRenderService` render in the focused suite passed and produced a video larger than 1 KB.
- Real `VideoRenderFollowUpCapability` child fixture: `1 passed, 3 deselected` in 14.90s; child terminal `succeeded`, resource `ready`, app-owned MP4 exists and is non-empty.
- Missing-SVG durable fixture: `1 passed, 3 deselected` in 9.31s; child/resource terminal failed with `missing_external_asset`, `executor.render` call count zero, and no permanent running/rendering state.

## Self-review and remaining concerns

- Claim fencing remains authoritative because only the existing `_claim_guard` persists the rendered resource; stale/cancelled workers cannot overwrite a newer claim.
- Retry idempotency is bounded by active-child lookup plus durable dedupe creation, and tests verify two clicks produce one active retry child.
- API/resource projections contain artifact keys/URLs rather than host paths; structured tails and summaries are redacted and internal exceptions are generic.
- Concern: the repository-wide TypeScript check is still non-zero solely because of the pre-existing unrelated files listed above. Task 12 focused frontend tests and Task 12 TypeScript files are clean.
- Concern: pytest reports existing `pytest_asyncio` fixture and Starlette/httpx deprecation warnings; no Task 12 failure is hidden by them.
