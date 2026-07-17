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
