# TutorBot reliability follow-up: Claude Code handoff

Updated: 2026-07-19 (Asia/Shanghai)

## Resume location and safety rules

- Repository: `E:\github\TutorBot`
- Active isolated worktree: `E:\github\TutorBot\.worktrees\tutorbot-reliability`
- Branch: `codex/tutorbot-reliability`
- Work only in the isolated worktree. Do not edit the main checkout.
- Python/runtime: `E:\Anaconda3\anaconda\envs\tutor\python.exe`
- Preserve and never stage/overwrite `frontend/next-env.d.ts`. It is a pre-existing user change and is intentionally the only unrelated dirty file.
- Web search is the user's MiniMax MCP service. Do not redesign or replace that provider.
- Original Manim log supplied by the user: `C:\Users\18303\Downloads\attempt-01.log`.
- Execution method chosen by the user: Subagent-Driven; one implementer at a time, then independent read-only review.

## Source documents

- Approved design: `docs/superpowers/specs/2026-07-19-local-validation-follow-up-design.md`
- Approved plan: `docs/superpowers/plans/2026-07-19-local-validation-follow-up.md`
- SDD task briefs/reports: `.superpowers/sdd/` (ignored by Git but present in this worktree)
- Current Task 10 report: `.superpowers/sdd/task-10-report.md`
- Task 11 brief: `.superpowers/sdd/task-11-brief.md`

## Completed follow-up work

All items below were implemented test-first and independently reviewed clean unless noted otherwise.

1. Public resource projection and truncation/schema safety: `bba206a..6b1dd8f`
2. Durable terminal workflow/session/job deletion behavior: `6b1dd8f..66e11cf`
3. Mermaid/media normalization and safe media rendering: `66e11cf..25b094e`
4. Durable exercise backend, scoring, idempotency and answer secrecy: `25b094e..9dec983`
5. Frontend resource validation: `9dec983..483fd38`
6. Typed code output and lazy Matplotlib capture: `483fd38..e6c012f`
7. Exercise feedback into profile/path workflow: `e6c012f..5cfd4ff`
8. Exercise draft/answer restore and identity isolation: `5cfd4ff..a1f7fcb`
9. Manim full-code intelligent repair backend: `a1f7fcb..c5a11d8` (final independent review passed)
10. Intelligent Manim repair UI base implementation:
   - `d636bac feat: expose intelligent Manim repair`
   - `3d9e021 fix: preserve terminal Manim repair state`
   - `70d0733 fix: harden equal-revision repair ordering`
   - `9c0537e fix: reject terminalized first repair snapshots`
   - implementation and tests are complete; the final ultra-narrow read-only re-review was still running when this line was updated.

## Task 9 final behavior and evidence

Task 9 is complete and review-clean.

- Initial Manim generation renders once; no automatic LLM patch loop.
- Failed-video button creates/reuses a durable `video_repair_render` child before associating the resource.
- The claimed child sends the full failed source plus bounded, sanitized diagnostics/runtime information to the model and requests one complete `MainScene`.
- Generated source is compile/AST/StaticGuard/Manim validated; external assets/config writes and unsafe NumPy/file operations are rejected.
- Crash recovery works before resource association, after association, after saved success and after saved failure, without duplicate model/render/history work.
- Failed candidates and latest diagnostics are stored as private bounded transient fields for the next manual attempt, omitted from public projection, promoted/cleared only on success.
- Video publication is content-addressed; existing targets are rehashed and corrupt targets atomically replaced.
- Final evidence at `c5a11d8`: focused 80 passed; expanded Task 9 matrix 268 passed; real installed-Manim 1 passed; Ruff/scoped compile/diff checks passed.

## Current active work: finish Task 10 review fixes

Current Task 10 implementation HEAD: `9c0537e` (handoff-doc commits also appear between implementation commits).

The first Task 10 implementation passed:

- focused component/event tests: 52/52 after first review fix;
- full frontend: 256/256;
- `npm run type-check` passed;
- Playwright desktop terminal-Manim-failure fixture passed 1/1;
- Browser plugin was unavailable, so repository Playwright 1.60.0 was used without installing anything;
- focused ESLint exceeded 120 seconds with no diagnostics.

Independent review confirmed the `VideoViewer` local tracking fix, but found two remaining event-ordering cases in `frontend/lib/event-handler.ts`:

1. Equal-revision first repair transition is incorrectly rejected when the current failed resource has no `repair_job_id` and the incoming snapshot introduces the first repair job. Accept this causal `no job -> first pending/running repair job` transition.
2. Same-job, same-terminal (`ready` or `failed`) incremental snapshots are accepted without ordering evidence and can overwrite a newer canonical URL/history. At equal revision, treat an already-terminal same-job state as immutable (or perform only demonstrably monotonic enrichment); do not wholesale replace it.

The active implementer completed both cases and committed `70d0733`:

- RED: 3 failed / 30 passed;
- focused: 55/55 passed;
- full frontend: 259/259 passed;
- TypeScript: passed;
- `git diff --check`: passed;
- Playwright was not repeated because the final patch changes event ordering only.

A final review found that a delayed active snapshot could still be mistaken for a first repair when the current resource had no `repair_job_id` but its history already terminalized that incoming job. Commit `9c0537e` adds that history guard and regression. Final evidence: focused 56/56, full frontend 260/260, TypeScript and diff checks passed.

After commit, `git status --short` should show only:

```text
 M frontend/next-env.d.ts
```

This is the unrelated pre-existing user file.

To close Task 10:

1. Obtain/confirm a clean independent review of `c5a11d8..9c0537e`, particularly the final event-ordering/history guard. An ultra-narrow re-review was requested before this update.
2. If desired, re-run/confirm:

   ```powershell
   cd E:\github\TutorBot\.worktrees\tutorbot-reliability\frontend
   npx vitest run components/resources/VideoViewer.test.tsx lib/event-handler.test.ts
   npm test -- --run
   npm run type-check
   ```

3. Obtain an independent read-only review of the final Task 10 range `c5a11d8..HEAD`, especially equal-revision ordering, first repair acceptance, terminal immutability, polling cleanup and canonical terminal authority.
4. Do not accept Task 10 until the review is clean.

## Remaining Task 11: cross-system verification and local runbook

Task 11 has not started. Follow `.superpowers/sdd/task-11-brief.md`.

Required deliverables:

- Create `backend/tests/integration/test_local_validation_follow_up.py` covering:
  - complete exercise projection -> draft/save/submit -> durable event -> profile/path scheduling;
  - failed Manim video -> durable mocked full-regeneration child -> terminal resource/job state.
- Extend `frontend/e2e/reliability.spec.ts` for:
  - completed workflow stages remain visible;
  - normal exercise options and draft restoration;
  - Mermaid fallback;
  - NumPy-only/text-only code has no false image error;
  - intelligent video repair click-through, progress, refresh/restart recovery, success/failure terminal UI.
- Update `docs/runbooks/local-development.md` if present, otherwise `README.md`, with repeatable local test commands using the `tutor` Conda environment.
- Run real-runtime checks:
  - NumPy-only script succeeds with `artifacts=[]` and no Matplotlib error;
  - Matplotlib script emits `figure_1.png`;
  - initial broken Manim has exactly one render and terminal failure;
  - repaired native-shape Manim scene produces an MP4 and marks the resource ready.
- Run MiniMax MCP acceptance if the locally configured service is available. If unavailable, record it as environment-blocked and keep deterministic mocked tests green; do not change providers.

Suggested commands:

```powershell
cd E:\github\TutorBot\.worktrees\tutorbot-reliability
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/integration/test_local_validation_follow_up.py -q
E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q

cd frontend
npm test -- --run
npm run type-check
npm run test:e2e -- --grep "MiniMax MCP|completed workflow|exercise draft|intelligent video repair"
```

Run `npm run lint` only with a bounded timeout; earlier focused ESLint produced no diagnostics but did not exit within 120 seconds. Record this honestly if it repeats.

Expected Task 11 commit:

```text
test: cover local validation follow-up flows
```

Then perform a fresh whole-branch read-only review and final verification. Do not merge into `main`; the user explicitly wants local testing before deciding whether to merge.

## Baselines and known limitations

- Pre-follow-up baseline: backend 932 passed; frontend 190 passed; type-check clean.
- Latest Task 10 full frontend evidence before the two final event-ordering cases: 256/256 and type-check clean.
- The existing Playwright fixture verifies terminal failed-video rendering/no spinner but not yet full button-to-backend repair click-through; Task 11 must add that flow.
- Broad Python `compileall` sees an existing generated invalid Manim fixture under `backend/tutor/services/manim_render/output/.../source.py`; syntax-check changed production modules or exclude generated output.
- Existing pytest deprecation warnings are not part of this follow-up.
- Never stage `frontend/next-env.d.ts`.
