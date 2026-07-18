# Task 14 report: UTF-8, canonical frontend contracts, and safe logging

## Status

DONE_WITH_CONCERNS

## RED evidence and root causes

### Frontend contracts and test isolation

- `npm --prefix frontend run type-check` reproduced 13 errors: two local Course-shape drifts in `Sidebar`, one un-narrowed `RetrievalScope.none`, nine stream/resource/error contract mismatches, and one stale reducer matcher signature.
- New focused tests failed before production changes for five behavior gaps: `none` serialized as `none:`, a non-canonical `ResourcePackage.summary`, ignored injected conversation persistence (and therefore an accidental API/fetch path), structured error text lost when event content was empty, and unknown stream events accepted instead of failing closed.
- API job list/detail/aggregate accepted both legacy string errors and structured errors in multiple downstream shapes instead of normalizing once at the REST boundary.

### UTF-8 and logging privacy

- Importing `tutor.services.logging.redaction` initially failed with `ModuleNotFoundError`.
- Existing general redaction intentionally preserved source code, so it was not safe for ordinary logs. New tests covered nested mappings/lists/tuples, API-key/token/password variants, hidden tests, private reasoning/prompts, full source length-only markers, cycles, deep/wide inputs, unsupported objects whose `repr` contains secrets, and non-mutation.
- WebSocket exception handling returned raw exception text to the client and ordinary logs; Manim retry logged exception repr and an unmatched source prefix; MiniMax MCP logged raw stderr and malformed JSON prefixes.
- Encoding tests exercised Chinese stdout/stderr and invalid bytes at code-sandbox, Manim static-guard/executor, and MCP stderr boundaries.
- The first default full-backend collection failed before tests because pytest's prepend mode collided on duplicate basenames (`test_executor.py`, `test_policy.py`, and `test_redaction.py`).
- The first capabilities/services run exposed one existing security assertion: `resource_generation` formatted the current traceback itself before placing it in the protected operator artifact. It also reproducibly exposed an unclosed retry-test JobRunner/aiosqlite worker warning at event-loop teardown.
- Independent review then found two additional MiniMax logging gaps: startup INFO logged fully expanded MCP argv, and the first redactor version missed exact `token`, vendor-prefixed assignments such as `MINIMAX_API_KEY=...`, plus `original_code`, `current_source`, and `repair_prompt` variants. Both were reproduced with synthetic secrets before the fix.
- A second review pass found the real `MCPWebSearchTool` wrapper still logged/returned raw MCP exception/provider errors and identified project field variants (`starter_code`, `python_code`, prompt templates/content, and system messages) not covered by the first classification expansion.

## Canonical decisions and implementation

- `frontend/lib/types.ts` is the single owner of `CourseResponse`, `RetrievalScope`, `StructuredError`, `ConversationMessageInput`, resource events, job summary/detail errors, and the canonical resource/package fields.
- REST job list/detail/aggregate normalize legacy strings and canonical error objects once through `normalizeStructuredError`; UI components render `[code] message` through one formatter.
- `parseStreamEvent(unknown)` is the explicit WebSocket boundary. Unknown types fail closed, nullable fields get deterministic defaults, and Task 13's authoritative-session requirement remains enforced before UI projection or persistence.
- Event persistence accepts an injected adapter for tests and defaults to the API adapter in production; adapter-injected tests assert no fallback fetch.
- `redact_sensitive()` reconstructs bounded JSON-friendly data without mutating input or calling arbitrary `repr`/`str`. Secret/private fields become `[REDACTED]`; source fields become length-only markers; safe codes, status, durations, counts, artifact keys, summaries, public URLs, and answer text survive.
- Unified WebSocket, Manim CodeRetry, and MiniMax MCP ordinary logs now emit stable codes/types/counts through the logging redactor. Clients receive stable generic internal-failure text.
- MCP startup logs only provider, command basename, and argument count; expanded argv is never logged. Tool-refresh failures likewise retain only stable code/provider/exception type.
- The actual MiniMax web-search wrapper now uses stable failure codes/provider/tool/exception type and returns only `MCP web search unavailable`; neither caught exceptions nor provider error payloads reach ordinary logs or `ToolResult.error`.
- MiniMax stderr is decoded with UTF-8 replacement and redacted; malformed JSON logs only provider and character count.
- Code sandbox output bounding and Manim subprocess compilation explicitly use UTF-8 with deterministic replacement. Full Manim operator logs remain UTF-8 access-controlled artifacts; only the public diagnostic is sanitized. Traceback capture moved into the Manim service's protected-artifact helper so capabilities never format or expose it.
- Pytest now uses `--import-mode=importlib`, allowing the default repository command to collect all same-named test modules. The retry test explicitly shuts down JobRunner and closes JobStore, eliminating the aiosqlite worker-after-loop warning.

## GREEN verification

- Frontend focused contract suite: 7 files, 73 tests passed with no stderr/warnings.
- `npm --prefix frontend run type-check`: exit 0, zero diagnostics.
- `npm --prefix frontend test`: 27 files, 189 tests passed; no network error, duplicate-key warning, or unhandled async update output.
- Backend Task 14 focused files: 68 tests passed; the final traceback/service follow-up added 14 passing tests; retry cleanup passed alone with only the repository pytest-asyncio deprecation.
- Final MiniMax review regressions: 2 RED assertions failed on the old code, then the complete redaction + MCP lifecycle/log suite passed 10/10 with changed-path Ruff green.
- Second-pass MiniMax wrapper/real-field regressions: 3 RED assertions failed on the old code, then wrapper + redaction tests passed 12/12. Source classifications cover starter/python and common generated-code fields; prompt prefixes/system message fields are private while safe prompt/completion/total token counts remain visible.
- Final search/log regression run over search policy/executor, logging redaction, MCP stdio, and the real MiniMax wrapper: 23 passed; changed-path Ruff and diff-check passed.
- Default monolithic `pytest backend/tests -q` successfully collected after importlib mode but exceeded the tool's 15-minute command limit without returning assertions. The exact 102-file suite was then partitioned only by existing top-level directories under the same configuration:
  - `core + api + agents`: 250 passed.
  - `capabilities + services`: 626 passed after the traceback fix.
  - `e2e + integration + runtime`: 37 passed.
  - Total: 913 passed. No test was skipped or deselected by the partitioning.
- The reproducible `PytestUnhandledThreadExceptionWarning` disappeared after explicit retry-test shutdown. Remaining warnings are repository/environment deprecations from pytest-asyncio's custom `event_loop_policy` fixture plus one Starlette/httpx compatibility deprecation.
- Changed-path Ruff: `All checks passed!`.
- `git diff --check`: exit 0; only configured LF-to-CRLF checkout notices were printed.

## Security and durability review

- Ordinary logs no longer contain raw caught exceptions, MiniMax stderr credentials, malformed provider payloads, prompt internals, hidden tests, or submitted/generated source.
- Stable error codes, exception types, job/provider identifiers, lengths, status/count/duration data, artifact keys, and sanitized summaries remain useful for operations.
- Access-controlled attempt/source and Manim operator artifacts retain their existing complete diagnostic contracts; public job/resource/client projections remain sanitized.
- Chinese text survives owned UTF-8 files and decoded subprocess output; undecodable external bytes become `\ufffd` deterministically rather than raising or using the Windows locale.
- Canonical error parsing does not weaken Task 13 search/session authority: client metadata remains observational and events without an authoritative session still fail closed.

## Concerns

- The repository still emits pytest-asyncio `event_loop_policy` and Starlette/httpx deprecation warnings; they are dependency/fixture migrations outside Task 14's functional contract, not unhandled runtime failures.
- The one-command backend run exceeds the current desktop tool's 15-minute cap; complete coverage is evidenced by the three exhaustive top-level partitions (913 passing tests).

## Commits and review

- Implementation commit: pending.
- Report commit: pending.
- Independent Task 14 final review: approved after three passes with no remaining Important/Critical findings.
- Independent Task 14 initial review: rejected for expanded MCP argv logging and incomplete vendor/token/source/prompt classification; both findings were fixed with RED/GREEN coverage and submitted for final re-review.
- Independent Task 14 second review: rejected for raw MiniMax wrapper errors and remaining project-specific source/prompt fields; both findings were fixed with RED/GREEN coverage and submitted for final approval.
- Final approval explicitly confirmed both registry-exception and provider-error branches, startup/refresh logs, real source/prompt/message field coverage, safe token counts, recursion bounds, and preservation of stable operational fields.
