# Task 13 report: per-conversation web-search policy and execution gate

## Status

DONE_WITH_CONCERNS

## Root-cause and data-flow findings

- Conversation persistence had no web-search setting and relied on `create_all()`, which cannot add a column to a legacy SQLite table.
- All primary WebSocket and REST plan submissions converge on `JobRunner.submit`; durable follow-up children bypass submission and are created from their persisted parent in `JobStore.create_child_if_absent`.
- The existing non-MCP `WebSearchTool` is an explicit empty placeholder and therefore cannot be accepted as evidence.
- Tutoring and resource generation had no capability-local search gate. Merely registering `web_search` globally would grant no safe per-conversation control.
- Zustand aggregate hydration is the active conversation boundary; draft creation and first-send are coordinated in `ChatComposer`.
- Reviewer follow-up found that compensating a failed draft PATCH with an unconditional DELETE could destroy an already-existing conversation and that an exception outside the submission `finally` could lose text or leave the composer spinning.
- Reviewer follow-up also found that the global mutation revision/chain allowed delayed settings completions from conversation A to alter conversation B and that stale aggregate hydration could overwrite A while its PATCH was still pending.
- A final race audit found that `useJobQueue` read the active session only after the WebSocket opened. Navigation during the handshake could therefore submit conversation A's text under conversation B's session and search choice.
- A live MiniMax MCP probe exposed Windows Proactor pipe transports surviving event-loop shutdown because reader tasks were cancelled before stdin/process teardown completed.
- Independent re-review found that capturing the submit envelope alone was insufficient: delayed ACK/stream events could still enter B, terminal persistence still read the current session, successful pre-submit navigation returned after clearing A's text, and concurrent aggregate loads could commit out of order.
- The same re-review found that awaiting `stdin.wait_closed()` before process termination required its own timeout; a child that stopped consuming stdin could otherwise block cleanup indefinitely.
- Non-document resource branches derived `source_content` only from a document/pedagogy resource. With no document planned, they advertised persisted web sources without receiving that evidence as grounding input.

## RED evidence

1. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/search/test_policy.py -q`
   Expected: collection failure because the required search policy package did not exist.
   Observed: `1 error` with `ModuleNotFoundError: No module named 'tutor.services.search'`.
2. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/conversations/test_web_search_settings.py -q`
   Expected: conversation projections lacked `web_search_enabled` and legacy initialization did not migrate the column.
   Observed: `2 failed`; create response raised `KeyError: web_search_enabled` and legacy detail raised `AttributeError` for the missing field.
3. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_web_search_snapshot.py -q`
   Expected: no injectable conversation lookup and no persisted Job snapshot.
   Observed: `2 failed`; constructor rejected `conversation_lookup` and a migrated legacy Job lacked `web_search_enabled`.
4. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_web_search_snapshot.py::test_running_context_uses_immutable_persisted_snapshot -q`
   Expected: the capability context lacked the immutable first-class snapshot.
   Observed: `1 failed`; capability raised `AttributeError` before setting its started event, producing the expected timeout assertion.
5. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_retry.py::test_rest_retry_inherits_parent_web_search_snapshot -q`
   Expected: REST retry re-read a missing conversation instead of inheriting the parent snapshot.
   Observed: `1 failed`; child `web_search_enabled` was `False` instead of parent `True`.
6. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/search/test_executor.py -q`
   Expected: the shared gate/normalization executor did not exist.
   Observed: `1 collection error`; `SearchExecutor` could not be imported from `tutor.services.search`.
7. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_tutoring_capability.py -k 'web_' -q`
   Expected: tutoring never invoked search, attached evidence to the answer context/result, or emitted typed degradation.
   Observed: `2 failed, 7 deselected`; the evidence test recorded zero search calls. The degradation test also exposed an invalid test-only `StreamBus.drain()` assumption, which was corrected to consume a real subscription queue before production was verified.
8. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_resource_generation_capability.py -k 'web_search or web_sources' -q`
   Expected: resource generation never invoked search, persisted sources, or emitted typed degradation.
   Observed: `2 failed, 21 deselected`; search recorded zero calls and no `WEB_SEARCH_UNAVAILABLE` observation existed.
9. Command: `npm --prefix frontend test -- --run components/chat/WebSearchToggle.test.tsx`
   Expected: the standalone accessible switch did not exist.
   Observed: `1 failed suite, no tests collected`; Vite could not resolve `./WebSearchToggle`.
10. Command: `npm --prefix frontend test -- --run lib/api.test.ts -t "conversation web-search settings"`
    Expected: the typed narrow PATCH client did not exist.
    Observed: `1 failed, 6 skipped`; `setConversationWebSearch is not a function`.
11. Command: `npm --prefix frontend test -- --run lib/store.test.ts -t "per-conversation web search state"`
    Expected: no aggregate hydration, optimistic mutation, serialization, or rollback state existed.
    Observed: `3 failed, 4 skipped`; hydrated setting was undefined and both mutation tests found no action.
12. Command: `npm --prefix frontend test -- --run hooks/useJobQueue.test.tsx -t "per-turn web-search"`
    Expected: submitted display metadata omitted the per-turn choice.
    Observed: `1 failed, 4 skipped`; `metadata.web_search_requested` was undefined.
13. Command: `npm --prefix frontend test -- --run components/chat/ChatComposer.web-search.test.tsx`
    Expected: first-send submitted before persisting the draft choice and had no failure stop.
    Observed: `2 failed`; settings PATCH had zero calls and the failure case had no visible status.
14. Command: `npm --prefix frontend test -- --run components/chat/ChatComposer.web-search.test.tsx lib/store.test.ts`
    Expected: a failed draft PATCH should clean up the empty row, preserve retryable draft state/text, issue another PATCH before retry submit, and roll back against known server false.
    Observed: `2 failed, 8 passed`; no draft delete occurred and the store retained optimistic true instead of the explicit server false rollback.
15. Command: `npm --prefix frontend test -- --run lib/store.test.ts`
    Expected: two serialized failed mutations from server false must end at confirmed false, while success-then-failure must end at confirmed true.
    Observed: `1 failed, 9 passed`; the double-failure case ended true. The success-then-failure and existing success sequence already matched the intended matrix.
16. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_resource_generation_capability.py::test_web_evidence_grounds_non_document_resource_branches -q`
    Expected: a non-document exercise branch must receive normalized web evidence in its actual agent `source_content`.
   Observed: `1 failed`; the capturing real-agent wrapper was invoked once with `source_content == ''`. An earlier full-pipeline version reached an unrelated package failure before this assertion, so the test was narrowed to the validated branch boundary before production changed.
17. Command: `npm --prefix frontend test -- --run components/chat/ChatComposer.web-search.test.tsx lib/store.test.ts --maxWorkers=1`
    Expected: atomic first-send retry, cross-session mutation fencing, and stale-hydration protection were not implemented.
    Observed: draft creation failure produced an unhandled rejection; the existing-conversation failure path attempted DELETE; and stale aggregate hydration changed A from its pending desired `true` back to `false`.
18. Command: `npm --prefix frontend test -- --run hooks/useJobQueue.test.tsx components/chat/ChatComposer.web-search.test.tsx --maxWorkers=1`
    Expected: a submission snapshot must remain bound to conversation A even if navigation switches to B before WebSocket `onOpen`.
    Observed: `2 failed, 8 passed`; the envelope contained `session-b` and the composer did not pass the explicit `{sessionId, webSearchRequested}` context.
19. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/mcp/test_stdio_client.py -q`
    Expected: graceful MCP shutdown closes stdin, waits for the process, and lets stdout/stderr readers observe EOF without cancellation.
    Observed before the lifecycle fix: the fake process order started with terminate/wait instead of stdin close/wait, matching the ignored Proactor destructor errors from the live MiniMax probe.
20. Commands: focused deferred race tests in `ChatComposer.web-search.test.tsx`, `useJobQueue.test.tsx`, `event-handler.test.ts`, and `store.test.ts`.
    Observed before the final review fixes: four session-event tests failed (A not submitted after navigation, ACK inserted into B, A resource routed into B, assistant not persisted to A), and the deferred aggregate test showed slow A replacing completed B. A root follow-up malformed-event test also failed because an A event without `job_id` inserted a protocol error into B.
21. Command: MCP test with a fake writer whose `wait_closed()` never completes.
    Observed before the bound: the test's outer 0.2-second timeout fired while `_kill()` was still awaiting stdin, before `terminate()` appeared in the lifecycle order.

## Implementation summary

- Conversation boundary: first-class default-off schema/row field, idempotent SQLite `ALTER TABLE`, narrow extra-forbid settings PATCH, canonical identity and existing owner checks, and all projections hydrated from the persisted row.
- Job boundary: first-class immutable row/model/context boolean, idempotent job migration, authoritative metadata overwrite, normal submission lookup, false no-session plan behavior, trusted REST retry inheritance, and durable child inheritance.
- Search boundary: shared two-factor `SearchPolicy` and lazy `SearchExecutor`; registry resolution occurs only after both gates. Results are bounded, sanitized, URL-validated, provider/timestamp normalized, and failures collapse to a stable typed outcome.
- Capability boundary: only tutoring and resource generation declare/run a `web_search` stage. Tutoring merges evidence into answer context and returns sources; resource generation supplies evidence to document generation and now also merges it into every non-document branch's `source_content`, while unavailable/empty search injects nothing. Sources persist through package/resource metadata.
- Frontend boundary: a standalone accessible switch composes separately from RAG controls. Zustand owns per-conversation setting, pending/error/materialization state, per-session serialized optimistic mutations, revision fencing, confirmed/desired values, pending hydration, and exact rollback. Draft first-send creates the conversation and initial setting atomically; existing rows use PATCH and are never deleted as compensation. All pre-submit I/O is inside one retry-safe `try/finally`. Navigation does not cancel the captured A turn: durable append/submit/subscribe continue with A while optimistic UI effects are allowed only when A remains active. `useJobQueue` freezes session/search/RAG/language metadata before the WebSocket handshake; ACK and stream dispatch carry that session. Event dispatch drops inactive-session UI effects and persists canonical terminal assistant text to the authoritative session. Aggregate hydration has a generation/target fence before any map or Zustand write. Sidebar new actions create local drafts only. Client metadata remains observable and never authorizes server search.
- MCP boundary: the configured provider remains the project's MiniMax MCP server. Graceful stdio shutdown closes stdin and waits at most one second, then terminates/awaits the subprocess and drains reader tasks to EOF with a bounded cancellation fallback, avoiding both deadlock and Windows event-loop transport leaks.

## GREEN and broad verification

1. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/search/test_policy.py -q`
   Observed: `1 passed` (one pre-existing pytest-asyncio deprecation warning).
2. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/conversations/test_web_search_settings.py -q`
   Observed: `2 passed` (two pre-existing pytest-asyncio deprecation warnings).
3. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_web_search_snapshot.py -q`
   Observed: `3 passed` (three pre-existing pytest-asyncio deprecation warnings).
4. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/jobs/test_retry.py::test_rest_retry_inherits_parent_web_search_snapshot backend/tests/services/jobs/test_web_search_snapshot.py -q`
   Observed: `4 passed` (four pre-existing pytest-asyncio deprecation warnings).
5. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/search/test_executor.py -q`
   Observed: `10 passed` (ten pre-existing pytest-asyncio deprecation warnings). The matrix includes three disabled combinations with zero registry/tool calls, bounded safe URL/source normalization, placeholder/failed/malformed/exception/missing-tool cases, and a bounded timeout.
6. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_tutoring_capability.py -k 'web_' -q`
   Observed: `2 passed, 7 deselected`; normalized sources enter the answer context/result, while unavailable search emits exactly one stable degradation and still returns an answer.
7. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_resource_generation_capability.py -k 'web_search or web_sources' -q`
   Observed: `2 passed, 21 deselected`; normalized sources enter content generation, package metadata, each resource metadata, and the capability result. The package store is closed/reopened and both package-level and resource-level sources remain present. Unavailability emits exactly one degradation and a package is still produced.
8. Command: `npm --prefix frontend test -- --run components/chat/WebSearchToggle.test.tsx components/chat/ChatComposer.web-search.test.tsx lib/api.test.ts lib/store.test.ts hooks/useJobQueue.test.tsx`
   Observed: `5 files passed, 23 tests passed`; includes accessibility, typed PATCH, aggregate hydration/switch isolation, serialized rapid mutations, owning-revision rollback, visible error, first-send ordering/failure stop, and submitted display metadata.
9. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/search backend/tests/services/conversations backend/tests/services/jobs backend/tests/capabilities/test_tutoring_capability.py backend/tests/capabilities/test_resource_generation_capability.py -q`
   Observed: final post-lint run `152 passed, 152 warnings in 123.38s`. All warnings are the repository's existing pytest-asyncio `event_loop_policy` deprecation. Two earlier wrapper-limited attempts were terminated at 120 seconds; their pytest children survived the wrappers and competed concurrently. Those stale children were terminated, a clean exact run passed in 124.56 seconds, and the final post-edit run above passed again with one process.
10. Command: `npm --prefix frontend run type-check`
    Observed: exit 1 with 13 pre-existing diagnostics in unchanged regions; no Task 13-added line is flagged. Exact baseline locations are `components/layout/Sidebar.tsx:377,384`, `hooks/useJobQueue.ts:134`, `lib/event-handler.test.ts:72,102,103`, `lib/event-handler.ts:84,129,204,422,506,507`, and `lib/job-reducer-stage-lifecycle.test.ts:203`. The one Task 13 test-mock inference error found on the first run was corrected, after which only this unchanged set remained.
11. Command: changed-file collection followed by `E:\Anaconda3\anaconda\envs\tutor\python.exe -m ruff check <all changed/untracked Python files>`
    Observed: `All checks passed!` after safe Ruff import/style normalization and removal of one pre-existing unused assignment in the modified conversation store.
12. Command: `git diff --check`
    Observed: exit 0 with no whitespace errors (only Git's configured LF-to-CRLF checkout warnings).
13. Command: `npm --prefix frontend test -- --run components/chat/WebSearchToggle.test.tsx lib/api.test.ts lib/store.test.ts hooks/useJobQueue.test.tsx components/chat/ChatComposer.web-search.test.tsx`
    Observed after reviewer fixes: `5 files passed, 26 tests passed`. The added matrix covers failed draft retry/cleanup, explicit server rollback, double failure, success-then-failure, and the existing all-success serialization path.
14. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/capabilities/test_resource_generation_capability.py -q`
    Observed after reviewer fix: `25 passed, 25 warnings in 78.04s`; warnings remain the existing pytest-asyncio deprecation. Both grounded non-document evidence and empty unavailable-search input are covered.
15. Commands: `npm --prefix frontend run type-check`, changed-path Ruff, and `git diff --check`.
    Observed after reviewer fixes: TypeScript retains the identical 13 baseline diagnostics listed above with no new Task 13 line; Ruff reports `All checks passed!`; diff-check exits 0.
16. Command: `npm --prefix frontend test -- --run components/chat/WebSearchToggle.test.tsx components/chat/ChatComposer.web-search.test.tsx lib/api.test.ts lib/store.test.ts hooks/useJobQueue.test.tsx --maxWorkers=1`
    Observed after the final race fixes: `5 files passed, 30 tests passed`. This includes atomic create retry, no destructive compensation, delayed A failure isolation, stale A hydration while PATCH is pending, and navigation during WebSocket open.
17. Commands: split backend focused runs for `services/search + services/conversations + services/mcp`, `services/jobs`, and both affected capabilities.
    Observed: `23 passed`, `99 passed`, and `34 passed` respectively: `156 passed` total. Warnings are only the repository's existing pytest-asyncio `event_loop_policy` deprecation.
18. Command: live `SearchExecutor` probe with `TUTOR_WEB_SEARCH_ENABLED=true`, `TUTOR_WEB_SEARCH_PROVIDER=mcp`, and the user's existing `.mcp.json`.
    Observed twice: `search_used=true`, five normalized HTTP(S) sources, `degradation_code=null`, provider `MiniMax`. After the stdio lifecycle fix the second probe exited without Proactor/pipe destructor errors. No secret values were printed or copied into the worktree.
19. Commands: changed-path Ruff, `git diff --check`, and `npm --prefix frontend run type-check`.
    Observed: Ruff `All checks passed!`; diff-check exit 0; TypeScript retains the same 13 planned Task 14 diagnostics, including the pre-existing retrieval-scope narrowing error whose line shifted within `useJobQueue.ts`, with no new Task 13 diagnostic.
20. Command: `npm --prefix frontend test -- --run components/chat/WebSearchToggle.test.tsx components/chat/ChatComposer.web-search.test.tsx lib/api.test.ts lib/store.test.ts hooks/useJobQueue.test.tsx lib/event-handler.test.ts --maxWorkers=1`
    Observed after independent re-review fixes: `6 files passed, 39 tests passed`. The suite now includes pre-submit navigation continuation, delayed ACK isolation, inactive resource/terminal/malformed-event isolation, authoritative assistant persistence, and out-of-order aggregate hydration.
21. Command: `$env:PYTHONPATH='backend'; & 'E:\Anaconda3\anaconda\envs\tutor\python.exe' -m pytest backend/tests/services/mcp/test_stdio_client.py -q`
    Observed: `2 passed`; normal close reaches reader EOF without cancellation, and stalled stdin close is cancelled at its bound before process termination and cleanup continue.
22. Command: final focused frontend run including `JobTray.test.tsx` after the last fail-closed review fix.
    Observed: `7 files passed, 45 tests passed`. JobTray supplies the row's authoritative session on resubscribe, and sessionless terminal events make no reducer, active-turn, or persistence mutation.
23. Command: final single-process Task 13 backend run over search, conversations, jobs, tutoring, resource generation, and MCP lifecycle tests.
    Observed: `157 passed, 157 warnings in 126.54s`; every warning is the repository's existing pytest-asyncio `event_loop_policy` deprecation.

## Evidence matrix

- Migration/restart: focused conversation and job tests reopen the same legacy/current SQLite files; existing messages remain readable, legacy booleans default false, enabled parent/child snapshots remain true.
- Immutable snapshot: a blocked in-flight capability retains `True` after the mutable conversation lookup changes to `False`; a subsequent submission observes the new value.
- Submission coverage: normal `submit` snapshots from the server and overwrites malicious aliases; both `submit_job` and legacy `start_turn` converge on that boundary; plan confirmation has no session and therefore snapshots false; REST retry explicitly inherits the parent; durable follow-ups inherit the persisted parent row without re-reading the conversation.
- Zero-call gate: disabled conversation/runtime combinations assert both registry `get_calls == 0` and provider `calls == 0`; only both true resolves and invokes the fake provider once.
- Normalization/degradation: only `http`/`https` URLs survive; markup/control characters and excess results are stripped/bounded; placeholder, empty, malformed, failed, exception, missing-tool, and timeout outcomes expose only `WEB_SEARCH_UNAVAILABLE` without raw provider details.
- Capability degradation: tutoring and resource tests each assert exactly one `WEB_SEARCH_UNAVAILABLE` observation and a normal answer/package result after provider unavailability.
- Source persistence: resource evidence is read after closing and reopening `ResourcePackageStore`; the normalized URL survives in both package metadata and every restored resource's metadata.
- Optimistic rollback/race: mutation revision, chain, confirmed value, desired value, and pending state are keyed by session. Tests cover all-success, double-failure (`false`), success-then-failure (`true`), A/B navigation, and stale A hydration while A's PATCH is pending. Delayed A completions never alter B.
- Draft first-send: tests assert default-off state, atomic POST creation with the selected initial setting before submission, no PATCH for a new row, and user/job metadata recording true. A failed create makes zero submits/appends, restores text, shows an error, and retries with exactly one successful create and submit. An existing-row PATCH failure never issues DELETE.
- Submission handshake: ChatComposer supplies the captured session/search choice explicitly and `useJobQueue` snapshots all turn metadata before connecting; a deferred-open test switches A to B and still observes A/true in both top-level and metadata fields.
- Cross-session event delivery: after navigation, the ACK does not create an A reducer entry in B; subscription dispatch carries A's session/user; resource and malformed events are ignored by B; terminal canonical assistant content is persisted to A without mutating B. A successful pre-submit request continues A's durable user-message append and job submission rather than returning after clearing text.
- Resubscription fail-closed: both ChatComposer and JobTray pass an authoritative session to `subscribe`; an event with no context/top-level/metadata session is ignored instead of falling back to the active conversation.
- Aggregate ordering: each load owns a generation and target session; `setSessionId` invalidates older work, and stale responses return before web-search maps or visible store state change. A deferred B-then-A completion test remains on B.
- MiniMax transport lifecycle: a fake-process ordering test verifies stdin close/wait precedes process termination and that readers finish via EOF rather than cancellation; two live searches verify the actual configured MiniMax MCP provider and clean Windows shutdown.
- Non-document grounding: the actual exercise agent receives the normalized web excerpt through branch `source_content`; a matching unavailable-search test receives an empty string, preventing metadata-only/fake grounding claims.

## Commits and self-review

- Implementation commit: `06c340a` (`feat: add per-conversation web search policy`).
- Initial report commit: `5e16896` (`docs: add Task 13 verification report`).
- Reviewer frontend reliability commit: `06a02df` (`fix: preserve web search intent across draft retry`).
- Reviewer capability grounding commit: `a9714bb` (`fix: ground non-document resources with web evidence`).
- Reviewer follow-up report commit: created after this update is staged.
- Final independent re-review: approved after three rounds; no remaining Important/Critical findings. It explicitly confirmed production subscribe entry points, ACK/event/resource/terminal isolation, atomic retry, per-session mutation/hydration fencing, and bounded MCP shutdown.
- Security/privacy review: conversation settings use canonical identity and owner checks; missing sessions return 404; extra fields are forbidden. JobRunner overwrites client authorization aliases from server state. Disabled policy combinations make zero registry/provider calls. Provider exceptions and raw error data never leave the executor. Sources are bounded, stripped of HTML/control characters, restricted to HTTP(S), and contain only normalized display fields.
- Durability review: both SQLite migrations are idempotent and default legacy rows off; conversation/job rows survive close/reopen; running jobs use the persisted first-class snapshot; retry and follow-up inheritance are explicit; resource source metadata survives package-store reopen.
- Frontend review: aggregate hydration preserves a pending desired value; serialized PATCHes and revisions are isolated by session; rollback applies only to the owning latest revision against the latest confirmed server value. First-send atomically creates the row/setting, preserves retry text on failure, never compensates by deleting history, and fences delayed completions to their originating session. The WebSocket submission boundary uses the same captured session context.
- Concern: repository-wide TypeScript checking remains red only at the 13 unchanged baseline locations listed above. Task 13 focused frontend tests, backend broad tests, changed-path Ruff, and diff hygiene are green.
