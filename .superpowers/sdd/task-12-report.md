# Task 12 report — Manim preflight, meaningful retries, and complete failures

- status: `DONE_WITH_CONCERNS`
- base commit: `d9319e1`
- implementation commit: `47d38dd3a1ebb7e838e9ff5564feaa9ce215ff4a`
- report-finalization commit: the follow-up commit containing this self-referential update

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

## Fix wave 1 — review findings

### Retry completion race and ready-resource guard

RED command:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/api/test_video_render_retry.py -k "completion_race or rejects_ready" -q
```

- Result: `2 failed, 2 deselected`.
- Expected failures: the completion race returned stale `status='pending'` instead of `succeeded` and overwrote the terminal resource; a ready video returned HTTP 200 instead of 409.

GREEN with child-active serialization, retry revision tagging, and the ready guard: `2 passed, 2 deselected` in 9.88s. The full retry API file then passed `4 passed` in 10.69s, preserving repeated-click active-child idempotency.

### Frontend retry lifecycle reconciliation

RED command:

```powershell
npm test -- --run components/resources/VideoViewer.test.tsx -t "reconciles a new retry revision"
```

- Result: `1 failed, 6 skipped`.
- Expected failure: after a successful retry response, the viewer continued to render the old child failure (`旧渲染失败`) because neither the new child nor the current resource snapshot was reconciled into the canonical store.

GREEN added typed retry snapshots, immutable historical-child preservation, current-revision child selection, parent-detail polling, and terminal package refresh. The focused regression passed `1 passed, 6 skipped`; the complete focused frontend set then passed `3 files, 17 tests` after the pending-state banner retained the queue feedback.

### Public diagnostics and downloadable render logs

RED command:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/manim_render/test_executor.py::test_public_tail_redacts_spaced_unc_posix_file_uri_and_credentials backend/tests/api/test_resources_artifact_endpoint.py::test_downloadable_render_log_is_complete_sanitized_utf8 -q
```

- Result: `2 failed`.
- Expected failures: the public tail leaked UNC/POSIX paths and short provider credentials, while the downloadable artifact exposed raw paths and credentials and had no separate operator-only copy.

GREEN added one complete public-diagnostic sanitizer for quoted/spaced drive, UNC, POSIX, and file-URI paths plus strict diagnostic credential assignments. Raw UTF-8 streams now live only under `operator_logs/manim`; the downloadable artifact is a full, non-truncated sanitized copy. Result: `2 passed` in 7.99s.

### Keyword and sound asset discovery

RED command:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/manim_render/test_static_guard.py -k "keyword_and_sound" -q
```

- Result: `3 failed, 2 passed, 15 deselected`.
- Expected failures: keyword-only `ImageMobject`/`SVGMobject` references were omitted, and positional/qualified `add_sound` expressions were ignored instead of classified as external or dynamic assets.

GREEN uses source-position-ordered AST calls and per-API positional/keyword argument specifications for direct and qualified `SVGMobject`, `ImageMobject`, and `add_sound` forms. Literal references retain containment/existence validation; non-literals fail closed. Result: `5 passed, 15 deselected` in 8.42s.

### Missing-code and unexpected-internal terminal failures

After correcting a test-fixture-only directory setup mistake, the valid RED command was:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_video_render_follow_up.py -k "missing_code_child or internal_exception_child" -q
```

- Result: `2 failed, 4 deselected`.
- Expected failures: both persisted failed resources lacked the durable `render_job_id`; the missing-code path had no structured failure/log, and the unexpected-exception path had an empty tail/log projection.

GREEN routes both branches through one safe structured-failure writer, creates a public render-log manifest before the durable child terminal event, and stores full unexpected exception details only in the operator log. Result: `2 passed, 4 deselected` in 9.50s.

### Integration correction

The first exact backend integration run found `7 failed, 77 passed`: legacy capability fakes did not accept the already-established durable `job_id` render argument, so success fixtures entered the new internal-exception branch; two assertions also expected the obsolete unstructured `VIDEO_RENDER_FAILED` code. Updating only those fixtures/assertions made the complete capability file pass `14 passed` in 12.44s.

### Fix-wave integration verification

The exact required backend command passed `84 passed` in 62.85s:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/manim_render backend/tests/services/jobs/test_video_render_follow_up.py backend/tests/api/test_video_render_retry.py backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/agents/resource/test_manim_video_inline_resource.py -q
```

The review note's literal frontend command combines `npm --prefix frontend` with paths already prefixed by `frontend/`, so Vitest correctly reported no matching files from its `frontend/` root. The repository-equivalent command passed `3 files, 17 tests` in 9.17s:

```powershell
npm --prefix frontend test -- --run components/resources/VideoViewer.test.tsx lib/api.test.ts lib/store.test.ts
```

- Focused Ruff initially found one import-order issue in `service.py`; Ruff's mechanical import fix was applied, and the complete changed-backend/test path set then reported `All checks passed!`.
- `npm --prefix frontend run type-check` remains non-zero only for the same pre-existing unrelated files listed above; no Task 12 file appears in the output.
- The complete public resource artifact endpoint file passed `10 passed` in 10.16s.
- The explicit real-Manim service pipeline plus durable child pair passed `2 passed` in 23.89s. The durable missing-SVG preflight plus real child pairing also passed `2 passed, 4 deselected` in 16.01s.
- `git diff --check` passed; Git emitted only the repository's LF-to-CRLF working-copy notices.

Final privacy self-review found one defense-in-depth gap: a tampered historical resource manifest could point directly at `operator_logs/` even though Task 12 itself never emits such a manifest. The regression was RED at `1 failed` (HTTP 200), then GREEN at `1 passed` in 3.13s after the artifact resolver made the operator subtree categorically non-public.

The same review injected a raw credential and spaced drive path into a child that terminalized during the retry race. RED was `1 failed` because the retry snapshot echoed `child.error`; after routing that field through the public diagnostic sanitizer, the retry privacy regression and operator-subtree regression passed together: `2 passed` in 9.67s.

The final retry review strengthened repeated-click idempotency to cover scheduler wakeups as well as child creation. RED showed two `resume_pending` awaits for one active revision; the reset now recognizes an already-tagged pending/rendering revision, and the complete retry API file passed `4 passed` in 10.70s with exactly one wakeup.

Final post-review evidence: the exact required backend command passed `84 passed` in 64.96s; the complete artifact endpoint file passed `11 passed` in 9.99s; and the repository-corrected focused frontend command passed `3 files, 17 tests` in 8.45s.

## Fix wave 2 — re-review findings

### Retry polling, refresh recovery, and deterministic cleanup

RED command:

```powershell
npm --prefix frontend test -- --run components/resources/VideoViewer.test.tsx -t "transient|visible recovery|settles|cancelling"
```

- Result: `4 failed, 2 passed, 7 skipped`.
- Expected failures: transient poll and terminal-refresh errors were invisible and stopped synchronization, repeated failures exposed no recovery action, and the requested cancellable polling primitive did not exist. The two behavioral cleanup assertions passed because `clearTimeout` removed timers, but the direct settlement regression correctly proved the awaited Promise remained unsupported.

GREEN retries polling and terminal package refresh independently, preserves the terminal-refresh phase, pauses after three consecutive failures with a visible `继续同步视频状态` action, and uses a cancellable delay whose cancel operation settles the active wait. Focused result: `6 passed, 7 skipped` in 8.09s. The complete Viewer plus store set then passed `2 files, 17 tests` in 8.06s.

### Durable render revision in the strict video schema

RED command:

```powershell
$env:PYTHONPATH='backend'
& 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/resource_package/test_schema.py -k "render_job_id" -q
```

- Result: `4 failed, 20 deselected`.
- Expected failures: pending, ready, and failed payloads carrying `render_job_id` returned `None` from strict parsing, while a legacy payload parsed but exposed no optional revision attribute.

GREEN declares `VideoResource.render_job_id: str | None = None`; all three durable states round-trip and legacy resources retain compatibility. Result: `4 passed, 20 deselected` in 1.78s.

### Fix-wave-2 phase-boundary review

Self-review strengthened the terminal-refresh regression so two transient job-detail failures precede a successful terminal observation and a transient package-refresh failure. Before resetting the consecutive-failure budget at the phase boundary, the focused test was RED (`1 failed, 12 skipped` in 8.60s): the package refresh paused after its first failure and was called only once. Resetting the budget after a successful job-detail response made the same test GREEN (`1 passed, 12 skipped` in 8.38s), with exactly three job-detail calls and two package-detail calls.

The final implementation keeps the current retry revision visible while synchronization is pending, uses bounded linear backoff per phase, and offers an explicit recovery action after three consecutive failures. Effect cleanup both cancels the timer and settles the awaited delay; responses that arrive after an unmount or dependency change are ignored. Existing historical/current-revision selection, request-failure, and store behavior remain covered by the complete Viewer/store test set.

### Fix-wave-2 integration verification

- The exact required frontend command passed `2 files, 17 tests` in 8.41s.
- The exact required backend command passed `34 passed` in 22.88s (35 existing warnings).
- Focused Ruff for both changed backend paths reported `All checks passed!`.
- `git diff --check` passed; Git emitted only the repository's LF-to-CRLF working-copy notices.
- `npm --prefix frontend run type-check` remains non-zero only for pre-existing unrelated files; neither changed Task 12 TypeScript path appears in its diagnostics.
- A changed-path ESLint invocation cannot run because this repository has ESLint 9 dependencies but no flat `eslint.config.*`; this is a repository tooling limitation, not a reported Task 12 source diagnostic.

Remaining concerns: repository-wide TypeScript checking is still blocked by the pre-existing unrelated errors described above, and scoped ESLint remains unavailable until the repository adds an ESLint 9 flat configuration.

Fix-wave-2 implementation commit: `eb4abc7` (`fix: recover video retry synchronization`).
