# Task 15 report: migration, recovery, browser E2E, and final integration

## Status

DONE

## Production-data migration and recovery

- Migrated the actual `E:\github\TutorBot\data` and `E:\github\TutorBot\backend\data` stores into the canonical root data directory without deleting either source.
- Created three pre-change backups: `local-data-20260718T112234974218Z`, `local-data-20260718T112449769678Z`, and `local-data-20260718T125557858581Z` under `E:\github\TutorBot\backups`.
- Rehearsed rollback in the independent `recovery-verification-20260718T112234974218Z` directory and confirmed the source hashes were unchanged.
- Replaced unsafe absolute-path suffix guessing with repeatable, explicit `--relocate-from` roots. Existing external files are never redirected and unresolved paths remain visible for operator review.
- Recursively normalized nested JSON ownership in addition to SQL columns. Profile collisions retain the newest watermark/version/update tuple instead of whichever row happens to be visited last.
- Made SQL row ownership authoritative when reading profiles, learning paths, learning events, and resource packages so stale embedded JSON cannot recreate a historical user or make package details disappear.

## Workflow and public-resource recovery

- Startup reconciliation now repairs a missing path for the current profile even when another five events have not arrived. It preserves the original failed job and creates at most one deterministic `path_rebuild:<version>:recovery-1` attempt.
- Reconciliation includes users represented only by profiles, not just users with event rows.
- The actual local store now has profile v3 at event watermark 11 and four-node learning paths for v2 and v3. All 47 jobs are terminal.
- Legacy failed Manim resources no longer expose raw tracebacks or host paths in public package projections. The workbench shows a terminal failure instead of an endless rendering state; protected diagnostics remain available through their existing operator artifact contract.

## Frontend and browser coverage

- Added responsive shell/sidebar/workbench behavior for desktop and 390x844 mobile viewports.
- Added deterministic Playwright fixtures for refresh persistence, parent/child terminal transitions, runnable code exercises with `.py` upload and submission history, Matplotlib natural-size/lightbox behavior, and Manim failure sanitization.
- Added real-data coverage for `sess_ebb5a8f5dfdb`, ready MP4 playback, failed Manim terminal state, profile/path version advancement, and per-conversation web-search persistence.
- Added an explicit MiniMax online guard that requires `provider=mcp`, `mcp_server=MiniMax`, and `mcp_tool=web_search`, then checks the durable terminal result event for `search_used=true` and HTTP(S) sources.
- Migrated the removed Next.js 16 `next lint` command to ESLint 9 flat configuration. Six categories of pre-existing TypeScript/React migration debt remain visible as warnings; Next.js correctness rules continue to run and lint now has a usable exit status.

## Verification

- Backend exhaustive partitions: 931 passed (`250 + 92 + 552 + 37`). A one-off parallel aiosqlite loop-close warning did not reproduce in the isolated test.
- Final changed-service/API regression: 110 passed.
- Frontend unit tests: 27 files, 190 tests passed.
- Frontend TypeScript check and Next.js production build: passed. ESLint completed with 0 errors and 181 documented pre-existing migration warnings.
- Real-data Playwright: 12 passed, 2 expected skipped. The historical code resource lacks `code_spec`; deterministic code-exercise coverage passes. MiniMax live search is intentionally isolated behind its explicit guard.
- Explicit configured MiniMax MCP Playwright: 1 passed.
- Actual post-E2E store: 25 conversations, 34 messages, 47 terminal jobs, 11 learning events, 1 profile, 72 profile events, 2 learning paths, 34 resource packages, and 135 resources. All 34 package summaries/details are readable as `local-user`.

## Review closure

- Initial independent review findings were addressed: relocation requires an allowlist; Playwright is a declared dependency; default browser tests execute deterministic fixtures; a code-spec exercise is covered; learning workflow checks require version advancement; parent/child terminal events are asserted exactly once; and the online guard is MiniMax-specific rather than generic MCP.
- Follow-up failures found by real E2E (embedded JSON owners, profile collision ordering, missing-path recovery, and legacy video traceback exposure) all received regression tests before their fixes.
- Final independent re-review: approved with no remaining Critical, Important, or Minor findings. The reviewer additionally verified multiple legacy profile ordering, older-source non-overwrite, source SHA-256 stability, nested-owner normalization, retry public projection, and a clean `next-env.d.ts` blob.
