# Task 9 Report — User-triggered full Manim regeneration backend

## Outcome

Implemented a durable, owner-scoped `video_repair_render` follow-up that regenerates a complete `MainScene`, validates at most two LLM candidates, and performs exactly one real Manim render. Initial rendering is now terminal after one executor invocation and never enters the legacy LLM patch loop.

The existing retry URL remains compatible. Enqueueing durably persists a repair child while leaving the failed resource completely unchanged. Once claimed, that child uses its first guarded operation to CAS-bind `repair_job_id` to itself and set `repair_status=running`, so both the pre-bind and post-bind crash boundaries are resumable.

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
- `backend/tutor/services/jobs/store.py`
- `backend/tutor/api/routers/resources.py`
- `backend/tutor/services/resource_package/schema.py`
- `backend/tests/services/manim_render/test_service.py`
- `backend/tests/services/manim_render/test_code_retry.py`
- `backend/tests/capabilities/test_video_render_fire_and_forget.py`
- `backend/tests/api/test_resources_artifact_endpoint.py`
- `backend/tests/services/jobs/test_follow_up.py`

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

## Post-review hardening follow-up

The review findings were reproduced before implementation and fixed with
adjacent regression coverage:

- Transactional resource CAS: RED `3 failed`; GREEN `3 passed`. A
  `BEGIN IMMEDIATE` compare-and-swap now checks package/resource/owner plus
  the expected source revision and repair job in the same SQLite transaction.
  All repair writers use this API, and stale children cannot mutate resources.
  The endpoint reuses only an active child for the current owner/resource and
  failed revision; repeated idempotent requests no longer resume it twice.
- Immutable publishing and terminal failures: RED `5 failed`; GREEN
  `5 passed`. Videos are published under SHA-256 content-addressed names via a
  verified temporary copy and atomic replace. Executor exceptions, missing
  render history, copy failures, missing paths, and empty URLs return bounded
  structured failures. Repair publish failures preserve the original visible
  source and render failure (`5` repair capability tests passed).
- Candidate isolation: RED `21 failed, 2 passed`; GREEN `23 passed` for the
  focused additions. Imports are limited to Manim, NumPy, and `math`; explicit
  missing Manim symbols are rejected against the installed runtime. Dynamic
  imports plus network, subprocess, environment, filesystem, and NumPy I/O
  paths are rejected before execution. Bound-method validation now permits
  known objects such as `axes.x_axis` while rejecting methods such as
  `dot.rotate` in `VGroup`.
- Legacy public repair history: RED `2 failed`; GREEN `2 passed`. Every video
  `public_resource_dump` now projects only the last 10 records, applies a field
  allowlist and diagnostic redaction/limits, and exposes only safe portable
  `manim_logs/...` artifact keys, including records written by older versions.
- Legacy SEARCH/REPLACE safety: RED `2 failed, 2 passed`; GREEN
  `19 passed` for the complete CodeRetry module. Python token spans are now
  used when tokenization succeeds, and searches overlapping string or comment
  tokens are rejected. The conservative lexical fallback remains for malformed
  code that cannot be tokenized.

Post-review expanded verification:

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/api/test_resources_artifact_endpoint.py backend/tests/api/test_video_render_retry.py backend/tests/services/resource_package/test_repair_cas.py backend/tests/services/resource_package/test_schema.py -q
```

The first run was `167 passed, 1 failed` because an old assertion still
expected the pre-hardening filename `fake.mp4`. It was updated to assert the
SHA-256 artifact key. Final result: `168 passed, 169 warnings`; warnings are
the existing Starlette/httpx and pytest-asyncio deprecations.

Ruff covered all 15 changed implementation/test files and finished with
`All checks passed!`. The installed-runtime smoke
`test_real_render_full_pipeline` passed in 16.17 seconds and produced a real
video larger than 1 KiB. `git diff --check` exited 0. The pre-existing
`frontend/next-env.d.ts` modification remains untouched and unstaged.

## Second review hardening follow-up

The second review was handled test-first. The initial combined focused run
produced `17 failed, 29 passed`, with every failure corresponding to one
missing behavior rather than a collection or environment error:

- Six indirect builtins/object-model escape cases were accepted, including
  `getattr(__builtins__, ...)`, `__builtins__[...]`, and the
  `__class__.__mro__.__subclasses__` chain.
- `import manim as m` attributes were not checked against the runtime.
- Four callable Mobject methods absent from the old finite list were accepted
  as `VGroup` arguments.
- An existing external asset was accepted and ordinary `str.replace` and
  `Mobject.replace` calls were rejected by an overbroad terminal-name rule.
- Untokenizable source fell back to lexical patching.
- Structured legacy render-failure codes/log paths were exposed on direct and
  endpoint reads.
- A resource-bind CAS miss returned an unbound pending child.

The candidate validator now uses a fail-closed Python name surface with an
explicit pure-builtin allowlist, rejects dangerous names and all dunder
object-model access, validates only `manim` module aliases against the supplied
runtime namespace, and derives callable Mobject methods from runtime classes.
It permits non-callable Mobject attributes and ordinary `replace` calls.
Repair candidates reject SVG, image, and audio asset references even when the
referenced file exists. The injected/default repair runtime now preserves a
namespace mapping so class inspection is available; unrelated `math` aliases
remain outside Manim-symbol validation.

For stale endpoint binding, the existing post-CAS `delete` operation was found
to leave a cross-process claim window. After explicit scope approval, JobStore
gained `run_if_child_active_or_delete`: it holds the jobs database
`BEGIN IMMEDIATE` transaction, verifies both active child and eligible parent,
runs the resource CAS in the established jobs→resources lock order, and deletes
the child before commit when the CAS returns false. A two-connection test proves
another store blocks and then cannot claim the deleted child; the true-CAS case
remains claimable. The parent-eligibility addition was independently RED
(`1 failed`) then GREEN (`3 passed` for the atomic group). No reverse
resources→jobs lock acquisition was found in the reviewed paths.

Public structured failures now whitelist their shape, redact and bound
`error_code`, sanitize summaries/trace tails, synchronize the top-level public
error code, and expose only validated portable `manim_logs/...` keys. Patch
application now rejects the entire patch when Python tokenization fails.

Verification evidence:

- First focused GREEN after implementation: `48 passed, 0 failed`.
- Expanded suite plus the two initial atomic race tests: `186 passed,
  0 failed`.
- Final validator plus three atomic-store tests: `48 passed, 0 failed`.
- Changed-file Ruff: `All checks passed!`.
- Real installed-Manim pipeline: `1 passed` in 15.13 seconds.
- Final combined post-lint run including all new cases: `190 passed,
  0 failed`; final real-Manim rerun: `1 passed` in 15.83 seconds.

## Third review hardening follow-up

The third review focused on namespace allowlisting and eliminating the final
pre-bind claim window. Focused RED produced `9 failed`:

- NumPy namespace escape calls (`numpy.lib.format.open_memmap`) and ndarray
  serialization (`array.dump`) were accepted.
- SVG, image, and audio callables could be assigned and invoked through simple
  or chained aliases.
- A set-only runtime namespace silently disabled runtime Mobject inspection.
- A legacy top-level `render_error_code` remained secret-bearing and unbounded.
- The two transactional insert/bind concurrency tests failed because the bound
  enqueue primitive did not exist.

NumPy calls rooted at `numpy` or an imported NumPy alias now use an explicit
full-name computation allowlist (array construction, ranges, zeros/ones,
stacking/reshape, numeric functions, linear-algebra norm, and selected safe
random functions). Every other rooted NumPy call is rejected by default;
ndarray `dump` and `tofile` remain explicitly forbidden file-capable methods.
The representative native Manim/NumPy scene passed while both new escape cases
were rejected.

Asset validation now computes assignment aliases to a fixed point, including
multi-target chained assignments, and rejects calls through aliases of
`SVGMobject`, `ImageMobject`, and `add_sound`, whether referenced directly or
through the `manim` module alias. Runtime namespaces are Mapping-only and carry
actual runtime objects; direct validation fails closed with
`INVALID_RUNTIME_NAMESPACE` for a set, injected capability callers now supply
mappings, and unavailable-Manim fallback uses an empty mapping.

The new-child retry path now uses `create_child_if_absent_with_bind`. JobStore
holds one jobs DB `BEGIN IMMEDIATE` transaction across parent lookup, child
insert/on-conflict selection, and the resource CAS callback. The child is
therefore uncommitted and unclaimable until binding succeeds; false binding
deletes it before commit and returns 409. Two independent JobStore connections
prove a claim started after insertion blocks until callback release, then
either observes the correctly bound child or no child. Existing active-child
reuse continues through the prior atomic active-child guard. Lock order remains
jobs→resources.

Finally, legacy unstructured `render_error_code` is redacted and bounded on
every public read with the same diagnostic sanitizer used for structured
failures.

Verification evidence:

- New focused GREEN: `13 passed, 0 failed`.
- Full candidate validator plus refreshed repair compatibility: `53 passed`.
- Retry API compatibility after bound-enqueue race adaptation: `18 passed`.
- Final combined prior-plus-new suite: `200 passed, 0 failed`.
- Changed-file Ruff: `All checks passed!`.
- Real installed-Manim pipeline: `1 passed` in 16.70 seconds.

## Fourth review hardening follow-up

The fourth review found two remaining validator indirections and a durability
problem in the cross-database insert/bind transaction. The focused validator
RED run produced `9 failed`: NumPy file-capable attributes could be read into
aliases, and asset constructors could be hidden in list/tuple/unpack/lambda
expressions without being called directly.

Every loaded NumPy-rooted attribute is now checked against the full-path
computation allowlist (including only the namespace prefixes needed to reach an
allowed computation). Consequently `writer = np.save`, ndarray `tofile`/`dump`
attribute reads, and `np.lib.format.open_memmap` are rejected before invocation,
while the representative NumPy computation scene remains valid. `dump` and
`tofile` loads are rejected on arbitrary receivers as well. Asset validation
now rejects every expression load of `SVGMobject`, `ImageMobject`, or
`add_sound`, including direct names, Manim-module attributes, containers,
unpacking, and lambdas; imports remain parseable until such a symbol is used.

The endpoint-side jobs-transaction/resource-CAS primitive was removed. A retry
now commits or reuses the durable pending child first and records the resource's
prior repair owner in child metadata. The claimed child performs the first
resource CAS under its claim guard, binding the resource to its own job ID and
marking it running. If the process dies before that CAS, the durable unbound
child is reclaimable; if it dies after the CAS, the same child recognizes its
existing binding and resumes. Revision changes, ownership failures, or a
different repair owner make the child fail terminally without modifying the
resource. Duplicate endpoint calls still reuse the active child and schedule
only one resume. The misleading `create_child_if_absent_with_bind`,
`enqueue_bound`, and `run_if_child_active_or_delete` APIs and their obsolete
cross-database atomicity tests were removed.

New tests cover the exact NumPy and asset bypass snippets, endpoint store order,
an unbound pre-CAS resume, a self-bound post-CAS resume, and terminal failure
without overwrite for both stale revisions and competing repair owners.

Verification evidence:

- Full candidate validator: `61 passed`.
- Focused saga/validator/API/capability/store group: `129 passed`, followed by
  `4 passed` for the explicit pre-bind, post-bind, stale-revision, and
  competing-owner recovery cases.
- Combined Task 9 and follow-up store suite: `237 passed, 0 failed`.
- Changed-file Ruff: `All checks passed!`; `git diff --check` exited 0.
- Real installed-Manim pipeline: `1 passed` in 15.40 seconds.
