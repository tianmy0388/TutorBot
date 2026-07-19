# Manim Repair Review Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the final Manim candidate-validator indirections and make durable repair outcomes and failed candidates safely resumable across process crashes and manual attempts.

**Architecture:** Candidate validation remains a deterministic AST pass, extended with parent-aware use rules for privileged NumPy/config/bound-method objects. Repair execution remains a claimed-child saga: it recognizes already-committed terminal resource outcomes before binding/running, stores bounded transient candidate diagnostics on failure, and clears them only after success.

**Tech Stack:** Python 3.11, `ast`, FastAPI service models, SQLAlchemy/SQLite resource CAS, pytest/pytest-asyncio, Ruff, Manim 0.20.

## Global Constraints

- Use strict TDD: every production change follows an observed behavior-level RED failure.
- Preserve existing safe NumPy computation, config scalar reads, and ordinary Mobject method calls.
- Never overwrite canonical `manim_code` or `source_revision` on repair failure.
- Do not touch or stage `frontend/next-env.d.ts`.
- Run expanded focused and combined Task 9 tests, Ruff, `git diff --check`, and the real Manim pipeline before committing.

---

### Task 1: Parent-aware candidate validation

**Files:**
- Modify: `backend/tutor/services/manim_render/candidate_validation.py`
- Test: `backend/tests/services/manim_render/test_candidate_validation.py`

**Interfaces:**
- Consumes: parsed Python `ast.AST`, import aliases, runtime Manim namespace.
- Produces: `CandidateValidationIssue` codes for unsafe NumPy namespace capture, config mutation/capture, and bound-method capture.

- [x] **Step 1: Write failing NumPy namespace-use tests**

Add parameterized scenes for `n=np; n.save(...)`, `n=np; n.ctypeslib.load_library(...)`, `[np]`, and `fn(np)`, plus safe `np.array`, `np.random.random`, `np.random.default_rng`, and numeric constant reads.

- [x] **Step 2: Run the focused tests and verify RED**

Run `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render/test_candidate_validation.py -k "numpy_namespace" -q`; expect unsafe captures to be accepted before the fix.

- [x] **Step 3: Write failing config-surface tests**

Add exact unsafe cases for `config['media_dir'] = ...`, `config.media_dir = ...`, `cfg = config; cfg[...]`, config subscript/method access, and safe scalar reads of `frame_width`/`frame_height`.

- [x] **Step 4: Write failing bound-method capture tests**

Add `method=dot.rotate; VGroup(method)` and `VGroup(*[dot.rotate])`, retaining a passing `dot.rotate(...)` scene.

- [x] **Step 5: Implement parent-aware AST rules**

Build a parent map and reject NumPy module aliases unless a Name is the direct base of an allowed Attribute chain. Permit config only as the direct base of a loaded scalar allowlisted Attribute and reject config stores, deletes, subscripts, calls, or capture. Reject loaded runtime Mobject method attributes unless the Attribute is the direct callable of an `ast.Call`.

- [x] **Step 6: Run the entire candidate validator suite**

Run `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render/test_candidate_validation.py -q`; expect all tests to pass.

### Task 2: Durable post-outcome saga recovery

**Files:**
- Modify: `backend/tutor/services/jobs/follow_up.py`
- Test: `backend/tests/capabilities/test_video_render_fire_and_forget.py`

**Interfaces:**
- Consumes: persisted resource payload, claimed child `job_id`, `failed_revision`.
- Produces: a success `CapabilityResult`, or a deterministic failure exception, without invoking the repair agent/renderer when that same child already committed its outcome.

- [x] **Step 1: Write success-after-commit crash test**

Seed a resource with `repair_job_id == child`, `source_revision == failed_revision + 1`, ready status and URL/artifact; resume the durable running/pending child and assert succeeded terminal status with zero agent/render calls.

- [x] **Step 2: Verify the success recovery test is RED**

Run its exact pytest node; expect current code to reject the incremented revision.

- [x] **Step 3: Write failure-after-commit crash test**

Seed a resource with the child-owned failed repair state and one matching failed history record; resume and assert failed terminal status, zero agent/render calls, and no duplicate history.

- [x] **Step 4: Verify the failure recovery test is RED**

Run its exact pytest node; expect current code to invoke regeneration and/or append duplicate history.

- [x] **Step 5: Implement outcome recognition and idempotent history**

Recognize child-owned ready revision `failed_revision + 1` with publishable output before the normal failed-revision bind. Recognize a child-owned durable failed history record before regeneration and re-raise the durable failure. Make `_append_repair_history` replace/deduplicate the same `(job_id, failed_revision, status)` outcome.

- [x] **Step 6: Run repair capability tests**

Run the focused post-outcome nodes and then the complete `test_video_render_fire_and_forget.py` file.

### Task 3: Latest failed candidate and diagnostic handoff

**Files:**
- Modify: `backend/tutor/services/jobs/follow_up.py`
- Modify if public projection requires explicit bounds: `backend/tutor/services/resource_package/schema.py`
- Test: `backend/tests/capabilities/test_video_render_fire_and_forget.py`
- Test if projection changes: `backend/tests/services/resource_package/test_schema.py`

**Interfaces:**
- Consumes: generated candidate string and sanitized `RenderFailure` from validation/render.
- Produces: private bounded `repair_candidate_code` and `repair_candidate_failure` transient payload fields; the next repair uses them as agent inputs and success removes them.

- [x] **Step 1: Write two-manual-attempt RED test**

Make attempt one generate a candidate that fails deterministic validation or render. Start attempt two and assert its first agent call receives that candidate and the latest sanitized diagnostic while canonical `manim_code`/revision remain unchanged.

- [x] **Step 2: Write generation-before-candidate retention RED test**

Seed transient candidate state, force provider generation failure before a new candidate, and assert the prior transient state remains.

- [x] **Step 3: Write success cleanup RED test**

Seed transient state, complete repair successfully, and assert candidate transient fields are removed when the candidate is promoted.

- [x] **Step 4: Implement bounded transient persistence**

Track the latest candidate in the run, persist it only for deterministic validation/render/publish failures, sanitize and bound its diagnostic fields, and preserve previous transient state for provider failure before a candidate. Select transient inputs before canonical source/failure on the next job and clear them in the success CAS.

- [x] **Step 5: Run focused and compatibility tests**

Run the new nodes, full repair capability file, and schema/public-projection tests; confirm secrets/path details remain bounded and redacted.

### Task 4: Final verification and delivery

**Files:**
- Modify: `.superpowers/sdd/task-9-report.md`
- Generate: `.superpowers/sdd/review-a1f7fcb..<commit>.diff`

**Interfaces:**
- Consumes: all completed changes and test evidence.
- Produces: one review-ready commit and refreshed diff package.

- [x] **Step 1: Run expanded focused and combined suites**

Run all Task 9 agent, Manim, capability, API, resource CAS/schema, and JobStore follow-up tests.

- [x] **Step 2: Run static and real-runtime verification**

Run changed-file Ruff, `git diff --check`, and `test_real_render_full_pipeline` against installed Manim.

- [x] **Step 3: Update report and commit**

Append RED/GREEN evidence and architecture changes to `.superpowers/sdd/task-9-report.md`, stage only intended files, and commit with a scoped message.

- [x] **Step 4: Generate review package**

Generate `.superpowers/sdd/review-a1f7fcb..<commit>.diff`, verify only pre-existing `frontend/next-env.d.ts` remains unstaged, and report the commit/test evidence.
