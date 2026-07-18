# Local Validation Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make completed workflows, generated resources, exercise work, and failed Manim repairs durable and correct across live streaming, navigation, refresh, and backend restart.

**Architecture:** Keep the existing capability/Agent topology and add deterministic contracts at each boundary: schema-aware public resource projection, stable workflow snapshots, typed code output, durable exercise responses, and a user-triggered full Manim regeneration child job. Implement independent backend and frontend slices in parallel, then converge through integration and real-runtime tests.

**Tech Stack:** Python 3.11/FastAPI/Pydantic/SQLAlchemy/SQLite/pytest, Next.js 16/React 19/Zustand/Vitest/Playwright, Mermaid 11.14, Matplotlib Agg, Manim CE in the local `tutor` conda environment.

## Global Constraints

- Work only in `E:\github\TutorBot\.worktrees\tutorbot-reliability` on branch `codex/tutorbot-reliability`; do not edit `main`.
- Do not stage or overwrite the pre-existing `frontend/next-env.d.ts` worktree change.
- Run backend tests with `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest` and runtime smoke tests with the same interpreter.
- Preserve MiniMax MCP as the web-search provider; no search-provider redesign is in scope.
- Unsubmitted exercise answers are durable drafts and never publish learning events.
- Only explicit submissions publish idempotent `EXERCISE_SCORED` evidence.
- A failed initial Manim render is immediately terminal; automatic LLM SEARCH/REPLACE retries are not used.
- The video repair button sends full source plus bounded sanitised diagnostics to the LLM and requests a complete replacement program.
- Never expose secrets, host paths, hidden code tests, or unbounded tracebacks through public events or resource payloads.
- Every task follows RED → GREEN → focused regression → commit, and each commit stages only that task's files.

---

### Task 1: Schema-aware public resource projection

**Files:**
- Create: `backend/tutor/services/resource_package/public_projection.py`
- Modify: `backend/tutor/services/jobs/runner.py`
- Test: `backend/tests/services/jobs/test_resource_public_projection.py`

**Interfaces:**
- Produces: `project_public_event(event: Mapping[str, Any]) -> dict[str, Any]`
- Produces: `project_public_payload(payload: Mapping[str, Any]) -> dict[str, Any]`
- Preserves: validated `Resource` and `ResourcePackage` nesting while applying credential scrubbing and size bounds.

- [ ] **Step 1: Write the failing resource-event test**

```python
def test_public_resource_event_preserves_nested_exercise_options():
    event = exercise_resource_event(
        options=[{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    )
    projected = project_public_event(event)
    options = projected["metadata"]["resource"]["format_specific"]["questions"][0]["options"]
    assert options == [{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    assert "[TRUNCATED]" not in json.dumps(projected, ensure_ascii=False)
```

Add a sibling assertion that `api_key`, bearer tokens, and credential-shaped metadata are still `[REDACTED]`.

- [ ] **Step 2: Run the test and confirm the current depth-8 truncation**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/jobs/test_resource_public_projection.py -q`

Expected: FAIL because option `label`/`text` become `[TRUNCATED]`.

- [ ] **Step 3: Implement the schema-aware projection**

```python
def project_public_event(event: Mapping[str, Any]) -> dict[str, Any]:
    detached = copy.deepcopy(dict(event))
    metadata = detached.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("resource"), dict):
        resource = Resource.model_validate(metadata["resource"])
        metadata["resource"] = _scrub_known_json(resource.model_dump(mode="json"))
    return _scrub_known_json(detached)
```

Use explicit string/item/total-node bounds in `_scrub_known_json`; scrub sensitive keys at every depth without a shallow traversal cutoff. Route `resource` events and terminal capability payloads through the new functions in `runner.py`; leave generic log redaction unchanged.

- [ ] **Step 4: Run public-projection and existing redaction tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/jobs/test_resource_public_projection.py backend/tests/core/test_redaction.py backend/tests/capabilities/test_capability_exception_source_redaction.py -q`

Expected: PASS, with complete A/B option text and retained secret redaction.

- [ ] **Step 5: Commit**

```powershell
git add backend/tutor/services/resource_package/public_projection.py backend/tutor/services/jobs/runner.py backend/tests/services/jobs/test_resource_public_projection.py
git commit -m "fix: preserve public resource structure"
```

### Task 2: Frontend resource validation and option stability

**Files:**
- Create: `frontend/lib/resource-validation.ts`
- Create: `frontend/lib/resource-validation.test.ts`
- Modify: `frontend/lib/event-handler.ts`
- Modify: `frontend/components/resources/ExerciseViewer.tsx`
- Modify: `frontend/lib/event-handler.test.ts`

**Interfaces:**
- Consumes: complete resource/package projections from Task 1.
- Produces: `isUsableStreamedResource(value: unknown): value is Resource`
- Produces: `isUsableResourcePackage(value: unknown): value is ResourcePackage`

- [ ] **Step 1: Write failing validation and dispatch tests**

```typescript
it("rejects redacted or string exercise options", () => {
  expect(isUsableStreamedResource(exerciseWithOptions(["[TRUNCATED]"]))).toBe(false);
  expect(isUsableStreamedResource(exerciseWithOptions([{ label: "", text: "" }]))).toBe(false);
});

it("does not replace a canonical exercise with a truncated streamed copy", () => {
  seedCanonicalPackage();
  dispatchStreamEvent(truncatedResourceEvent(), context);
  expect(currentOptions()).toEqual([{ label: "A", text: "完整选项" }]);
});
```

Also render duplicate/empty options under a `console.error` spy and assert no React duplicate-key warning.

- [ ] **Step 2: Run the focused frontend tests**

Run from `frontend`: `npm test -- --run lib/resource-validation.test.ts lib/event-handler.test.ts components/resources/ExerciseViewer.test.tsx`

Expected: FAIL because streamed payloads are accepted and option keys use only `label`.

- [ ] **Step 3: Implement validation, recovery, and composite keys**

```typescript
export function hasUsableExerciseOptions(resource: Resource): boolean {
  const questions = resource.format_specific?.questions;
  return !Array.isArray(questions) || questions.every((question) =>
    !Array.isArray(question?.options) || question.options.every((option: unknown) =>
      isRecord(option) && clean(option.label) !== "" && clean(option.text) !== "" &&
      option.label !== "[TRUNCATED]" && option.text !== "[TRUNCATED]"
    )
  );
}
```

Reject invalid streamed/result resources before `setLatestPackage`. If a durable package id is present, schedule one `getResourcePackageDetail` recovery using the authoritative user id. Change option keys to `${q.id}:${opt.label || "option"}:${index}` and derive fallback display labels only for legacy valid text objects.

- [ ] **Step 4: Run focused tests and type checking**

Run from `frontend`: `npm test -- --run lib/resource-validation.test.ts lib/event-handler.test.ts components/resources/ExerciseViewer.test.tsx && npm run type-check`

Expected: PASS and no duplicate-key console output.

- [ ] **Step 5: Commit**

```powershell
git add frontend/lib/resource-validation.ts frontend/lib/resource-validation.test.ts frontend/lib/event-handler.ts frontend/lib/event-handler.test.ts frontend/components/resources/ExerciseViewer.tsx frontend/components/resources/ExerciseViewer.test.tsx
git commit -m "fix: reject truncated streamed exercises"
```

### Task 3: Durable completed workflows, draft sessions, and idempotent job deletion

**Files:**
- Create: `frontend/lib/workflow-snapshot.ts`
- Create: `frontend/lib/workflow-snapshot.test.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/job-reducer.ts`
- Modify: `frontend/lib/store.ts`
- Modify: `frontend/lib/event-handler.ts`
- Modify: `frontend/components/chat/ChatMessages.tsx`
- Modify: `frontend/hooks/useJobQueue.ts`
- Modify: `frontend/app/page.tsx`
- Test: `frontend/lib/job-reducer.test.ts`
- Test: `frontend/lib/job-reducer-stage-lifecycle.test.ts`
- Test: `frontend/components/chat/ChatMessages.test.tsx`
- Test: `frontend/hooks/useJobQueue.test.tsx`
- Test: `frontend/app/page-terminal-state.test.tsx`

**Interfaces:**
- Produces: `WorkflowSnapshot` and `buildWorkflowSnapshot(job, terminalStatus)`.
- Produces: stable message id `workflow:${job_id}` and metadata kind `workflow_timeline`.
- Produces store actions: `upsertMessage(message)` and `removeJob(jobId)`.
- Produces session origin: `"none" | "draft" | "restored" | "server"`.

- [ ] **Step 1: Write failing reducer/UI tests**

```typescript
it("keeps completed stages after terminal without keeping the spinner", () => {
  const terminal = reduceStageSequence([start("intent"), end("intent"), terminalSuccess()]);
  expect(terminal.messages.find((m) => m.id === "workflow:job-1")?.metadata?.workflow)
    .toMatchObject({ stages: [{ name: "intent", status: "completed" }] });
  renderStore(terminal);
  expect(screen.queryByText("正在调用 Agent…")).not.toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
});
```

Add tests for a failed job with an open stage (`incomplete`), duplicate terminal replay (one timeline), a draft session that performs no aggregate GET, and delete 404 that removes both queue and Zustand rows.

- [ ] **Step 2: Run the focused tests**

Run from `frontend`: `npm test -- --run lib/workflow-snapshot.test.ts lib/job-reducer.test.ts lib/job-reducer-stage-lifecycle.test.ts components/chat/ChatMessages.test.tsx hooks/useJobQueue.test.tsx app/page-terminal-state.test.tsx`

Expected: FAIL because terminal stages unmount, new sessions are treated as restorable, and deleted jobs remain in Zustand.

- [ ] **Step 3: Implement snapshot derivation and stable messages**

```typescript
export function workflowMessage(job: ClientJob, status: JobTerminalStatus): ChatMessage {
  return {
    id: `workflow:${job.job_id}`,
    role: "assistant",
    content: "",
    timestamp: job.finished_at ?? Date.now(),
    metadata: { kind: "workflow_timeline", job_id: job.job_id, workflow: buildWorkflowSnapshot(job, status) },
  };
}
```

Build the snapshot before clearing `open_stages`, upsert it locally in the reducer, persist exactly that stable message in `event-handler.ts`, and render it with a completed/failed `StageRow` card. Do not make terminal jobs live again.

- [ ] **Step 4: Implement session/job convergence**

Set `sessionOrigin="draft"` only when `hydrateSessionId` mints an id and `"restored"` only when localStorage supplied it. Gate mount aggregate loading on `restored`; convert an aggregate 404 to draft. Add `removeJob` to delete `jobsById[jobId]` and filter `jobOrder`; invoke it after DELETE success or 404.

- [ ] **Step 5: Run tests and type checking**

Run from `frontend`: `npm test -- --run lib/workflow-snapshot.test.ts lib/job-reducer.test.ts lib/job-reducer-stage-lifecycle.test.ts components/chat/ChatMessages.test.tsx hooks/useJobQueue.test.tsx app/page-terminal-state.test.tsx && npm run type-check`

Expected: PASS; completed stages remain, spinner stops, and no noisy draft/delete 404 remains.

- [ ] **Step 6: Commit**

```powershell
git add frontend/lib/workflow-snapshot.ts frontend/lib/workflow-snapshot.test.ts frontend/lib/types.ts frontend/lib/job-reducer.ts frontend/lib/store.ts frontend/lib/event-handler.ts frontend/components/chat/ChatMessages.tsx frontend/components/chat/ChatMessages.test.tsx frontend/hooks/useJobQueue.ts frontend/hooks/useJobQueue.test.tsx frontend/app/page.tsx frontend/app/page-terminal-state.test.tsx frontend/lib/job-reducer.test.ts frontend/lib/job-reducer-stage-lifecycle.test.ts
git commit -m "fix: retain terminal workflow feedback"
```

### Task 4: Valid Mermaid output and owned Markdown media

**Files:**
- Create: `backend/tutor/services/resource_package/markdown_media.py`
- Modify: `backend/tutor/agents/resource/multimedia.py`
- Modify: `backend/tutor/prompts/resource/zh/multimedia.yaml`
- Modify: `backend/tutor/services/resource_package/schema.py`
- Modify: `backend/tutor/capabilities/resource_generation.py`
- Modify: `backend/tests/agents/resource/test_resource_agents.py`
- Create: `backend/tests/services/resource_package/test_markdown_media.py`
- Modify: `frontend/components/resources/MindMapViewer.tsx`
- Create: `frontend/components/resources/MindMapViewer.test.tsx`
- Modify: `frontend/components/resources/DocumentViewer.tsx`
- Create: `frontend/components/resources/DocumentViewer.test.tsx`

**Interfaces:**
- Produces: `MindMapOutlineItem(depth: int, label: str)` in `MindMapResource.outline`.
- Produces: `normalise_mindmap_dsl(dsl: str) -> tuple[str, list[MindMapOutlineItem]]`.
- Produces: `replace_unowned_markdown_images(markdown: str, artifact_names: set[str]) -> str`.

- [ ] **Step 1: Write failing Mermaid and Markdown-media tests**

```python
def test_mindmap_normaliser_rewrites_bare_quoted_siblings():
    fixed, outline = normalise_mindmap_dsl(REPORTED_DSL)
    assert 'node_4["激活函数 a=σ(z)"]' in fixed
    assert not re.search(r'^\s*"', fixed, re.MULTILINE)
    assert [item.label for item in outline][-2:] == ["激活函数 a=σ(z)", "计算损失 C"]

def test_unowned_relative_image_becomes_visible_placeholder():
    assert "图片未提供" in replace_unowned_markdown_images("![Dyna](dyna_diagram.png)", set())
```

Frontend tests mock `mermaid.render` rejection, assert the outline fallback, rerender valid DSL, and assert the old error is cleared. DocumentViewer must not render an `<img src="dyna_diagram.png">` for a legacy unowned reference.

- [ ] **Step 2: Run focused backend/frontend tests**

Run backend: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_resource_agents.py backend/tests/services/resource_package/test_markdown_media.py -q`

Run from `frontend`: `npm test -- --run components/resources/MindMapViewer.test.tsx components/resources/DocumentViewer.test.tsx`

Expected: FAIL on the reported quoted-node DSL and unowned relative image.

- [ ] **Step 3: Implement normalisation, outline storage, and assembly sanitisation**

Use deterministic ids based on source line number, escape `\`/`"`, preserve legal `root((...))` and `id["..."]`, and create outline entries from indentation. Update the prompt to require shaped nodes. Before package persistence, replace relative Markdown images unless the basename matches an owned artifact.

- [ ] **Step 4: Implement viewer fallback and guarded image rendering**

Clear Mermaid error before each render and after success. On failure render `format_specific.outline`; show only a concise public error. Provide a ReactMarkdown `img` component that renders a placeholder for unresolved relative sources and lets canonical artifact/http URLs through.

- [ ] **Step 5: Run focused tests plus a real Mermaid parse check**

Run the commands from Step 2, then from `frontend`: `npm run type-check`.

Expected: PASS; the reported DSL parses under Mermaid 11.14 and no `/dyna_diagram.png` request is produced.

- [ ] **Step 6: Commit**

```powershell
git add backend/tutor/services/resource_package/markdown_media.py backend/tutor/agents/resource/multimedia.py backend/tutor/prompts/resource/zh/multimedia.yaml backend/tutor/services/resource_package/schema.py backend/tutor/capabilities/resource_generation.py backend/tests/agents/resource/test_resource_agents.py backend/tests/services/resource_package/test_markdown_media.py frontend/components/resources/MindMapViewer.tsx frontend/components/resources/MindMapViewer.test.tsx frontend/components/resources/DocumentViewer.tsx frontend/components/resources/DocumentViewer.test.tsx
git commit -m "fix: validate generated diagrams and media"
```

### Task 5: Typed code output and lazy Matplotlib capture

**Files:**
- Modify: `backend/tutor/services/resource_package/schema.py`
- Modify: `backend/tutor/prompts/resource/zh/code_sandbox.yaml`
- Modify: `backend/tutor/agents/resource/code_sandbox.py`
- Modify: `backend/tests/agents/resource/test_code_sandbox_artifacts.py`
- Modify: `backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py`
- Modify: `backend/tests/agents/resource/test_resource_agents.py`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/components/resources/CodeViewer.tsx`
- Modify: `frontend/components/resources/CodeViewer.test.tsx`

**Interfaces:**
- Produces: `CodeResource.output_kind: Literal["text", "figure"]`.
- Produces: `_code_uses_matplotlib(code: str) -> bool` using AST.
- Changes: `_wrap_user_code(code, scratch, *, capture_matplotlib: bool) -> str`.

- [ ] **Step 1: Write failing text/figure contract tests**

```python
def test_text_only_code_does_not_import_matplotlib_or_emit_cache_noise(tmp_path, settings):
    status, stdout, stderr, error_code, deps, artifacts, duration = _safe_run_python(
        "print('ok')", interpreter=sys.executable, timeout=10, settings=settings
    )
    assert status == "success"
    assert stderr == ""
    assert artifacts == []

def test_figure_contract_without_artifact_is_typed_failure():
    resource = run_generated_code("print('no plot')", output_kind="figure")
    assert resource.format_specific["error_code"] == "FIGURE_EXPECTED_BUT_NOT_PRODUCED"
```

Retain a real plot test that produces `figure_1.png`, and a warning test that filters only the exact font-cache/Agg line while preserving `warnings.warn("educational")`.

- [ ] **Step 2: Run focused code-sandbox tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_code_sandbox_artifacts.py backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py backend/tests/agents/resource/test_resource_agents.py -q`

Expected: FAIL because every run imports pyplot and the contract lacks `output_kind`.

- [ ] **Step 3: Implement AST dependency detection and conditional wrappers**

```python
def _code_uses_matplotlib(code: str) -> bool:
    tree = ast.parse(code)
    return any(
        isinstance(node, (ast.Import, ast.ImportFrom)) and import_mentions(node, "matplotlib")
        for node in ast.walk(tree)
    )
```

Generate a minimal `exec(compile(...))` wrapper for text code. Install pyplot capture only when Matplotlib is imported. Probe only actual imports. Add `output_kind` to the prompt/schema, enforce figure artifacts, and filter only exact runner-owned startup diagnostics.

- [ ] **Step 4: Update CodeViewer semantics and tests**

Render `failed`/`timeout` diagnostics in red, `success` plus remaining stderr as an amber warning, text plus no artifacts as normal, and `FIGURE_EXPECTED_BUT_NOT_PRODUCED` as an explicit image-generation failure.

- [ ] **Step 5: Run backend/frontend tests and type checking**

Run backend command from Step 2.

Run from `frontend`: `npm test -- --run components/resources/CodeViewer.test.tsx && npm run type-check`

Expected: PASS; the reported NumPy-only XOR example has no Matplotlib stderr and correctly shows text-only output.

- [ ] **Step 6: Commit**

```powershell
git add backend/tutor/services/resource_package/schema.py backend/tutor/prompts/resource/zh/code_sandbox.yaml backend/tutor/agents/resource/code_sandbox.py backend/tests/agents/resource/test_code_sandbox_artifacts.py backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py backend/tests/agents/resource/test_resource_agents.py frontend/lib/types.ts frontend/components/resources/CodeViewer.tsx frontend/components/resources/CodeViewer.test.tsx
git commit -m "fix: distinguish text and figure code output"
```

### Task 6: Durable general exercise drafts and submissions

**Files:**
- Create: `backend/tutor/services/exercise_responses/__init__.py`
- Create: `backend/tutor/services/exercise_responses/schema.py`
- Create: `backend/tutor/services/exercise_responses/store.py`
- Create: `backend/tutor/services/exercise_responses/publisher.py`
- Create: `backend/tests/services/exercise_responses/test_store.py`
- Create: `backend/tests/services/exercise_responses/test_publisher.py`
- Modify: `backend/tutor/api/main.py`
- Modify: `backend/tutor/api/routers/exercises.py`
- Modify: `backend/tests/api/test_exercises_router.py`

**Interfaces:**
- Produces: `ExerciseDraft`, `ExerciseSubmission`, `ExerciseResponseState`.
- Produces store methods `upsert_draft`, `get_state`, `save_submission`, `mark_event_published`, and repair cursor methods.
- Produces API endpoints:
  - `GET /exercises/{package_id}/resources/{resource_id}/responses`
  - `PUT /exercises/{package_id}/resources/{resource_id}/questions/{question_id}/draft`
  - `POST /exercises/{package_id}/resources/{resource_id}/questions/{question_id}/submit`

- [ ] **Step 1: Write failing store tests**

```python
async def test_draft_upsert_restores_without_creating_learning_event(store):
    await store.upsert_draft(draft(answer_json="B"))
    state = await store.get_state(USER, PACKAGE, RESOURCE, QUESTION)
    assert state.draft.answer_json == "B"
    assert state.submissions == []

async def test_client_submission_id_is_idempotent(store):
    first = await store.save_submission(submission(client_submission_id="client-1"))
    second = await store.save_submission(submission(client_submission_id="client-1"))
    assert first.submission_id == second.submission_id
```

Cover ownership, conflicting idempotency keys, crash-repair publication flags, and migrations on an existing empty database.

- [ ] **Step 2: Run store tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/exercise_responses -q`

Expected: FAIL because the service does not exist.

- [ ] **Step 3: Implement schemas and SQLite store**

Use separate `exercise_drafts` and `exercise_submissions` tables. Key drafts by `(user_id, package_id, resource_id, question_id)` and submissions by stable ids. Store JSON answers, question type, score/correctness, concept/course/session context, linked code attempt, timestamps, and `event_published`.

- [ ] **Step 4: Write failing router scoring tests**

```python
def test_choice_submission_scores_on_server_and_clears_draft(client, exercise_package):
    put_draft(client, answer="B")
    response = submit(client, answer="B", client_submission_id="submit-1")
    assert response.status_code == 200
    assert response.json()["correct"] is True
    assert response.json()["score"] == 1.0
    assert get_state(client)["draft"] is None
```

Add single/multiple/true-false/fill/short-answer normalisation, wrong-answer, ownership, retry, and malformed-answer tests. Reuse one general owned-question resolver instead of `_owned_code_question` duplication.

- [ ] **Step 5: Implement endpoints and application lifecycle wiring**

Resolve the canonical answer from the owned stored resource, score server-side, save terminal submission before publishing, clear the matching draft, and repair unpublished submissions at startup. Keep code execution on the existing endpoint and expose a helper to link a code attempt without double-publishing its existing event.

- [ ] **Step 6: Run store/router/publisher tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/exercise_responses backend/tests/api/test_exercises_router.py backend/tests/services/exercise_attempts -q`

Expected: PASS; ordinary and code submissions remain idempotent and owner-scoped.

- [ ] **Step 7: Commit**

```powershell
git add backend/tutor/services/exercise_responses backend/tests/services/exercise_responses backend/tutor/api/main.py backend/tutor/api/routers/exercises.py backend/tests/api/test_exercises_router.py
git commit -m "feat: persist exercise drafts and submissions"
```

### Task 7: Restore exercise work in the frontend

**Files:**
- Create: `frontend/hooks/useExerciseResponses.ts`
- Create: `frontend/hooks/useExerciseResponses.test.tsx`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/resources/ExerciseViewer.tsx`
- Modify: `frontend/components/resources/ExerciseViewer.test.tsx`
- Modify: `frontend/components/resources/CodeExerciseEditor.tsx`
- Modify: `frontend/components/resources/CodeExerciseEditor.test.tsx`

**Interfaces:**
- Consumes Task 6 response-state and submission endpoints.
- Produces hook methods `setDraft(questionId, answer)`, `submit(questionId)`, `resetDraft(questionId)`, and state keyed by question id.

- [ ] **Step 1: Write failing navigation/refresh restoration tests**

```typescript
it("restores an unsubmitted choice draft after remount", async () => {
  mockResponseState({ draft: { question_id: "q1", answer_json: "B" } });
  const { unmount } = renderExercise();
  await choose("B");
  unmount();
  renderExercise();
  expect(screen.getByLabelText("B")).toBeChecked();
});
```

Add explicit-submit-only scoring, submitted-state restoration, code draft restoration, and request cancellation on resource identity change.

- [ ] **Step 2: Run focused tests**

Run from `frontend`: `npm test -- --run hooks/useExerciseResponses.test.tsx components/resources/ExerciseViewer.test.tsx components/resources/CodeExerciseEditor.test.tsx`

Expected: FAIL because answers and code drafts are component-local.

- [ ] **Step 3: Implement API types and the response hook**

Load state on `(userId, packageId, resourceId)` change. Debounce draft PUTs, flush the latest draft on unmount with a bounded request, and ignore stale responses by identity. Only POST on explicit submit.

- [ ] **Step 4: Replace local authoritative state in both editors**

Drive ordinary question answers/submitted feedback from the hook. Keep transient input responsive through optimistic draft state. Bind CodeExerciseEditor source to the persisted draft while keeping its existing immutable execution history.

- [ ] **Step 5: Run tests and type checking**

Run from `frontend`: `npm test -- --run hooks/useExerciseResponses.test.tsx components/resources/ExerciseViewer.test.tsx components/resources/CodeExerciseEditor.test.tsx && npm run type-check`

Expected: PASS across remount and resource switches.

- [ ] **Step 6: Commit**

```powershell
git add frontend/hooks/useExerciseResponses.ts frontend/hooks/useExerciseResponses.test.tsx frontend/lib/types.ts frontend/lib/api.ts frontend/components/resources/ExerciseViewer.tsx frontend/components/resources/ExerciseViewer.test.tsx frontend/components/resources/CodeExerciseEditor.tsx frontend/components/resources/CodeExerciseEditor.test.tsx
git commit -m "feat: restore exercise drafts and feedback"
```

### Task 8: Close the exercise-to-profile/tutoring/assessment loop

**Files:**
- Modify: `backend/tutor/services/exercise_responses/publisher.py`
- Modify: `backend/tutor/services/learning_events/workflow.py`
- Modify: `backend/tutor/services/learning_events/store.py`
- Modify: `backend/tutor/capabilities/tutoring.py`
- Modify: `backend/tests/services/exercise_responses/test_publisher.py`
- Modify: `backend/tests/services/learning_events/test_workflow.py`
- Modify: `backend/tests/capabilities/test_tutoring_capability.py`
- Modify: `backend/tests/capabilities/test_assessment_capability.py`
- Modify: `backend/tests/integration/test_learning_loop.py`

**Interfaces:**
- Consumes: terminal `ExerciseSubmission` from Task 6.
- Produces: deterministic event id `exercise-response:{submission_id}`.
- Produces: `LearningEventStore.recent_exercise_evidence(user_id, limit=10)`.

- [ ] **Step 1: Write failing first-submission and tutoring-evidence tests**

```python
async def test_first_scored_submission_schedules_profile_and_path(workflow, stores):
    await workflow.event_store.append(scored_event(score=0.0, concept_id="backprop"))
    jobs = await workflow.reconcile_user(USER, session_id=SESSION)
    assert any(job.task_kind == "profile_update" for job in jobs)

async def test_tutoring_receives_recent_exercise_evidence(capability):
    await seed_scored_event(score=0.0, concept_id="chain_rule")
    await capability.run(context, stream)
    assert capability.tutoring_agent.last_profile["recent_exercises"][0]["concept_id"] == "chain_rule"
```

Verify assessment statistics include the same submission without waiting for profile batching.

- [ ] **Step 2: Run focused learning-loop tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/exercise_responses/test_publisher.py backend/tests/services/learning_events/test_workflow.py backend/tests/capabilities/test_tutoring_capability.py backend/tests/capabilities/test_assessment_capability.py backend/tests/integration/test_learning_loop.py -q`

Expected: FAIL because the first event waits for five scores and tutoring reads only the profile snapshot.

- [ ] **Step 3: Implement idempotent publication and first-event scheduling**

Publish after durable submission, then mark `event_published`; repair unmarked rows on startup. When no profile exists, let the first scored exercise define the first profile window. Preserve the five-scored-event batching threshold after a profile watermark exists.

- [ ] **Step 4: Add bounded recent evidence to tutoring**

Query only recent scored events, project concept/score/question type/time without raw submitted answers, and attach the bounded list to the profile dict passed to TutorAgent. Assessment remains event-store based; add a regression assertion rather than a second ingestion path.

- [ ] **Step 5: Run learning-loop tests**

Run the command from Step 2.

Expected: PASS; the first submission creates visible profile/path work and later submissions batch safely.

- [ ] **Step 6: Commit**

```powershell
git add backend/tutor/services/exercise_responses/publisher.py backend/tutor/services/learning_events/workflow.py backend/tutor/services/learning_events/store.py backend/tutor/capabilities/tutoring.py backend/tests/services/exercise_responses/test_publisher.py backend/tests/services/learning_events/test_workflow.py backend/tests/capabilities/test_tutoring_capability.py backend/tests/capabilities/test_assessment_capability.py backend/tests/integration/test_learning_loop.py
git commit -m "feat: feed submitted exercises into learning"
```

### Task 9: User-triggered full Manim regeneration backend

**Files:**
- Create: `backend/tutor/agents/resource/manim_repair.py`
- Create: `backend/tutor/prompts/resource/zh/manim_repair.yaml`
- Create: `backend/tutor/services/manim_render/candidate_validation.py`
- Create: `backend/tests/agents/resource/test_manim_repair.py`
- Create: `backend/tests/services/manim_render/test_candidate_validation.py`
- Modify: `backend/tutor/services/manim_render/service.py`
- Modify: `backend/tutor/services/manim_render/code_retry.py`
- Modify: `backend/tutor/services/jobs/follow_up.py`
- Modify: `backend/tutor/api/routers/resources.py`
- Modify: `backend/tutor/services/resource_package/schema.py`
- Modify: `backend/tests/services/manim_render/test_service.py`
- Modify: `backend/tests/services/manim_render/test_code_retry.py`
- Modify: `backend/tests/capabilities/test_video_render_fire_and_forget.py`
- Modify: `backend/tests/api/test_resources_artifact_endpoint.py`

**Interfaces:**
- Produces: `ManimRepairAgent.regenerate(context, failed_code, failure, runtime) -> str`.
- Produces: `validate_manim_candidate(code, *, workdir, runtime_namespace) -> CandidateValidation`.
- Produces follow-up kind `video_repair_render` with payload `{package_id, resource_id, user_id, failed_revision}`.
- Keeps the existing retry endpoint URL for compatibility, but changes it to enqueue intelligent repair.

- [ ] **Step 1: Write failing “one initial render only” service tests**

```python
async def test_initial_render_does_not_call_llm_patch_retry(failing_executor, llm):
    service = ManimRenderService(executor=failing_executor, code_retry=CodeRetry(llm=llm, max_attempts=4))
    result = await service.render(code=VALID_BUT_RUNTIME_FAILING_CODE)
    assert result.success is False
    assert failing_executor.render.call_count == 1
    assert llm.calls == []
```

Add a unit test proving `_apply_patches` rejects non-unique searches such as `run_time=0` when it is only a prefix inside `run_time=0.5`; retain the class as a safe legacy utility but remove it from the initial render path.

- [ ] **Step 2: Run service tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render/test_service.py backend/tests/services/manim_render/test_code_retry.py -q`

Expected: FAIL because the service currently performs up to four LLM patch attempts.

- [ ] **Step 3: Make initial rendering single-attempt and terminal**

Call executor once after StaticGuard, publish on success, and return the structured failure/log on failure. Do not call `_ask_llm`. Make legacy patch application require an exact unique match with identifier/token boundaries.

- [ ] **Step 4: Write failing full-regeneration and validator tests**

```python
async def test_repair_prompt_contains_full_source_and_sanitised_failure(fake_llm):
    repaired = await agent.regenerate(context, failed_code=SOURCE, failure=FAILURE, runtime=RUNTIME)
    prompt = fake_llm.last_request.messages[-1].content
    assert SOURCE in prompt
    assert FAILURE.error_code in prompt
    assert "0.5.5" not in repaired

def test_validator_rejects_bound_method_in_vgroup_and_zero_runtime():
    result = validate_manim_candidate(REPORTED_BROKEN_PATTERNS, workdir=TMP, runtime_namespace=NAMESPACE)
    assert {issue.code for issue in result.issues} >= {"BOUND_METHOD_IN_VGROUP", "NON_POSITIVE_RUN_TIME"}
```

Also test unavailable uppercase Manim names, syntax errors, missing external assets, and a valid native-shape scene.

- [ ] **Step 5: Implement the repair Agent and deterministic candidate validation**

The prompt returns one JSON `manim_code` field containing a complete `MainScene`. Include full failed source, Manim/Python versions, stable failure code, bounded traceback tail, and explicit no-external-assets constraints. Run AST/compile/StaticGuard plus Manim-specific checks before render. Permit one internal regeneration when the first replacement fails validation, using those validation issues as the second diagnostic.

- [ ] **Step 6: Write failing durable child-job tests**

```python
async def test_retry_endpoint_enqueues_video_repair_and_preserves_visible_failure(client, failed_video):
    response = client.post(retry_url(failed_video))
    assert response.json()["child"]["task_kind"] == "video_repair_render"
    resource = await reload_resource()
    assert resource.format_specific["render_status"] == "failed"
    assert resource.format_specific["repair_status"] == "pending"
```

Add success (new source/video revision), regeneration failure (original code/error remains plus repair history), idempotent active child, owner isolation, refresh/resume, and private log artifact tests.

- [ ] **Step 7: Implement `VideoRepairFollowUpCapability` and retry endpoint semantics**

Do not clear the original failure when enqueuing. Set `repair_status`/`repair_job_id`, regenerate full code, validate, render once, and atomically update the resource only under the current child claim. On success set `manim_code`, `scene_class`, `render_status=ready`, `video_url`, and increment `source_revision`. On failure keep `render_status=failed`, set `repair_status=failed`, and append a bounded repair-history record with log artifact keys.

- [ ] **Step 8: Run all focused Manim/job/API tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/api/test_resources_artifact_endpoint.py -q`

Expected: PASS with one initial render and durable full-code repair.

- [ ] **Step 9: Commit**

```powershell
git add backend/tutor/agents/resource/manim_repair.py backend/tutor/prompts/resource/zh/manim_repair.yaml backend/tutor/services/manim_render/candidate_validation.py backend/tutor/services/manim_render/service.py backend/tutor/services/manim_render/code_retry.py backend/tutor/services/jobs/follow_up.py backend/tutor/api/routers/resources.py backend/tutor/services/resource_package/schema.py backend/tests/agents/resource/test_manim_repair.py backend/tests/services/manim_render backend/tests/capabilities/test_video_render_fire_and_forget.py backend/tests/api/test_resources_artifact_endpoint.py
git commit -m "feat: regenerate failed Manim videos on demand"
```

### Task 10: Intelligent video repair UI and recovery

**Files:**
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/resources/VideoViewer.tsx`
- Modify: `frontend/components/resources/VideoViewer.test.tsx`
- Modify: `frontend/lib/event-handler.ts`
- Modify: `frontend/lib/event-handler.test.ts`

**Interfaces:**
- Consumes Task 9 fields `repair_status`, `repair_job_id`, `source_revision`, and `repair_history`.
- Keeps `retryVideoRender(...)` API name for compatibility, with new intelligent-repair semantics.

- [ ] **Step 1: Write failing UI tests**

```typescript
it("labels failed-video action as intelligent regeneration", async () => {
  renderFailedVideo();
  await user.click(screen.getByRole("button", { name: "智能修复并重新渲染" }));
  expect(retryVideoRender).toHaveBeenCalledWith(USER, PACKAGE, RESOURCE);
  expect(screen.getByText("正在生成修复代码并重新渲染…")).toBeInTheDocument();
});
```

Add tests for original error remaining visible while repair is pending, failed repair history, success replacement, duplicate click disabling, polling recovery, unmount/remount, and backend restart snapshot hydration.

- [ ] **Step 2: Run focused tests**

Run from `frontend`: `npm test -- --run components/resources/VideoViewer.test.tsx lib/event-handler.test.ts`

Expected: FAIL because the UI says “重新渲染” and the backend currently resets the failure into generic pending.

- [ ] **Step 3: Implement repair-specific rendering and polling**

Use `repair_status` for the repair banner without treating the base `render_status=failed` as a live spinner. Show the original failure and a separate repair-progress area. Rehydrate the child from job detail, fetch canonical package detail after terminal, and show the latest bounded repair failure if regeneration fails.

- [ ] **Step 4: Run tests and type checking**

Run from `frontend`: `npm test -- --run components/resources/VideoViewer.test.tsx lib/event-handler.test.ts && npm run type-check`

Expected: PASS across click, failure, refresh, and success.

- [ ] **Step 5: Commit**

```powershell
git add frontend/lib/types.ts frontend/lib/api.ts frontend/components/resources/VideoViewer.tsx frontend/components/resources/VideoViewer.test.tsx frontend/lib/event-handler.ts frontend/lib/event-handler.test.ts
git commit -m "feat: expose intelligent Manim repair"
```

### Task 11: Cross-system regression and real-runtime verification

**Files:**
- Modify: `frontend/e2e/reliability.spec.ts`
- Create: `backend/tests/integration/test_local_validation_follow_up.py`
- Modify: `docs/runbooks/local-development.md` if present, otherwise `README.md`

**Interfaces:**
- Consumes all prior tasks.
- Produces repeatable local validation commands and evidence.

- [ ] **Step 1: Add focused integration coverage for the reported cases**

Backend integration must cover a complete exercise resource passing through runner projection, draft/save/submit/event/profile scheduling, and a failed video followed by a mocked full regeneration child. Frontend E2E must cover completed workflow retention, normal exercise options, draft restoration, Mermaid fallback, text-only code without a false image error, and intelligent video repair state.

- [ ] **Step 2: Run the new integration tests**

Run backend: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/integration/test_local_validation_follow_up.py -q`

Run from `frontend`: `npm test -- --run && npm run type-check`

Expected: PASS.

- [ ] **Step 3: Run real Matplotlib and Manim smoke tests in `tutor`**

Run targeted real tests with the configured execution interpreter and a small native-shape Manim scene. Verify:

```text
NumPy-only script: success, stderr empty, artifacts=[]
Matplotlib plot: success, figure_1.png exists
Initial broken Manim: one render attempt, failed terminal
Repair replacement: static validation passes, MP4 exists and resource becomes ready
```

- [ ] **Step 4: Run MiniMax MCP acceptance and refresh/restart checks**

Run from `frontend`: `npm run test:e2e -- --grep "MiniMax MCP|completed workflow|exercise draft|intelligent video repair"`

Expected: PASS when the local backend and configured MiniMax MCP service are running. If the external MCP is unavailable, record that test as environment-blocked while all deterministic mocked coverage remains green.

- [ ] **Step 5: Run broad regression suites**

Run backend: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q`

Run from `frontend`: `npm test -- --run && npm run type-check && npm run lint`

Expected: PASS. Inspect `git status --short`; only intentional task files and the pre-existing `frontend/next-env.d.ts` difference may remain.

- [ ] **Step 6: Document local retest commands and commit**

```powershell
git add frontend/e2e/reliability.spec.ts backend/tests/integration/test_local_validation_follow_up.py README.md
git commit -m "test: cover local validation follow-up flows"
```

## Subagent-Driven Execution Order

Subagent-Driven implementation uses one fresh implementer at a time and accepts
the task only after spec-compliance and code-quality review. The independent
boundaries below still minimise context and test time, but no two implementers
write to the shared worktree concurrently.

1. Tasks 1, 3, 4, and 6 establish the four independent backend/frontend foundations.
2. Task 2 follows Tasks 1/3; Task 5 follows Task 4; Task 8 follows Task 6.
3. Task 7 follows Tasks 2/6; Task 9 follows Tasks 4/5.
4. Task 10 follows Tasks 7/9, then Task 11 performs broad verification.
5. Every subagent receives its extracted task brief, the approved design constraints, and an explicit file ownership boundary. The primary agent performs spec review and code-quality review before accepting each task.
