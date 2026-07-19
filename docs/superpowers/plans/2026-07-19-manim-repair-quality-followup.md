# Manim Repair Quality Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve valid candidates across renderer exceptions, type their transient schema, and repair corrupt content-addressed publish destinations.

**Architecture:** Convert renderer exceptions at the capability boundary into the same sanitized `RenderFailure` path as structured render failures. Model private transient candidate state in `VideoResource`, and make `_publish` trust an existing digest path only after verifying its bytes.

**Tech Stack:** Python 3.11, Pydantic v2, asyncio, SHA-256, atomic `os.replace`, pytest, Ruff, Manim 0.20.

## Global Constraints

- Every production change follows an observed RED test.
- Transient candidate state remains private in public projections.
- Never expose raw renderer exceptions in persisted/public diagnostics.
- Do not touch or stage `frontend/next-env.d.ts`.

---

### Task 1: Renderer exception handoff

**Files:**
- Modify: `backend/tutor/services/jobs/follow_up.py`
- Test: `backend/tests/capabilities/test_video_render_fire_and_forget.py`

**Interfaces:**
- Consumes: a valid generated candidate and an exception from `render()`.
- Produces: sanitized `repair_render_failed` transient diagnostic and candidate input for the next manual repair.

- [x] Write a two-attempt test whose first renderer raises a secret-bearing exception.
- [x] Run the exact node and verify missing candidate persistence/wrong error code.
- [x] Wrap renderer exceptions into a safe log-backed `_VideoRepairError`.
- [x] Re-run and assert the next agent receives the valid candidate and sanitized render failure.

### Task 2: Typed transient video schema

**Files:**
- Modify: `backend/tutor/services/resource_package/schema.py`
- Test: `backend/tests/services/resource_package/test_schema.py`

**Interfaces:**
- Consumes: bounded persisted `repair_candidate_code` and diagnostic mapping.
- Produces: a successful `VideoResource` parse with length/shape rejection and unchanged public omission.

- [x] Add valid parse and oversize/invalid-shape tests.
- [x] Run the exact nodes and verify strict-schema rejection of valid state.
- [x] Add optional bounded typed fields to `VideoResource`.
- [x] Re-run schema/public projection tests.

### Task 3: Corrupt digest destination repair

**Files:**
- Modify: `backend/tutor/services/manim_render/service.py`
- Test: `backend/tests/services/manim_render/test_service.py`

**Interfaces:**
- Consumes: source video and existing digest-named destination.
- Produces: verified destination bytes matching the source digest via atomic replacement.

- [x] Add a test pre-seeding corrupt bytes at the expected digest path.
- [x] Run it and verify current `_publish` incorrectly reuses corrupt bytes.
- [x] Re-hash existing destinations and route mismatches through verified temp-copy/replace.
- [x] Re-run publish and service tests.

### Task 4: Verification and delivery

**Files:**
- Modify: `.superpowers/sdd/task-9-report.md`
- Generate: `.superpowers/sdd/review-a1f7fcb..<commit>.diff`

- [x] Run focused and expanded combined Task 9 suites.
- [x] Run changed-file Ruff, compileall, diff check, and real Manim smoke.
- [x] Update the Task 9 report, commit intended files only, and generate the review package.
