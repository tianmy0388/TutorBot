# Task 3 Report — Atomic conversation recovery and portable artifacts

## Outcome

- Added canonical relative POSIX artifact keys with traversal/absolute-path rejection.
- New resource writes normalize legacy local paths to `artifact_key`; code sandbox and Manim serialization no longer emit absolute host paths.
- Expanded the conversation aggregate to return conversation/messages, session jobs, full packages, profile summary, path summary, and typed recovery warnings in one request.
- Conversation ownership is resolved and checked once; legacy mixed-owner job/package rows are recovered by `session_id` in creation order.
- Missing artifacts annotate their resource and produce warnings rather than failing the aggregate.
- Frontend hydration uses only the aggregate response, exposes dismissible recovery notices, and offers the stored retry contract on missing resource cards.

## RED evidence

Command:

```powershell
$env:PYTHONPATH='backend'; E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts/test_keys.py backend/tests/services/conversations/test_conversations_router.py::test_aggregate_recovers_all_session_records_after_owner_migration backend/tests/services/resource_package/test_schema.py::test_artifact_ref_serializes_only_portable_key backend/tests/services/resource_package/test_schema.py::test_artifact_ref_accepts_legacy_relative_path_without_reserializing_path -v
```

Observed: **10 failed**. Artifact tests failed because `tutor.services.artifacts` did not exist; aggregate returned no mixed-owner jobs; `ArtifactRef` did not exist.

Command:

```powershell
npm --prefix frontend test -- --run lib/api.test.ts
```

Observed: recovery hydration failed in `store.ts` at `latestPackage.resources[0]` because the old implementation issued a second package fetch and received a non-package response.

Command:

```powershell
npm --prefix frontend test -- --run components/chat/ChatMessages.test.tsx components/resources/ResourceCard.test.tsx
```

Observed: **2 failed** recovery assertions: no dismissible warning and no missing-artifact regeneration surface.

## GREEN evidence

Required backend command (fresh final run):

```powershell
$env:PYTHONPATH='backend'; E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts backend/tests/services/conversations backend/tests/services/resource_package backend/tests/api/test_resources_artifact_endpoint.py -q
```

Result: **41 passed**, exit 0.

Required frontend command (fresh final run):

```powershell
npm --prefix frontend test -- --run lib/api.test.ts lib/event-handler.test.ts
```

Result: **9 passed**, exit 0, no real-network stderr.

Expanded integration run:

```powershell
$env:PYTHONPATH='backend'; E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts backend/tests/services/conversations backend/tests/services/resource_package backend/tests/api/test_resources_artifact_endpoint.py backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py backend/tests/services/manim_render/test_service.py -v
```

Result: **49 passed, 1 skipped** (real Manim executable test), exit 0.

Frontend recovery UI run:

```powershell
npm --prefix frontend test -- --run lib/api.test.ts components/chat/ChatMessages.test.tsx components/resources/ResourceCard.test.tsx
```

Result: **9 passed**, exit 0.

## Files

- Artifact keys: `backend/tutor/services/artifacts/`
- Aggregate schema/router/store reads: conversation schema/router plus job and resource-package stores
- Artifact persistence/serving/producers: resource schema/store/router, code sandbox, Manim service
- Frontend: API/store hydration, recovery notices, missing-resource retry card
- Tests: artifact keys, aggregate recovery, schema, artifact endpoint compatibility, producers, hydration and UI

## Self-review

- Security: key resolution rejects POSIX traversal, Windows separators/drives, absolute paths, and resolved paths outside `data_dir`.
- Compatibility: legacy relative/absolute path shapes remain readable only after containment validation; new database writes drop absolute paths.
- Ownership: no duplicate identity resolution was added; `IdentityPolicy` remains the boundary and aggregate children are joined only after conversation authorization.
- Failure isolation: absent files become resource metadata plus typed warnings; artifact download returns 404 for missing files and 403 for unsafe legacy values.
- Ordering: messages, jobs, and packages hydrate in creation order; the frontend selects the final package as the latest.

## Concerns / known baseline issues

- The three existing stores use separate SQLite databases, so a cross-database transaction is not possible without prohibited new infrastructure. Each store read is transactional; the API/frontend provide one authorized aggregate snapshot/hydration boundary.
- `npm --prefix frontend run type-check` remains non-zero because of pre-existing unrelated errors in Sidebar, `useJobQueue`, `event-handler`, and a job-reducer test. Task 3's newly exposed store error-shape mismatch and stale ChatMessages fixtures were corrected.
- Ruff reports numerous pre-existing style findings in touched legacy modules/tests; no automatic broad rewrite was performed.

## Review-fix wave (2026-07-17)

Addressed all six follow-up review findings:

- Aggregate hydration now canonicalizes legacy `path`, `mp4_path`, `pptx_path`, and local `url` values before serialization. Safe local files become `artifact_key`; unsafe values are removed and reported without exposing host paths. External HTTP(S) URLs remain external.
- Successful Manim renders now place a portable `artifact_key` in the live resource before the RESOURCE event and package save; no absolute `mp4_path` enters the streamed payload.
- Artifact endpoints allow historical owner rows only in local single-user mode. Multi-user mode validates user, package, resource owner, and package/resource membership.
- Missing artifacts can synthesize recovery contracts from succeeded generation jobs. Local mode accepts stale parent owners, succeeded parents can regenerate selected artifacts, retry children preserve the parent session, and requested missing artifacts are excluded from `preserved_artifacts`.
- Job and resource-package session reads select the newest capped window and return it in chronological order.
- Frontend hydration remains deterministic: the last package in the chronological aggregate becomes `latestPackage`; the existing ResourceCard test confirms the recovery button invokes the retry API.

### RED evidence

Focused regression command covering aggregate canonicalization, Manim stream payloads, artifact identity/URL behavior, retry contracts, and newest-window ordering initially produced **8 failed, 1 passed**. The passing case was the pre-existing multi-user denial; each other review finding failed for its reported reason (legacy values leaked or were dropped, stale owners returned 404, succeeded retry was rejected, and capped queries retained the oldest rows).

### GREEN evidence

Focused backend review matrix:

```powershell
$env:PYTHONPATH='backend'; E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/conversations/test_conversations_router.py::test_aggregate_removes_legacy_absolute_paths_from_response backend/tests/capabilities/test_video_render_fire_and_forget.py::test_render_success_streams_portable_artifact_key backend/tests/api/test_resources_artifact_endpoint.py::test_local_mode_serves_historical_owner_artifacts_from_both_routes backend/tests/api/test_resources_artifact_endpoint.py::test_multi_user_mode_denies_historical_owner_artifacts backend/tests/api/test_resources_artifact_endpoint.py::test_legacy_url_normalizes_local_but_preserves_external backend/tests/services/jobs/test_retry.py::test_local_mode_retries_missing_artifact_from_succeeded_historical_job backend/tests/services/jobs/test_retry.py::test_local_mode_retries_failed_historical_job_but_multi_user_denies backend/tests/services/jobs/test_store_session_window.py backend/tests/services/resource_package/test_store_list.py::test_list_for_session_keeps_newest_window_in_chronological_order -q
```

Result: **9 passed**.

Expanded backend regression command:

```powershell
$env:PYTHONPATH='backend'; E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts backend/tests/services/conversations backend/tests/services/resource_package backend/tests/api/test_resources_artifact_endpoint.py backend/tests/services/jobs/test_retry.py backend/tests/services/jobs/test_store_session_window.py backend/tests/capabilities/test_video_render_fire_and_forget.py -q
```

Result: **58 passed**.

Frontend recovery/hydration command:

```powershell
npm --prefix frontend test -- --run lib/api.test.ts lib/event-handler.test.ts components/resources/ResourceCard.test.tsx components/chat/ChatMessages.test.tsx
```

Result: **13 passed** across 4 files, with no real-network stderr.

Final strengthened artifact-route run (including package/resource join isolation): **8 passed**.

`git diff --check` passed. Focused Ruff import-order findings introduced by this wave were fixed; broader Ruff still reports pre-existing findings in legacy touched modules, consistent with the baseline noted above.

### Review-wave self-review

- No API response contains an unsafe legacy filesystem value; warnings contain only canonical keys or `null`.
- External URLs are never resolved through the local filesystem serving path.
- Local-mode compatibility is explicit at the transport policy boundary; multi-user ownership and package/resource membership checks remain strict.
- A succeeded-parent retry regenerates only requested missing types and does not falsely preserve them.
- DESC-plus-limit followed by reversal makes truncation and chronological presentation both deterministic, including tie-breaking by row id.
