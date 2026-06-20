# Modular Learning Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Tutor into a stable single-user demonstration system with recoverable jobs, intent-aware resource planning, independently configurable AI services, uploadable knowledge bases, and four clear product pages.

**Architecture:** Keep FastAPI, the existing capability/agent layer, SQLite, and Next.js. Add explicit application services for intent routing, resource planning, configuration, and knowledge ingestion; make persisted jobs the cross-layer source of truth and render all terminal outcomes through a typed result contract.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, SQLAlchemy/SQLite, pytest/pytest-asyncio, Next.js 16, React 19, TypeScript, Zustand, Vitest, React Testing Library, Tailwind CSS.

---

## File map

New backend modules:

- `backend/tutor/services/jobs/contracts.py`: typed progress, artifact, warning, error, and terminal result models.
- `backend/tutor/services/intent/router.py`: deterministic fast-path intent classification.
- `backend/tutor/services/resource_plan/schema.py`: editable resource-plan request/response models.
- `backend/tutor/services/resource_plan/service.py`: profile-aware and cost-aware resource recommendations.
- `backend/tutor/api/routers/plans.py`: plan and confirm endpoints.
- `backend/tutor/api/routers/config.py`: masked configuration read/update/test endpoints.
- `backend/tutor/services/config/runtime.py`: atomic `.env` update and provider cache refresh.
- `backend/tutor/services/knowledge_base/schema.py`: library, document, and ingestion status models.
- `backend/tutor/services/knowledge_base/store.py`: SQLite metadata persistence.
- `backend/tutor/services/knowledge_base/loaders.py`: PDF/DOCX/PPTX/MD/TXT extraction.
- `backend/tutor/services/knowledge_base/service.py`: upload, extraction, chunking, indexing, retry, delete.
- `backend/tutor/api/routers/knowledge_bases.py`: knowledge-base REST API.

New frontend modules:

- `frontend/lib/job-reducer.ts`: pure per-job event reducer and terminal message builder.
- `frontend/lib/resource-plan.ts`: plan types and selection helpers.
- `frontend/app/knowledge-bases/page.tsx`: knowledge-base management page.
- `frontend/app/resources/page.tsx`: persisted resource center.
- `frontend/app/settings/page.tsx`: appearance and AI service configuration.
- `frontend/components/layout/AppShell.tsx`: shared four-page navigation shell.
- `frontend/components/workspace/ResourcePlanCard.tsx`: recommendation confirmation UI.
- `frontend/components/workspace/JobProgressCard.tsx`: per-job progress and partial-failure UI.
- `frontend/components/knowledge-base/KnowledgeBaseCard.tsx`: library/index status card.
- `frontend/components/settings/ServiceConfigSection.tsx`: masked config form and connection test.

Existing files modified in focused places:

- Job persistence/execution: `backend/tutor/services/jobs/{schema,store,runner}.py`, `backend/tutor/api/routers/{jobs,unified_ws}.py`.
- Routing/resource generation: `backend/tutor/runtime/orchestrator.py`, `backend/tutor/capabilities/resource_generation.py`.
- App composition: `backend/tutor/api/main.py`.
- Frontend transport/state: `frontend/lib/{types,store,event-handler,api}.ts`, `frontend/hooks/useJobQueue.ts`.
- UI composition: `frontend/app/{layout,page}.tsx`, current sidebar/chat/resource components.

## Task 1: Reproducible test environment and frontend test harness

**Files:**
- Modify: `pyproject.toml`
- Modify: `frontend/package.json`
- Create: `frontend/vitest.config.ts`
- Create: `frontend/test/setup.ts`
- Create: `frontend/lib/job-reducer.test.ts`

- [ ] **Step 1: Pin the Python settings dependency used by the code**

Change the dependency to `pydantic-settings>=2.5.0` because `NoDecode` is imported by `settings.py`, and retain `pytest-asyncio>=0.24.0` in the dev extra.

- [ ] **Step 2: Install the existing backend project and verify collection**

Run: `python -m pip install -e ".[dev]"`

Run: `python -m pytest --collect-only -q`

Expected: collection completes without `ModuleNotFoundError: loguru`, missing `pytest-asyncio`, or missing `NoDecode`.

- [ ] **Step 3: Add Vitest and Testing Library**

Run: `npm install --workspace frontend --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom`

Add scripts:

```json
"test": "vitest run",
"test:watch": "vitest"
```

Configure `vitest.config.ts` with `environment: "jsdom"`, alias `@` to the frontend root, and setup file `test/setup.ts`; the setup imports `@testing-library/jest-dom/vitest`.

- [ ] **Step 4: Prove the frontend harness executes**

Create a temporary test in `job-reducer.test.ts` asserting `expect(true).toBe(true)`.

Run: `npm test --workspace frontend`

Expected: one passing test. Remove the temporary assertion when Task 2 adds behavioral tests.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml frontend/package.json package-lock.json frontend/vitest.config.ts frontend/test/setup.ts frontend/lib/job-reducer.test.ts
git commit -m "test: establish reproducible backend and frontend harnesses"
```

## Task 2: Typed job result contract and explicit terminal states

**Files:**
- Create: `backend/tutor/services/jobs/contracts.py`
- Create: `backend/tests/services/jobs/test_contracts.py`
- Modify: `backend/tutor/services/jobs/schema.py`
- Modify: `backend/tutor/services/jobs/store.py`
- Modify: `backend/tutor/services/jobs/runner.py`
- Modify: `backend/tutor/api/routers/jobs.py`

- [ ] **Step 1: Write failing contract tests**

Test these exact behaviors:

```python
def test_terminal_result_requires_visible_message():
    with pytest.raises(ValidationError):
        JobResultContract(job_id="j1", capability="tutoring", status="succeeded", assistant_message="")

def test_partial_result_lists_successes_and_failures():
    result = JobResultContract(
        job_id="j1", capability="resource_generation", status="partial",
        assistant_message="已生成 2 项，1 项失败",
        artifacts=[ArtifactResult(resource_type="document", status="succeeded")],
        warnings=[JobWarning(code="ARTIFACT_FAILED", message="video failed")],
    )
    assert result.status == JobTerminalStatus.PARTIAL
```

Run: `python -m pytest backend/tests/services/jobs/test_contracts.py -v`

Expected: FAIL because `contracts.py` does not exist.

- [ ] **Step 2: Implement the minimal contract**

Define `JobTerminalStatus = Literal["succeeded", "partial", "failed", "cancelled"]`, plus `JobProgress`, `ArtifactResult`, `JobWarning`, `JobError`, and `JobResultContract`. Validate `assistant_message` with `min_length=1`.

- [ ] **Step 3: Expand persisted job status safely**

Replace `COMPLETED` with `SUCCEEDED` and add `PARTIAL`. During row hydration map legacy database value `"completed"` to `JobStatus.SUCCEEDED` so current `jobs.db` remains readable.

- [ ] **Step 4: Make runner terminalization deterministic**

In `runner.py`, normalize capability output into `JobResultContract`; if artifacts include both succeeded and failed entries, store `PARTIAL`; if no structured result exists, store `FAILED` with `MISSING_RESULT`. Broadcast one terminal event containing the normalized contract instead of an empty duplicate `done` sentinel.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest backend/tests/services/jobs/test_contracts.py backend/tests/services/jobs -v`

Expected: PASS.

```bash
git add backend/tutor/services/jobs backend/tutor/api/routers/jobs.py backend/tests/services/jobs
git commit -m "feat: define recoverable job result contract"
```

## Task 3: Per-job frontend reducer and no-output regression

**Files:**
- Create: `frontend/lib/job-reducer.ts`
- Modify: `frontend/lib/job-reducer.test.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/store.ts`
- Modify: `frontend/lib/event-handler.ts`
- Modify: `frontend/hooks/useJobQueue.ts`

- [ ] **Step 1: Write the exact regression test**

```ts
it("adds a visible assistant message when an async job succeeds", () => {
  const state = createJobState("job-1", "resource_generation");
  const next = reduceJobEvent(state, {
    type: "job_terminal", job_id: "job-1", capability: "resource_generation",
    result: { status: "succeeded", assistant_message: "已生成 3 项资源", artifacts: [] }
  });
  expect(next.jobsById["job-1"].status).toBe("succeeded");
  expect(next.messages.at(-1)?.content).toBe("已生成 3 项资源");
});

it("does not treat an older assistant message as output for a new job", () => {
  const state = createJobState("job-2", "tutoring", [{ role: "assistant", content: "旧回答" }]);
  const next = reduceJobEvent(state, terminalEvent("job-2", "新回答"));
  expect(next.messages.map(m => m.content)).toEqual(["旧回答", "新回答"]);
});
```

Run: `npm test --workspace frontend -- job-reducer.test.ts`

Expected: FAIL because reducer behavior is missing.

- [ ] **Step 2: Implement normalized state**

Add `jobsById: Record<string, ClientJob>` and `jobOrder: string[]` to the store. `useJobQueue.submit` inserts the submitted job immediately using the server-returned `job_id` and capability. `dispatchStreamEvent` requires `job_id`, delegates to the pure reducer, and never reads `currentCapability` to infer event ownership.

- [ ] **Step 3: Add replay and deduplication tests**

Assert duplicate `event_id` changes neither event count nor message count, and an event with `seq <= last_seq` is ignored unless it is a REST snapshot newer than local `updated_at`.

- [ ] **Step 4: Remove the synthetic completion heuristic**

Delete `injectCompletionMessageIfMissing`; render only the server contract's `assistant_message`. Keep legacy event compatibility behind a small adapter that derives `job_id` from event metadata and emits a visible protocol error when absent.

- [ ] **Step 5: Verify and commit**

Run: `npm test --workspace frontend`

Run: `npm run type-check --workspace frontend`

Expected: all tests and type checking pass.

```bash
git add frontend/lib frontend/hooks/useJobQueue.ts frontend/package.json
git commit -m "fix: bind streamed output to persisted jobs"
```

## Task 4: Intent routing and confirm-before-generate resource plans

**Files:**
- Create: `backend/tutor/services/intent/{__init__,router}.py`
- Create: `backend/tutor/services/resource_plan/{__init__,schema,service}.py`
- Create: `backend/tutor/api/routers/plans.py`
- Create: `backend/tests/services/intent/test_router.py`
- Create: `backend/tests/services/resource_plan/test_service.py`
- Modify: `backend/tutor/runtime/orchestrator.py`
- Modify: `backend/tutor/api/main.py`

- [ ] **Step 1: Write failing routing tests**

Assert `解释 self-attention` routes to `tutoring`; `为 Transformer 制定学习资源` routes to `resource_generation`; `生成一个注意力动画` routes to planning with video requested; and a generic explanation never requests video.

- [ ] **Step 2: Implement deterministic router precedence**

Use precedence: explicit capability → assessment → profile → path → explicit resource-generation language → tutoring default. Do not include `解释` or `讲解` in resource-generation keywords.

- [ ] **Step 3: Write failing resource-plan tests**

Assert the recommended default contains document, mindmap, and exercise; profile modality can add reading or code; video and PPT require explicit user request or explicit selection; comparison queries exclude video; selected types are validated against the seven supported types.

- [ ] **Step 4: Implement plan and confirm endpoints**

`POST /api/v1/plans` returns `{plan_id, intent, topic, recommended[], optional[], estimated_seconds}` without starting a generation job. `POST /api/v1/plans/{plan_id}/confirm` accepts `selected_types`, creates the job with those types in metadata, and returns its `job_id`.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest backend/tests/services/intent backend/tests/services/resource_plan -v`

```bash
git add backend/tutor/services/intent backend/tutor/services/resource_plan backend/tutor/api/routers/plans.py backend/tutor/runtime/orchestrator.py backend/tutor/api/main.py backend/tests/services/intent backend/tests/services/resource_plan
git commit -m "feat: plan resource generation before execution"
```

## Task 5: Independent artifact execution and partial retry

**Files:**
- Modify: `backend/tutor/capabilities/resource_generation.py`
- Modify: `backend/tutor/services/jobs/runner.py`
- Modify: `backend/tutor/api/routers/jobs.py`
- Modify: `backend/tests/capabilities/test_resource_generation_capability.py`
- Create: `backend/tests/services/jobs/test_retry.py`

- [ ] **Step 1: Write a failing partial-generation test**

Inject three fake artifact generators where document and exercise succeed and video raises. Assert the final contract is `partial`, contains two succeeded artifacts, one failed video artifact, and a visible summary naming the failed type.

- [ ] **Step 2: Make selected types authoritative**

Read `context.metadata["selected_resource_types"]`; never add an unselected video or PPT in `_plan_resources`. Wrap each type result as `ArtifactResult` with duration, agent names, resource ID, and structured error.

- [ ] **Step 3: Add retry endpoint behavior**

`POST /api/v1/jobs/{user_id}/{job_id}/retry` accepts `resource_types`. Validate that each requested type failed in the source job, submit a child job with `parent_job_id`, and preserve successful source artifacts for package reassembly.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest backend/tests/capabilities/test_resource_generation_capability.py backend/tests/services/jobs/test_retry.py -v`

```bash
git add backend/tutor/capabilities/resource_generation.py backend/tutor/services/jobs backend/tutor/api/routers/jobs.py backend/tests
git commit -m "feat: support partial resource jobs and targeted retry"
```

## Task 6: Masked runtime configuration API

**Files:**
- Create: `backend/tutor/services/config/runtime.py`
- Create: `backend/tutor/api/routers/config.py`
- Create: `backend/tests/services/config/test_runtime.py`
- Create: `backend/tests/api/test_config_router.py`
- Modify: `backend/tutor/api/main.py`
- Modify: `backend/tutor/services/llm/provider_factory.py`
- Modify: `backend/tutor/services/embeddings/embedder_factory.py`

- [ ] **Step 1: Write security-first failing tests**

Assert GET never contains a raw key; PATCH with `api_key: null` preserves it; `clear_api_key: true` removes it; invalid provider/model values return 422; `.env` replacement uses a temporary sibling file and `Path.replace`.

- [ ] **Step 2: Implement `RuntimeConfigService`**

Expose three sections (`llm`, `embedding`, `web_search`) plus appearance-independent metadata. Resolve the project-root `.env`, serialize supported `TUTOR_*` keys, atomically replace, call `reset_settings_cache()`, and clear provider factory caches.

- [ ] **Step 3: Implement connection tests**

`POST /config/test/llm` performs a minimal `回复 OK` completion; embedding sends one short string and validates a nonempty vector; web search requests one result. Return `{ok, provider, model, latency_ms, message}` and map authentication, timeout, DNS, and model errors to stable codes without echoing secrets.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest backend/tests/services/config backend/tests/api/test_config_router.py -v`

```bash
git add backend/tutor/services/config backend/tutor/api/routers/config.py backend/tutor/api/main.py backend/tutor/services/llm/provider_factory.py backend/tutor/services/embeddings/embedder_factory.py backend/tests/services/config backend/tests/api/test_config_router.py
git commit -m "feat: add masked runtime AI configuration"
```

## Task 7: Settings page with per-service connection tests

**Files:**
- Create: `frontend/app/settings/page.tsx`
- Create: `frontend/components/settings/ServiceConfigSection.tsx`
- Create: `frontend/components/settings/ServiceConfigSection.test.tsx`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/components/layout/SettingsModal.tsx`

- [ ] **Step 1: Write failing component tests**

Assert an existing key renders as `••••••••` but is never placed in an input value; blank key submission omits `api_key`; clicking “测试连接” disables the button and renders latency on success or the stable error message on failure.

- [ ] **Step 2: Implement typed API functions**

Add `getRuntimeConfig`, `updateRuntimeConfig(section, patch)`, and `testRuntimeConfig(section)`. Use a JSON request for configuration; never store keys in Zustand or localStorage.

- [ ] **Step 3: Build settings groups**

Render appearance, LLM, Embedding, and Web Search sections. Replace the old modal's future-feature text with a link to `/settings`; retain the theme switch for quick access.

- [ ] **Step 4: Verify and commit**

Run: `npm test --workspace frontend -- ServiceConfigSection.test.tsx`

Run: `npm run type-check --workspace frontend`

```bash
git add frontend/app/settings frontend/components/settings frontend/components/layout/SettingsModal.tsx frontend/lib
git commit -m "feat: configure and test AI services in the UI"
```

## Task 8: Knowledge-base metadata, extraction, and ingestion API

**Files:**
- Create: `backend/tutor/services/knowledge_base/{__init__,schema,store,loaders,service}.py`
- Create: `backend/tutor/api/routers/knowledge_bases.py`
- Create: `backend/tests/services/knowledge_base/{test_store,test_loaders,test_service}.py`
- Create: `backend/tests/api/test_knowledge_bases_router.py`
- Modify: `backend/tutor/api/main.py`

- [ ] **Step 1: Write loader tests with five fixture formats**

Generate tiny valid PDF, DOCX, PPTX, Markdown, and TXT fixtures using installed libraries. Assert extracted text is nonempty and source anchors include page, paragraph, or slide. Assert corrupted PDF and an empty TXT raise stable `EXTRACTION_FAILED` and `EMPTY_DOCUMENT` errors.

- [ ] **Step 2: Define persistence models**

Create `KnowledgeBaseRecord` and `KnowledgeDocument` with IDs, display name, file metadata, checksum, status (`uploaded|extracting|chunking|embedding|ready|failed`), chunk count, embedding model, timestamps, and error.

- [ ] **Step 3: Implement service transitions**

Store uploads under `data/knowledge_bases/{kb_id}/sources/{document_id}/`; reject unsupported extensions and duplicate checksums; extract text; chunk with configured size/overlap; call the existing embedder; persist index metadata. On failure set `failed` and keep diagnostic text for retry.

- [ ] **Step 4: Implement REST endpoints**

Add list/create/detail/delete library, multipart document upload, document retry/delete, and select-active endpoints. Initialize the prebuilt `ai_introduction` directory as a read-only seeded library on startup.

- [ ] **Step 5: Verify and commit**

Run: `python -m pytest backend/tests/services/knowledge_base backend/tests/api/test_knowledge_bases_router.py -v`

```bash
git add backend/tutor/services/knowledge_base backend/tutor/api/routers/knowledge_bases.py backend/tutor/api/main.py backend/tests/services/knowledge_base backend/tests/api/test_knowledge_bases_router.py
git commit -m "feat: upload and index course knowledge bases"
```

## Task 9: Knowledge-base page and active-library selection

**Files:**
- Create: `frontend/app/knowledge-bases/page.tsx`
- Create: `frontend/components/knowledge-base/KnowledgeBaseCard.tsx`
- Create: `frontend/components/knowledge-base/KnowledgeBaseCard.test.tsx`
- Create: `frontend/hooks/useKnowledgeBases.ts`
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/store.ts`

- [ ] **Step 1: Write failing UI tests**

Assert cards display document/chunk counts and index status; upload accepts only the five formats; failed documents show error and retry; selecting a library updates the active marker and store.

- [ ] **Step 2: Build the page**

Use multipart `FormData` without forcing a JSON content type. Poll only libraries with nonterminal documents every two seconds; stop polling when all are ready or failed. Show empty, uploading, indexing, ready, and failed states.

- [ ] **Step 3: Pass knowledge base ID into plans and tutoring**

Add `activeKnowledgeBaseId` to session state and every plan/job submission. Default to seeded `ai_introduction`; do not silently switch libraries after the user selects one.

- [ ] **Step 4: Verify and commit**

Run: `npm test --workspace frontend -- KnowledgeBaseCard.test.tsx`

Run: `npm run type-check --workspace frontend`

```bash
git add frontend/app/knowledge-bases frontend/components/knowledge-base frontend/hooks/useKnowledgeBases.ts frontend/lib
git commit -m "feat: manage knowledge bases from a dedicated page"
```

## Task 10: App shell, learning workspace, plan confirmation, and resource center

**Files:**
- Create: `frontend/components/layout/AppShell.tsx`
- Create: `frontend/components/workspace/{ResourcePlanCard,JobProgressCard}.tsx`
- Create: `frontend/components/workspace/ResourcePlanCard.test.tsx`
- Create: `frontend/app/resources/page.tsx`
- Modify: `frontend/app/layout.tsx`
- Modify: `frontend/app/page.tsx`
- Modify: `frontend/components/layout/Sidebar.tsx`
- Modify: `frontend/components/chat/ChatComposer.tsx`
- Modify: `frontend/components/chat/JobTray.tsx`

- [ ] **Step 1: Write plan-confirmation tests**

Assert recommended types start selected; user can deselect video/PPT; estimated time updates; confirm sends exactly selected types; ordinary tutoring renders no plan card and starts immediately.

- [ ] **Step 2: Build the shared four-page shell**

Navigation entries are `/`, `/knowledge-bases`, `/resources`, and `/settings`. Move capability buttons out of global navigation; task intent belongs to the composer/planner. Preserve theme and service status in the shell.

- [ ] **Step 3: Refactor the workspace**

Keep chat as primary content. Add side cards for path, per-job progress, and six-dimension profile summary. Use `JobProgressCard` to display stage, percent, active agents, succeeded/failed artifacts, cancel, and retry-failed actions.

- [ ] **Step 4: Build resource center**

Use existing persisted package endpoints and viewers. Add filters for topic, resource type, terminal status, and time; display generated-by agents, confidence, citations, safety verdict, preview/download, and single-type regeneration.

- [ ] **Step 5: Verify and commit**

Run: `npm test --workspace frontend`

Run: `npm run type-check --workspace frontend`

Run: `npm run build --workspace frontend`

```bash
git add frontend/app frontend/components frontend/lib frontend/hooks
git commit -m "feat: deliver modular learning workspace and resource center"
```

## Task 11: Citation/safety evidence and dynamic learning-loop integration

**Files:**
- Modify: `backend/tutor/tools/rag_tool.py`
- Modify: `backend/tutor/capabilities/tutoring.py`
- Modify: `backend/tutor/capabilities/resource_generation.py`
- Modify: `backend/tutor/capabilities/assessment.py`
- Modify: `backend/tutor/capabilities/path_planning.py`
- Modify: `backend/tutor/services/resource_package/schema.py`
- Create: `backend/tests/integration/test_learning_loop.py`

- [ ] **Step 1: Write a failing learning-loop integration test**

Seed a tiny knowledge base and learner profile. Assert tutoring returns source anchors; resource artifacts expose citations, confidence, review verdict, and generated-by agents; a weak exercise result updates the profile error pattern; a subsequent path plan prioritizes the weak concept.

- [ ] **Step 2: Make evidence fields consistent**

Normalize each resource's metadata to `citations[]`, `confidence`, `review`, `safety`, and `generated_by[]`. Preserve `unverified` claims as warnings rather than silently presenting them as verified.

- [ ] **Step 3: Connect assessment to profile and path updates**

Persist learning events from resource views and exercise attempts. Assessment writes mastery/error deltas through the existing profile builder; path planning reads the updated snapshot and includes a human-readable `reason` per recommended node.

- [ ] **Step 4: Verify and commit**

Run: `python -m pytest backend/tests/integration/test_learning_loop.py backend/tests/agents/safety backend/tests/capabilities -v`

```bash
git add backend/tutor backend/tests/integration/test_learning_loop.py
git commit -m "feat: expose evidence and close the adaptive learning loop"
```

## Task 12: Full verification, documentation, and demo fixtures

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `docs/architecture.md`
- Modify: `docs/knowledge-base.md`
- Create: `docs/demo-script.md`
- Create: `backend/tests/e2e/test_demo_scenarios.py`

- [ ] **Step 1: Add API-level demo scenarios**

Cover profile dialogue, ordinary cited tutoring without video, plan confirmation for five resources, partial generation and targeted retry, knowledge upload and retrieval, bad-key config test, and job snapshot recovery.

- [ ] **Step 2: Update documentation and licenses**

Document the four pages, configuration workflow, upload formats, job states, troubleshooting, and exact startup/test commands. List every added open-source dependency with project URL and license in README's acknowledgment section.

- [ ] **Step 3: Run the complete backend suite**

Run: `python -m pytest -q`

Expected: all collected tests pass with no collection errors.

- [ ] **Step 4: Run complete frontend verification**

Run: `npm test --workspace frontend`

Run: `npm run type-check --workspace frontend`

Run: `npm run build --workspace frontend`

Expected: tests, TypeScript, and production build pass without warnings treated as errors.

- [ ] **Step 5: Execute the documented demo script**

Start backend and frontend, then follow `docs/demo-script.md`. Confirm first visible feedback appears within one second, each job has a visible terminal message, and a page refresh restores active job state.

- [ ] **Step 6: Commit**

```bash
git add README.md .env.example docs backend/tests/e2e/test_demo_scenarios.py
git commit -m "docs: finalize verified Tutor demonstration workflow"
```
