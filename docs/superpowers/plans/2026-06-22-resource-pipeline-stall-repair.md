# Resource Pipeline Stall Repair Plan

> Scope: diagnose and repair the exact failures reproduced by the prompt “什么是反向传播算法？”: jobs stuck at `persistence`, placeholder Manim output, and false `matplotlib` dependency failures.

## Verified diagnosis

1. Job `b274dd137702468e85f1742a01b81a5b` persisted package `9807bdc6230549b78f0f277ec6ab60cd`, emitted `stage_end(persistence)`, `result`, and `done`, but never persisted `job_terminal`; its database status remains `running`. Persistence itself succeeded.
2. `JobRunner` can cancel its watchdog after `done` closes the bus, then await it under `suppress(Exception)`. On Python 3.11, `asyncio.CancelledError` is not covered by `Exception`, so terminal-event persistence and status update can be skipped.
3. `ManimVideoAgent` deliberately replaces missing/invalid LLM code with `_fallback_manim_code()`. The fallback contains `Fallback scene` and `动画生成中`, but the quality gate still records it as passed. The raw code-generation response and parse failure are not persisted, so the upstream LLM failure cannot currently be distinguished from a parser failure.
4. The new background render path creates an unmanaged `ThreadPoolExecutor`, reuses a parent-loop `StreamBus` from another loop, and calls async `ManimRenderService.render()` without `await`. This path is unsafe even after the terminal-state race is fixed.
5. The failed code resource does not contain the new runner fields (`execution_python`, `dependency_versions`, `error_code`, `artifacts`). Therefore the backend that produced it was not executing the current `CodeSandboxAgent` implementation. The configured Tutor interpreter is `E:\Anaconda3\anaconda\envs\tutor\python.exe`, where matplotlib is installed; the running service must be treated as stale or launched with a different interpreter until runtime evidence proves otherwise.

## Repair order

### Task 1 — Make job finalization race-safe (P0)

**Files**

- Modify: `backend/tutor/services/jobs/runner.py`
- Test: `backend/tests/services/jobs/test_runner_contract.py`
- Test: `backend/tests/services/jobs/test_terminal_idempotency.py`

**Changes**

1. Add a regression test whose capability emits `result` + `done` and returns immediately. Assert exactly one `job_terminal`, terminal database status, and non-null `finished_at`.
2. Await/cancel the watchdog with explicit suppression of `asyncio.CancelledError`.
3. Refactor finalization into a shielded/idempotent method so cancellation of the stream consumer cannot skip terminal persistence.
4. Preserve ordering: persist terminal event, persist terminal status/result, then broadcast terminal event.
5. Add an invariant log when a job has `done` but lacks `job_terminal`.

**Acceptance**

- 100 repeated fast-completion jobs all leave `running`.
- Reloading the conversation shows `succeeded`, `partial`, `failed`, or `cancelled`, never the stale stage `persistence`.

### Task 2 — Replace the unsafe video background thread with a managed render job (P0)

**Files**

- Modify: `backend/tutor/capabilities/resource_generation.py`
- Modify: `backend/tutor/services/manim_render/service.py`
- Modify/create: persisted child-job integration under `backend/tutor/services/jobs/`
- Test: `backend/tests/capabilities/test_resource_generation_capability.py`
- Test: `backend/tests/services/manim_render/test_code_retry.py`

**Changes**

1. Delete the local `ThreadPoolExecutor` fire-and-forget block.
2. Persist the parent resource package first, finish the parent chat job, and enqueue one durable child render job per video resource.
3. In the child job, call `await manim_service.render(...)` on the owning event loop. Never pass the parent `StreamBus` into a new thread/event loop.
4. Persist `pending -> rendering -> ready|failed`, attempts, stderr summary, renderer executable, output path, timestamps, and child job ID on the video resource.
5. Make render retries bounded; failure must terminate the child job and must not keep the parent conversation running.

**Acceptance**

- Parent answer reaches terminal state before long video rendering completes.
- A render failure is visible on the resource card and creates no permanently running job.
- Restarting the backend can resume or mark interrupted render jobs deterministically.

### Task 3 — Make Manim generation fail closed and observable (P0)

**Files**

- Modify: `backend/tutor/agents/resource/manim_video.py`
- Modify: resource quality-review agent/rules used by video generation
- Test: `backend/tests/agents/resource/test_resource_agents.py`

**Changes**

1. Replace free-form JSON parsing with a validated schema containing storyboard, `scene_class`, and complete Manim CE code.
2. Record safe diagnostics: response length/hash, parse status, schema errors, model/provider, retry count, and a bounded redacted response excerpt.
3. Retry code generation once with the validation error and Manim CE constraints.
4. Reject placeholder markers (`Fallback scene`, `动画生成中`, title-only scenes) in the quality gate.
5. Require concept-specific visual structure for backpropagation: network layers/edges, forward activations, loss, backward gradients, parameter update, and multiple meaningful animations.
6. Do not publish fallback code as a passed resource. Persist a typed failed artifact (`VIDEO_CODEGEN_FAILED`) with a retry action instead.
7. Syntax-check/import-check generated code before enqueueing render; rendering remains the final validation.

**Acceptance**

- The test prompt never produces or passes the current placeholder.
- Invalid LLM output yields an explicit failed resource with a diagnosable reason.
- Valid code renders locally with stable Manim CE and produces a playable artifact.

### Task 4 — Pin code execution to Tutor and expose runtime provenance (P0)

**Files**

- Modify: `backend/tutor/agents/resource/code_sandbox.py`
- Modify: backend startup script(s)
- Modify: settings/health endpoint
- Test: `backend/tests/agents/resource/test_resource_agents.py`

**Changes**

1. Fully stop stale backend processes before retesting; start via `E:\Anaconda3\anaconda\envs\tutor\python.exe -m tutor.api.run_server` (or the project’s equivalent module).
2. At startup, log and expose `sys.executable`, configured `execution_python`, Python version, working directory, and versions/locations of numpy, matplotlib, and manim.
3. Before every code run, validate the configured interpreter exists and run the dependency probe through that exact executable.
4. Rewrite `_probe_dependency_versions()` as a valid multiline probe and persist its stdout/stderr; do not silently return `{}` on syntax/probe failure.
5. Persist `execution_python`, dependency versions, exit code, duration, error code, and artifacts on every result, including failures.
6. Classify only a real `ModuleNotFoundError` for a probed missing module as `DEPENDENCY_MISSING`; keep bad imports and user-code errors as `CODE_RUNTIME_ERROR`.
7. For plotting examples, require/save `output.png` under the sandbox artifact directory; `plt.show()` alone is not a render artifact under the Agg backend.

**Acceptance**

- The UI shows the exact interpreter and matplotlib version used for the run.
- The backpropagation example executes with Tutor’s matplotlib and returns the saved plot.
- Starting the backend from a non-Tutor interpreter fails fast with an actionable health error instead of producing misleading resource output.

### Task 5 — Make the frontend display terminal truth, not the last stage (P1)

**Files**

- Modify: `frontend/lib/job-reducer.ts`
- Modify: chat progress components consuming `ClientJob`
- Test: `frontend/lib/job-reducer.test.ts`
- Test: `frontend/components/chat/ChatMessages.test.tsx`

**Changes**

1. Remove duplicate `error` declarations/assignments in `ClientJob` and submit handling.
2. Rebuild stage/thinking/text buffers from replayed snapshot events after reload.
3. On `job_terminal`, clear active-stage loading state and display the terminal contract.
4. Treat legacy `done` only as stream closure; use `job_terminal` as the authoritative result.
5. Show video rendering as a separate resource status, not as an active chat-generation stage.

**Acceptance**

- Refreshing during or after a job reconstructs the same progress/result.
- A terminal parent job never continues to show “阶段：persistence”.

## Verification sequence

1. Run focused backend tests for job runner, resource capability, Manim generation/render, and code sandbox.
2. Run frontend reducer and chat component tests plus type-check.
3. Restart all services from the pinned Tutor interpreter and verify the health endpoint.
4. Submit “什么是反向传播算法？” with resource generation enabled.
5. Verify: parent job terminal event/status; persisted answer after reload; non-placeholder Manim code; child render status transition; playable output or typed failure; code execution provenance; matplotlib plot artifact.
6. Restart backend/frontend and verify the conversation, package, resource status, and runtime evidence remain available.

## Do not accept as fixes

- Increasing the frontend polling timeout.
- Marking `done` as success only in React state while the database remains `running`.
- Keeping fallback Manim code and merely changing its subtitle.
- Installing matplotlib into another environment without proving which executable ran the code.
- Launching unmanaged render threads or swallowing render exceptions.
