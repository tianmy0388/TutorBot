# TutorBot End-to-End Reliability Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 TutorBot 在本地单用户模式下可靠保存并恢复对话与资源，准确结束任务，完整支持图片、代码练习、Manim 视频、学习画像、学习路径和会话级联网搜索。

**Architecture:** 先在存储边界统一数据目录、身份和资源定位，再让 `JobRunner` 成为唯一终态写入者，并把视频、画像、路径建模为持久化子任务。前端只从 `jobsById` 和持久化聚合接口恢复状态；资源执行、练习提交和联网搜索通过显式协议接入，不再依赖内存态或隐式后台协程。

**Tech Stack:** Python 3.11（本地 Conda `tutor` 环境）、FastAPI、Pydantic、SQLite、asyncio、Next.js、React、TypeScript、Zustand、Vitest、Testing Library、Playwright、Matplotlib、Manim。

## Global Constraints

- 所有 Python 命令使用 `E:\\Anaconda3\\anaconda\\envs\\tutor\\python.exe`。
- 从仓库根目录运行后端命令前设置 `$env:PYTHONPATH="backend"`。
- 本轮不引入 Celery、Redis 或新的数据库；持久化继续使用现有 SQLite stores。
- `TUTOR_MULTI_USER_ENABLED=false` 时唯一用户 ID 必须是 `local-user`；多用户模式保留现有显式用户校验。
- 数据迁移必须先备份、支持 `--dry-run`、可重复执行，并且不得删除源数据。
- 新写入数据库的文件引用只保存相对 `settings.data_dir` 的 `artifact_key`，不保存绝对路径。
- 每个作业只能写入一次 `job_terminal`；能力层不得写 `done`、`error` 或作业终态。
- 联网搜索默认关闭，按 conversation 保存；新 conversation 始终从关闭状态开始。
- Python 代码题仅支持编辑器输入和 `.py` 上传，执行沿用本地受限沙箱，提交与测试结果必须持久化。
- 源码、JSON、数据库文本和日志统一按 UTF-8 读写。
- 保留用户现有改动；每个任务只提交该任务列出的文件。

---

## File Structure

### New backend modules

- `backend/tutor/services/identity/policy.py`: local/multi-user identity resolution.
- `backend/tutor/services/migration/local_single_user.py`: backup, discovery, dry-run report, user/data consolidation.
- `backend/tutor/services/artifacts/keys.py`: safe relative artifact-key conversion and resolution.
- `backend/tutor/core/capability_result.py`: capability result and durable follow-up task protocol.
- `backend/tutor/services/jobs/follow_up.py`: idempotent child-job creation and parent progress projection.
- `backend/tutor/runtime/workflow_graph.py`: typed agent DAG, node timeout and degradation execution.
- `backend/tutor/services/exercise_attempts/schema.py`: code exercise request/result models.
- `backend/tutor/services/exercise_attempts/store.py`: SQLite attempt persistence.
- `backend/tutor/api/routers/exercises.py`: exercise submission and history endpoints.
- `backend/tutor/api/routers/learning.py`: learning-event ingestion and profile/path read endpoints.
- `backend/tutor/services/search/policy.py`: conversation-aware search authorization.

### New frontend modules

- `frontend/components/resources/ImageLightbox.tsx`: in-page image zoom, pan, reset and download.
- `frontend/components/resources/CodeExerciseEditor.tsx`: Python editor, `.py` upload, submit and result history.
- `frontend/components/chat/WebSearchToggle.tsx`: accessible per-conversation search switch.
- `frontend/e2e/reliability.spec.ts`: refresh, terminal-state, image, exercise, video and search regression flow.
- `frontend/playwright.config.ts`: deterministic local E2E configuration.

### Existing boundaries to modify

- Settings and stores: `backend/tutor/services/config/settings.py`, conversation/resource/job/profile/event stores.
- API entry points: conversation, resource, job, unified WebSocket and new learning/exercise routers.
- Runtime: capability protocol, `JobRunner`, resource/tutoring/profile/path capabilities and intent router.
- Resource execution: code sandbox and Manim guard/retry/executor/service.
- Frontend state: `frontend/lib/types.ts`, `api.ts`, `store.ts`, reducer, page and chat/resource components.

---

### Task 1: Canonical data directory and safe migration inventory

**Files:**
- Create: `backend/tutor/services/migration/__init__.py`
- Create: `backend/tutor/services/migration/local_single_user.py`
- Create: `backend/tests/services/migration/test_local_single_user.py`
- Modify: `backend/tutor/services/config/settings.py` (`_default_data_dir`, `resolve_path`)
- Modify: `backend/tutor/cli/main.py` (register `migrate-local-data`)

**Interfaces:**
- Consumes: `Settings.data_dir: Path` and current SQLite/file stores.
- Produces: `MigrationReport`, `build_migration_report(repo_root: Path, target_user_id: str) -> MigrationReport`, and `run_local_migration(repo_root: Path, target_user_id: str, dry_run: bool) -> MigrationReport`.

- [ ] **Step 1: Write the failing path and dry-run tests**

```python
def test_relative_data_dir_resolves_from_repo_root(tmp_path, monkeypatch):
    repo = tmp_path / "TutorBot"
    (repo / "backend" / "tutor").mkdir(parents=True)
    monkeypatch.chdir(repo)
    settings = Settings(TUTOR_DATA_DIR="./data")
    assert settings.data_dir == repo / "data"


def test_dry_run_lists_sources_without_writing(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "backend" / "data").mkdir(parents=True)
    report = run_local_migration(tmp_path, "local-user", dry_run=True)
    assert report.source_dirs == (
        (tmp_path / "data").resolve(),
        (tmp_path / "backend" / "data").resolve(),
    )
    assert report.backup_dir is None
    assert report.written_files == 0
```

- [ ] **Step 2: Run the focused tests and confirm the old root is wrong**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/migration/test_local_single_user.py -v`

Expected: FAIL because the migration module is absent and `./data` resolves under `backend`.

- [ ] **Step 3: Implement deterministic root discovery and a read-only report**

```python
@dataclass(frozen=True)
class MigrationReport:
    source_dirs: tuple[Path, ...]
    target_dir: Path
    backup_dir: Path | None
    discovered_users: tuple[str, ...]
    written_files: int


def build_migration_report(repo_root: Path, target_user_id: str) -> MigrationReport:
    candidates = (repo_root / "data", repo_root / "backend" / "data")
    sources = tuple(path.resolve() for path in candidates if path.exists())
    users = tuple(sorted(_discover_user_ids(sources)))
    return MigrationReport(sources, (repo_root / "data").resolve(), None, users, 0)


def run_local_migration(repo_root: Path, target_user_id: str, dry_run: bool) -> MigrationReport:
    report = build_migration_report(repo_root, target_user_id)
    if dry_run:
        return report
    backup_dir = _copy_sources_to_timestamped_backup(report.source_dirs, repo_root / "backups")
    written = _merge_databases_and_artifacts(report, target_user_id)
    return replace(report, backup_dir=backup_dir, written_files=written)
```

Set the settings root with `Path(__file__).resolve().parents[4]` only through a named `_repo_root()` helper covered by the test. Register CLI arguments `--repo-root`, `--target-user-id local-user`, and `--dry-run`.

- [ ] **Step 4: Run migration tests and CLI dry-run**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/migration/test_local_single_user.py -v`

Expected: PASS.

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m tutor.cli.main migrate-local-data --repo-root E:\github\TutorBot --target-user-id local-user --dry-run`

Expected: exit 0, reports both discovered data locations when present, and reports zero writes.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/config/settings.py backend/tutor/services/migration backend/tutor/cli/main.py backend/tests/services/migration/test_local_single_user.py
git commit -m "feat: add safe local data migration inventory"
```

### Task 2: Canonical local identity and ownership consolidation

**Files:**
- Create: `backend/tutor/services/identity/__init__.py`
- Create: `backend/tutor/services/identity/policy.py`
- Create: `backend/tests/services/identity/test_policy.py`
- Modify: `backend/tutor/services/migration/local_single_user.py` (`_merge_databases_and_artifacts`)
- Modify: `backend/tutor/api/routers/conversations.py` (all ownership checks)
- Modify: `backend/tutor/api/routers/resources.py` (all ownership checks)
- Modify: `backend/tutor/api/routers/jobs.py` (all ownership checks)
- Modify: `backend/tutor/api/routers/unified_ws.py` (`user_id` resolution)
- Modify: `frontend/lib/store.ts` (`getOrCreateUserId`)
- Modify: `frontend/lib/store.test.ts` (create if absent)

**Interfaces:**
- Consumes: `settings.multi_user_enabled: bool` and optional requested user IDs.
- Produces: `IdentityPolicy.resolve(requested_user_id: str | None) -> str`, constant `LOCAL_USER_ID = "local-user"`, and idempotent migration of every `u_*` owner to `local-user`.

- [ ] **Step 1: Write failing backend and frontend identity tests**

```python
def test_single_user_mode_ignores_stale_browser_identity():
    policy = IdentityPolicy(multi_user_enabled=False)
    assert policy.resolve("u_664b09a5103745d6") == "local-user"


def test_multi_user_mode_requires_identity():
    policy = IdentityPolicy(multi_user_enabled=True)
    with pytest.raises(IdentityRequired):
        policy.resolve(None)
```

```ts
it("returns the canonical identity in local mode", () => {
  localStorage.setItem("tutor-user-id", "u_stale");
  expect(getOrCreateUserId(false)).toBe("local-user");
  expect(localStorage.getItem("tutor-user-id")).toBe("local-user");
});
```

- [ ] **Step 2: Run the tests and verify the random identity behavior is exposed**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/identity/test_policy.py -v`

Expected: FAIL because `IdentityPolicy` does not exist.

Run: `npm --prefix frontend test -- --run frontend/lib/store.test.ts`

Expected: FAIL because local mode still produces or retains `u_*`.

- [ ] **Step 3: Implement one identity policy at every API boundary**

```python
LOCAL_USER_ID = "local-user"


class IdentityRequired(ValueError):
    pass


class IdentityPolicy:
    def __init__(self, multi_user_enabled: bool) -> None:
        self.multi_user_enabled = multi_user_enabled

    def resolve(self, requested_user_id: str | None) -> str:
        if not self.multi_user_enabled:
            return LOCAL_USER_ID
        if not requested_user_id:
            raise IdentityRequired("user_id is required when multi-user mode is enabled")
        return requested_user_id
```

Make all four routers call the policy before store access. In the migration transaction, update `user_id`/`owner_user_id` columns in conversations, messages, jobs, packages, profiles and learning events, preserving primary IDs and timestamps. Use `INSERT ... ON CONFLICT DO UPDATE` for repeatability.

```ts
export function getOrCreateUserId(multiUserEnabled = false): string {
  if (!multiUserEnabled) {
    localStorage.setItem("tutor-user-id", "local-user");
    return "local-user";
  }
  const existing = localStorage.getItem("tutor-user-id");
  if (existing) return existing;
  const created = `u_${crypto.randomUUID().replaceAll("-", "")}`;
  localStorage.setItem("tutor-user-id", created);
  return created;
}
```

- [ ] **Step 4: Verify policy, router authorization and migration idempotency**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/identity backend/tests/services/conversations/test_conversations_router.py backend/tests/api/test_resources_artifact_endpoint.py -v`

Expected: PASS, including a regression where `sess_ebb5a8f5dfdb` is readable with a stale `u_*` request in local mode.

Run: `npm --prefix frontend test -- --run frontend/lib/store.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/identity backend/tutor/services/migration/local_single_user.py backend/tutor/api/routers/conversations.py backend/tutor/api/routers/resources.py backend/tutor/api/routers/jobs.py backend/tutor/api/routers/unified_ws.py backend/tests/services/identity frontend/lib/store.ts frontend/lib/store.test.ts
git commit -m "fix: unify local user ownership"
```

### Task 3: Atomic conversation recovery and portable artifacts

**Files:**
- Create: `backend/tutor/services/artifacts/__init__.py`
- Create: `backend/tutor/services/artifacts/keys.py`
- Create: `backend/tests/services/artifacts/test_keys.py`
- Modify: `backend/tutor/services/conversations/schema.py` (`ConversationAggregate`)
- Modify: `backend/tutor/api/routers/conversations.py` (`get_conversation_aggregate`)
- Modify: `backend/tutor/services/resource_package/schema.py` (`ArtifactRef`)
- Modify: `backend/tutor/services/resource_package/store.py` (artifact persistence)
- Modify: `backend/tutor/api/routers/resources.py` (`get_artifact`)
- Modify: `backend/tutor/agents/resource/code_sandbox.py` (artifact output)
- Modify: `backend/tutor/services/manim_render/service.py` (artifact output)
- Modify: `frontend/lib/api.ts` (`getConversationAggregate`)
- Modify: `frontend/lib/store.ts` (`loadConversationAggregate`)
- Modify: `frontend/components/chat/ChatMessages.tsx` (non-blocking recovery notice)
- Modify: `frontend/components/resources/ResourceCard.tsx` (missing-artifact recovery action)

**Interfaces:**
- Consumes: canonical `settings.data_dir` and canonical user from Task 2.
- Produces: `to_artifact_key(path: Path, data_dir: Path) -> str`, `resolve_artifact_key(key: str, data_dir: Path) -> Path`, and one aggregate payload containing conversation, messages, jobs, packages, profile summary, path summary and `recovery_warnings: list[RecoveryWarning]`.

- [ ] **Step 1: Write failing traversal, relocation and aggregate tests**

```python
def test_artifact_key_survives_data_directory_relocation(tmp_path):
    old = tmp_path / "old"
    image = old / "artifacts" / "p1" / "figure_1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    key = to_artifact_key(image, old)
    assert key == "artifacts/p1/figure_1.png"
    assert resolve_artifact_key(key, tmp_path / "new") == tmp_path / "new" / "artifacts" / "p1" / "figure_1.png"


def test_artifact_key_rejects_parent_traversal(tmp_path):
    with pytest.raises(UnsafeArtifactKey):
        resolve_artifact_key("../secret.txt", tmp_path)
```

Add a router test that creates messages, a terminal job and two packages under one session and asserts one GET returns all records in creation order.

- [ ] **Step 2: Run focused tests and confirm absolute-path coupling**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts/test_keys.py backend/tests/services/conversations/test_conversations_router.py -v`

Expected: FAIL because artifact-key helpers and the expanded aggregate are absent.

- [ ] **Step 3: Implement safe key resolution and one-snapshot aggregation**

```python
class UnsafeArtifactKey(ValueError):
    pass


def to_artifact_key(path: Path, data_dir: Path) -> str:
    return path.resolve().relative_to(data_dir.resolve()).as_posix()


def resolve_artifact_key(key: str, data_dir: Path) -> Path:
    candidate = (data_dir / PurePosixPath(key)).resolve()
    try:
        candidate.relative_to(data_dir.resolve())
    except ValueError as exc:
        raise UnsafeArtifactKey(key) from exc
    return candidate
```

Have the aggregate router open a read transaction, verify conversation ownership once, then query messages, jobs and packages by `session_id` without applying a second browser-originated owner filter. Serialize only `artifact_key`; the artifact endpoint resolves it at request time and returns 404 for missing migrated files. Return typed recovery warnings for migrated ownership, repaired interrupted jobs and missing artifacts; the frontend shows them as dismissible notices. A missing artifact card shows “资源文件缺失” and calls the existing resource regeneration submission with the original resource contract.

- [ ] **Step 4: Verify backend recovery and frontend hydration**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/artifacts backend/tests/services/conversations backend/tests/services/resource_package backend/tests/api/test_resources_artifact_endpoint.py -v`

Expected: PASS.

Run: `npm --prefix frontend test -- --run frontend/lib/api.test.ts frontend/lib/event-handler.test.ts`

Expected: PASS with no real-network stderr.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/artifacts backend/tutor/services/conversations backend/tutor/services/resource_package backend/tutor/api/routers/conversations.py backend/tutor/api/routers/resources.py backend/tutor/agents/resource/code_sandbox.py backend/tutor/services/manim_render/service.py backend/tests/services/artifacts backend/tests/services/conversations backend/tests/services/resource_package backend/tests/api/test_resources_artifact_endpoint.py frontend/lib/api.ts frontend/lib/store.ts frontend/lib/api.test.ts frontend/lib/event-handler.test.ts frontend/components/chat/ChatMessages.tsx frontend/components/resources/ResourceCard.tsx
git commit -m "fix: restore conversations with portable artifacts"
```

### Task 4: Single-owner job terminal lifecycle

**Files:**
- Create: `backend/tutor/core/capability_result.py`
- Create: `backend/tests/core/test_capability_result.py`
- Modify: `backend/tutor/core/capability_protocol.py` (`Capability.run`)
- Modify: `backend/tutor/services/jobs/runner.py` (`JobRunner._run_job`)
- Modify: `backend/tutor/services/jobs/store.py` (`set_terminal`)
- Modify: `backend/tutor/services/jobs/schema.py` (`JobRecord`)
- Modify: `backend/tutor/capabilities/tutoring.py`
- Modify: `backend/tutor/capabilities/resource_generation.py`
- Modify: `backend/tutor/capabilities/profile.py`
- Modify: `backend/tutor/capabilities/path_planning.py`
- Modify: `backend/tutor/capabilities/assessment.py`
- Modify: `backend/tests/services/jobs/test_runner_contract.py`
- Modify: `backend/tests/services/jobs/test_terminal_idempotency.py`

**Interfaces:**
- Consumes: capability registry and `JobStore`.
- Produces: `CapabilityResult(assistant_message: str | None, payload: dict[str, Any], artifacts: tuple[ArtifactRef, ...], follow_up_tasks: tuple[FollowUpTaskSpec, ...])`; only `JobRunner` writes `result`, `error`, `done` and `job_terminal`.

- [ ] **Step 1: Write failing exactly-once terminal tests**

```python
class SuccessfulCapability:
    async def run(self, context, request):
        return CapabilityResult(payload={"answer": 42})


async def test_runner_persists_one_terminal_event(job_runner, event_store):
    job = await job_runner.submit(capability="successful", payload={})
    await job_runner.wait(job.id)
    terminals = [event for event in event_store.list(job.id) if event.type == "job_terminal"]
    assert len(terminals) == 1
    assert terminals[0].data["status"] == "succeeded"
```

Add a failure case where the capability raises after emitting progress; assert status `failed`, one terminal event, and a non-empty full error log reference.

- [ ] **Step 2: Run runner tests and reproduce the `running`-after-`done` defect**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/jobs/test_runner_contract.py backend/tests/services/jobs/test_terminal_idempotency.py -v`

Expected: FAIL because capabilities currently close the stream and the runner does not own every terminal transition.

- [ ] **Step 3: Introduce the result contract and centralize terminal writes**

```python
@dataclass(frozen=True)
class FollowUpTaskSpec:
    kind: Literal["video_render", "profile_update", "path_rebuild"]
    payload: dict[str, Any]
    dedupe_key: str


@dataclass(frozen=True)
class CapabilityResult:
    assistant_message: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[ArtifactRef, ...] = ()
    follow_up_tasks: tuple[FollowUpTaskSpec, ...] = ()
```

In `_run_job`, persist `running`, await the capability, persist the result, enqueue follow-ups, call idempotent `set_terminal(..., "succeeded")`, then publish terminal events. On exception, store the full traceback in the job log artifact, call `set_terminal(..., "failed")`, publish one error and one terminal. Remove terminal stream writes from every capability.

- [ ] **Step 4: Run all job and capability contract tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/core/test_capability_result.py backend/tests/services/jobs backend/tests/capabilities -v`

Expected: PASS; terminal count equals one for success, failure and retry paths.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/core/capability_result.py backend/tutor/core/capability_protocol.py backend/tutor/services/jobs backend/tutor/capabilities backend/tests/core/test_capability_result.py backend/tests/services/jobs backend/tests/capabilities
git commit -m "fix: make runner own job terminal state"
```

### Task 5: Durable follow-up jobs and video status projection

**Files:**
- Create: `backend/tutor/services/jobs/follow_up.py`
- Create: `backend/tests/services/jobs/test_follow_up.py`
- Modify: `backend/tutor/services/jobs/schema.py` (`parent_job_id`, `task_kind`, `dedupe_key`)
- Modify: `backend/tutor/services/jobs/store.py` (child queries and unique dedupe index)
- Modify: `backend/tutor/services/jobs/runner.py` (enqueue and resume children)
- Modify: `backend/tutor/capabilities/resource_generation.py` (return `video_render` spec)
- Modify: `backend/tutor/api/routers/jobs.py` (include children)
- Modify: `frontend/lib/types.ts` (`ClientJob.children`)
- Modify: `frontend/lib/job-reducer.ts` (child progress)
- Modify: `frontend/components/resources/VideoViewer.tsx` (terminal child state)

**Interfaces:**
- Consumes: `FollowUpTaskSpec` from Task 4.
- Produces: `FollowUpScheduler.enqueue(parent_job_id: str, specs: tuple[FollowUpTaskSpec, ...]) -> list[JobRecord]`, unique `(parent_job_id, dedupe_key)`, and parent projection fields `background_status`, `children`.

- [ ] **Step 1: Write failing persistence and restart tests**

```python
async def test_follow_up_is_idempotent_and_resumable(job_store, scheduler):
    spec = FollowUpTaskSpec("video_render", {"package_id": "pkg-1"}, "video:pkg-1")
    first = await scheduler.enqueue("parent-1", (spec,))
    second = await scheduler.enqueue("parent-1", (spec,))
    assert first[0].id == second[0].id
    assert job_store.get_children("parent-1")[0].task_kind == "video_render"
```

Extend restart stability: persist a `queued` video child, construct a fresh runner, call `resume_pending()`, and assert terminal status is persisted.

- [ ] **Step 2: Run tests and confirm in-memory fire-and-forget cannot resume**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/jobs/test_follow_up.py backend/tests/e2e/test_restart_stability.py backend/tests/capabilities/test_video_render_fire_and_forget.py -v`

Expected: FAIL because video rendering is an untracked asyncio task.

- [ ] **Step 3: Persist child jobs before returning the parent result**

```python
class FollowUpScheduler:
    async def enqueue(self, parent_job_id: str, specs: tuple[FollowUpTaskSpec, ...]) -> list[JobRecord]:
        children = []
        for spec in specs:
            children.append(self.store.create_child_if_absent(
                parent_job_id=parent_job_id,
                task_kind=spec.kind,
                dedupe_key=spec.dedupe_key,
                payload=spec.payload,
            ))
        return children
```

The main resource job succeeds after package persistence. Its video child moves independently through `queued/running/succeeded/failed`; the aggregate and job endpoints include children so refresh shows the exact state. Replace the fire-and-forget test with the durable child contract.

- [ ] **Step 4: Verify restart, API shape and VideoViewer state**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/jobs/test_follow_up.py backend/tests/e2e/test_restart_stability.py backend/tests/capabilities/test_video_render_fire_and_forget.py -v`

Expected: PASS.

Run: `npm --prefix frontend test -- --run frontend/lib/job-reducer.test.ts frontend/lib/job-reducer-stage-lifecycle.test.ts`

Expected: PASS; failed child renders “渲染失败” rather than “视频渲染中”.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/jobs backend/tutor/capabilities/resource_generation.py backend/tutor/api/routers/jobs.py backend/tests/services/jobs backend/tests/e2e/test_restart_stability.py backend/tests/capabilities/test_video_render_fire_and_forget.py frontend/lib/types.ts frontend/lib/job-reducer.ts frontend/lib/job-reducer.test.ts frontend/lib/job-reducer-stage-lifecycle.test.ts frontend/components/resources/VideoViewer.tsx
git commit -m "feat: persist resource follow-up jobs"
```

### Task 6: Unified intent routing and frontend terminal state

**Files:**
- Modify: `backend/tutor/services/intent/router.py` (`IntentDecision`)
- Modify: `backend/tutor/services/jobs/runner.py` (`submit` routing)
- Modify: `backend/tutor/api/routers/unified_ws.py` (pass explicit intent hints only)
- Modify: `backend/tutor/runtime/orchestrator.py` (delegate to intent router)
- Modify: `backend/tests/services/intent/test_router.py`
- Modify: `frontend/lib/types.ts` (`ClientJob`, `StreamEventType`)
- Modify: `frontend/lib/store.ts` (remove `activeTurn` as loading authority)
- Modify: `frontend/lib/job-reducer.ts` (`isTerminal`)
- Modify: `frontend/app/page.tsx` (header spinner)
- Modify: `frontend/components/chat/ChatMessages.tsx`
- Modify: `frontend/components/chat/JobTray.tsx`
- Modify: `frontend/components/chat/ChatMessages.test.tsx`

**Interfaces:**
- Consumes: durable job model from Tasks 4–5.
- Produces: `IntentDecision(capability: Literal["tutoring", "resource_generation", "assessment", "profile", "path_planning"], confidence: float, reason: str)` and frontend `isJobTerminal(job: ClientJob) -> boolean`.

- [ ] **Step 1: Write failing routing and spinner regression tests**

```python
@pytest.mark.parametrize(("message", "capability"), [
    ("解释一下注意力机制", "tutoring"),
    ("生成一份代码示例", "resource_generation"),
    ("给我做一次测验", "assessment"),
])
def test_router_is_the_only_capability_selector(router, message, capability):
    assert router.classify(message).capability == capability
```

```tsx
it("stops the page and queue spinners when the job terminal event arrives", () => {
  render(<WorkspaceWithJob status="succeeded" terminalAt="2026-07-17T10:00:00Z" />);
  expect(screen.queryByText("处理中")).not.toBeInTheDocument();
  expect(screen.getByText("已完成")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests and expose default resource routing and stale `activeTurn`**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/intent/test_router.py -v`

Expected: FAIL for submissions without an explicit capability.

Run: `npm --prefix frontend test -- --run frontend/components/chat/ChatMessages.test.tsx frontend/lib/job-reducer-stage-lifecycle.test.ts`

Expected: FAIL because the header still treats a successful `activeTurn` as loading.

- [ ] **Step 3: Route once and derive all UI progress from jobs**

```python
@dataclass(frozen=True)
class IntentDecision:
    capability: CapabilityName
    confidence: float
    reason: str
```

`JobRunner.submit` calls the router only when capability is absent; WebSocket and HTTP paths both use that method. `MainOrchestrator.route` becomes a thin delegation and has no separate keyword table.

```ts
export const TERMINAL_JOB_STATUSES = new Set(["succeeded", "failed", "cancelled"]);
export function isJobTerminal(job: ClientJob): boolean {
  return TERMINAL_JOB_STATUSES.has(job.status) || Boolean(job.terminalAt);
}
```

Page header, message spinner and JobTray must select the active job from `jobsById` and stop when `isJobTerminal` returns true. Keep `activeTurn` only for composer correlation until it can be removed without changing persistence shape.

- [ ] **Step 4: Verify router, TypeScript and frontend behavior**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/intent backend/tests/services/jobs -v`

Expected: PASS.

Run: `npm --prefix frontend run type-check`

Expected: PASS with zero TypeScript errors.

Run: `npm --prefix frontend test`

Expected: PASS with no attempted calls to an unmocked backend.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/intent backend/tutor/services/jobs/runner.py backend/tutor/api/routers/unified_ws.py backend/tutor/runtime/orchestrator.py backend/tests/services/intent frontend/lib/types.ts frontend/lib/store.ts frontend/lib/job-reducer.ts frontend/app/page.tsx frontend/components/chat frontend/lib/job-reducer-stage-lifecycle.test.ts
git commit -m "fix: unify routing and frontend terminal state"
```

### Task 7: Explicit multi-agent workflow graph

**Files:**
- Create: `backend/tutor/runtime/workflow_graph.py`
- Create: `backend/tests/runtime/test_workflow_graph.py`
- Modify: `backend/tutor/capabilities/resource_generation.py` (`build_resource_graph`, `run`)
- Modify: `backend/tutor/services/jobs/contracts.py` (node input/output contracts)
- Modify: `backend/tests/capabilities/test_resource_generation_capability.py`
- Modify: `backend/tests/capabilities/test_resource_generation_failed_filter.py`

**Interfaces:**
- Consumes: `CapabilityResult` from Task 4 and existing content, pedagogy, mind-map, exercise, code, video-code, reading, quality and safety agents.
- Produces: `WorkflowNode[TIn, TOut](name, dependencies, timeout_seconds, run, degrade)`, `WorkflowGraph.execute(initial: dict[str, Any]) -> WorkflowExecution`, and immutable `NodeOutcome(status: Literal["succeeded", "failed", "degraded", "skipped"], output, error_code)`.

- [ ] **Step 1: Write failing dependency, concurrency and failure-isolation tests**

```python
async def test_graph_runs_independent_resource_nodes_concurrently():
    graph = WorkflowGraph([
        WorkflowNode("source", (), 1, succeed("source"), no_degrade),
        WorkflowNode("code", ("source",), 1, timed_success("code", 0.05), no_degrade),
        WorkflowNode("exercise", ("source",), 1, timed_success("exercise", 0.05), no_degrade),
    ])
    execution = await graph.execute({})
    assert execution.outcomes["code"].status == "succeeded"
    assert execution.outcomes["exercise"].status == "succeeded"
    assert execution.elapsed_seconds < 0.09


async def test_failed_artifact_is_excluded_from_review_and_package():
    execution = await build_resource_graph(failing_nodes={"video-code"}).execute(request_context)
    assert execution.outcomes["video-code"].status == "failed"
    assert "video-code" not in execution.inputs_seen_by("quality")
    assert "video" not in execution.package_resource_kinds
```

Add tests for timeout-to-degradation, safety rejection exclusion and a graph cycle error before execution.

- [ ] **Step 2: Run workflow tests and confirm orchestration is implicit**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/runtime/test_workflow_graph.py backend/tests/capabilities/test_resource_generation_failed_filter.py -v`

Expected: FAIL because there is no typed DAG and failed branches can leak into later review/persistence stages.

- [ ] **Step 3: Implement the fixed resource DAG and node contracts**

```python
RESOURCE_DAG = (
    ("intent", ()),
    ("profile_snapshot", ("intent",)),
    ("source", ("profile_snapshot",)),
    ("pedagogy", ("source",)),
    ("mindmap", ("pedagogy",)),
    ("exercise", ("pedagogy",)),
    ("code", ("pedagogy",)),
    ("video-code", ("pedagogy",)),
    ("reading", ("pedagogy",)),
    ("quality", ("mindmap", "exercise", "code", "video-code", "reading")),
    ("safety", ("quality",)),
    ("package", ("safety",)),
)
```

`WorkflowGraph` validates missing dependencies and cycles at construction, schedules only ready nodes, uses `asyncio.TaskGroup` for ready branches, wraps each node with `asyncio.timeout`, and passes frozen copies of outputs. Each node declares an exact input/output Pydantic model and a degradation function. Quality receives only successful artifact outputs; safety-rejected outputs and failed review outputs never enter package persistence. The package node returns durable follow-ups through `CapabilityResult` instead of starting background tasks.

- [ ] **Step 4: Verify the graph and resource capability**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/runtime/test_workflow_graph.py backend/tests/capabilities/test_resource_generation_capability.py backend/tests/capabilities/test_resource_generation_failed_filter.py -v`

Expected: PASS for dependency order, independent concurrency, timeout degradation, failed-artifact exclusion and package follow-ups.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/runtime/workflow_graph.py backend/tutor/services/jobs/contracts.py backend/tutor/capabilities/resource_generation.py backend/tests/runtime/test_workflow_graph.py backend/tests/capabilities/test_resource_generation_capability.py backend/tests/capabilities/test_resource_generation_failed_filter.py
git commit -m "refactor: define explicit resource agent workflow"
```

### Task 8: Learning events, profile triggers and executable learning paths

**Files:**
- Create: `backend/tutor/api/routers/learning.py`
- Create: `backend/tests/api/test_learning_router.py`
- Modify: `backend/tutor/api/main.py` (register learning router)
- Modify: `backend/tutor/services/learning_events/schema.py` (`LearningEventType`, evidence fields)
- Modify: `backend/tutor/services/learning_events/store.py` (`append`, counts since profile)
- Modify: `backend/tutor/services/learner_profile/builder.py` (event aggregation)
- Modify: `backend/tutor/capabilities/profile.py` (return profile result)
- Modify: `backend/tutor/capabilities/path_planning.py` (produce a real path)
- Modify: `backend/tutor/services/knowledge_graph/planner.py` (mastery-aware ordering)
- Modify: `backend/tutor/services/jobs/follow_up.py` (profile/path trigger rules)
- Modify: `backend/tests/integration/test_learning_loop.py`
- Modify: `frontend/hooks/useProfile.ts`
- Modify: `frontend/components/kg/PathVisualizer.tsx`

**Interfaces:**
- Consumes: durable `profile_update` and `path_rebuild` child tasks.
- Produces: `POST /api/learning/events`, `GET /api/learning/profile/{user_id}`, `GET /api/learning/path/{user_id}`; trigger profile after 5 new scored events or an assessment completion, then trigger path rebuild after profile version changes.

- [ ] **Step 1: Write failing full-loop integration test**

```python
async def test_assessment_updates_profile_and_rebuilds_path(app_client, runner):
    for score in (0.4, 0.5, 0.6, 0.7, 0.8):
        response = await app_client.post("/api/learning/events", json={
            "user_id": "local-user",
            "session_id": "sess-loop",
            "event_type": "exercise_scored",
            "concept_id": "attention",
            "score": score,
        })
        assert response.status_code == 202
    await runner.drain()
    profile = (await app_client.get("/api/learning/profile/local-user")).json()
    path = (await app_client.get("/api/learning/path/local-user")).json()
    assert profile["version"] >= 2
    assert profile["knowledge_scores"]["attention"] > 0
    assert path["nodes"]
    assert path["profile_version"] == profile["version"]
```

- [ ] **Step 2: Run the learning loop and verify the empty profile/path result**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/integration/test_learning_loop.py backend/tests/api/test_learning_router.py -v`

Expected: FAIL because no learning router exists and `path_planning` returns no result.

- [ ] **Step 3: Implement deterministic event thresholds and path output**

```python
PROFILE_EVENT_THRESHOLD = 5


def should_update_profile(events_since_version: int, event_type: str) -> bool:
    return event_type == "assessment_completed" or events_since_version >= PROFILE_EVENT_THRESHOLD
```

The profile builder calculates per-concept exponentially weighted score, confidence from evidence count, and preferred resource formats. `path_planning.run` loads the latest profile and knowledge graph, calls the planner, persists `{profile_version, nodes, edges, rationale}`, and returns it in `CapabilityResult.payload`. The follow-up scheduler creates `path_rebuild:{profile_version}` only after a successful profile child.

- [ ] **Step 4: Verify API, store and integrated workflow**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/learning_events backend/tests/services/learner_profile backend/tests/api/test_learning_router.py backend/tests/integration/test_learning_loop.py -v`

Expected: PASS and repeat submission does not create duplicate profile/path children.

Run: `npm --prefix frontend test -- --run frontend/components/profile frontend/components/kg`

Expected: PASS; empty, loading, success and failed states are distinguishable.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/api/routers/learning.py backend/tutor/api/main.py backend/tutor/services/learning_events backend/tutor/services/learner_profile backend/tutor/capabilities/profile.py backend/tutor/capabilities/path_planning.py backend/tutor/services/knowledge_graph/planner.py backend/tutor/services/jobs/follow_up.py backend/tests/api/test_learning_router.py backend/tests/integration/test_learning_loop.py backend/tests/services/learning_events backend/tests/services/learner_profile frontend/hooks/useProfile.ts frontend/components/profile frontend/components/kg
git commit -m "feat: connect learning events profile and path"
```

### Task 9: Matplotlib headless capture and shared font cache

**Files:**
- Modify: `backend/tutor/agents/resource/code_sandbox.py` (`execute_python`, matplotlib wrapper)
- Modify: `backend/tutor/api/routers/health.py` (runtime diagnostics)
- Modify: `backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py`
- Modify: `backend/tests/agents/resource/test_code_sandbox_cjk_font.py`
- Create: `backend/tests/agents/resource/test_code_sandbox_artifacts.py`

**Interfaces:**
- Consumes: canonical artifact keys from Task 3.
- Produces: one persistent `settings.data_dir/cache/matplotlib` cache; automatic capture of every open figure as `figure_1.png`, `figure_2.png`; warning filtering only for the known Agg `show()` warning.

- [ ] **Step 1: Write failing multi-figure and cache-reuse tests**

```python
def test_matplotlib_show_captures_all_figures_without_interactive_warning(sandbox, tmp_path):
    result = sandbox.execute_python("""
import matplotlib.pyplot as plt
plt.figure(); plt.plot([1, 2])
plt.figure(); plt.scatter([1], [2])
plt.show()
""")
    assert [a.name for a in result.artifacts] == ["figure_1.png", "figure_2.png"]
    assert "FigureCanvasAgg is non-interactive" not in result.stderr
    assert all(not Path(a.artifact_key).is_absolute() for a in result.artifacts)
```

Run the sandbox twice and assert both runs receive the identical `MPLCONFIGDIR` and the second stderr lacks the font-cache build message.

- [ ] **Step 2: Run focused sandbox tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_code_sandbox_matplotlib_drain.py backend/tests/agents/resource/test_code_sandbox_cjk_font.py backend/tests/agents/resource/test_code_sandbox_artifacts.py -v`

Expected: FAIL because each run creates a temporary Matplotlib cache and `show()` is not converted into artifact capture consistently.

- [ ] **Step 3: Inject a headless prelude and persist cache configuration**

```python
MATPLOTLIB_PRELUDE = r"""
import os
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib.pyplot as _tutor_plt
def _tutor_capture_figures():
    for _index, _number in enumerate(_tutor_plt.get_fignums(), start=1):
        _figure = _tutor_plt.figure(_number)
        _figure.savefig(f"figure_{_index}.png", bbox_inches="tight", dpi=160)
_tutor_plt.show = _tutor_capture_figures
"""
```

Set `MPLCONFIGDIR` to `settings.data_dir / "cache" / "matplotlib"`, create it once, append a `finally` capture for scripts that never call `show()`, de-duplicate identical figure files, and retain all unrelated warnings. Health output reports the Conda Python executable, Matplotlib version, backend and writable cache path.

- [ ] **Step 4: Verify sandbox and real Conda runtime**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_code_sandbox_*.py -v`

Expected: PASS.

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -c "import matplotlib; print(matplotlib.get_backend()); print(matplotlib.get_cachedir())"`

Expected: backend resolves to `Agg`; application test confirms its cache is under `data/cache/matplotlib`.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/agents/resource/code_sandbox.py backend/tutor/api/routers/health.py backend/tests/agents/resource
git commit -m "fix: capture matplotlib output headlessly"
```

### Task 10: In-page image lightbox with zoom and pan

**Files:**
- Create: `frontend/components/resources/ImageLightbox.tsx`
- Create: `frontend/components/resources/ImageLightbox.test.tsx`
- Modify: `frontend/components/resources/CodeViewer.tsx` (open image artifacts)
- Modify: `frontend/components/resources/ResourceCard.tsx` (image sizing)
- Modify: `frontend/app/globals.css` (lightbox and responsive image rules)

**Interfaces:**
- Consumes: artifact endpoint URLs from Task 3.
- Produces: `ImageLightboxProps { images: ImageArtifact[]; initialIndex: number; open: boolean; onOpenChange(open: boolean): void }`; zoom range 25%–500%, wheel/pinch zoom, drag pan, reset, previous/next, full-size download.

- [ ] **Step 1: Write failing interaction tests**

```tsx
it("opens an image without constraining it to the resource card", async () => {
  const user = userEvent.setup();
  render(<CodeViewer resource={resourceWithTwoImages} />);
  await user.click(screen.getByRole("button", { name: "查看 figure_1.png" }));
  expect(screen.getByRole("dialog", { name: "图片查看器" })).toBeVisible();
  expect(screen.getByText("100%")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "放大" }));
  expect(screen.getByText("125%")).toBeVisible();
});
```

Add keyboard assertions for `Escape`, `ArrowLeft`, `ArrowRight`, `+`, `-`, and reset.

- [ ] **Step 2: Run component tests and confirm no in-page viewer exists**

Run: `npm --prefix frontend test -- --run frontend/components/resources/ImageLightbox.test.tsx`

Expected: FAIL because `ImageLightbox` is absent.

- [ ] **Step 3: Implement accessible overlay and natural card sizing**

```tsx
const MIN_SCALE = 0.25;
const MAX_SCALE = 5;
const SCALE_STEP = 0.25;

export function clampScale(value: number) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}
```

Render the selected image in a fixed dialog overlay with `transform: translate(x, y) scale(scale)`, pointer capture for pan, wheel zoom centered at the cursor, focus trapping and labeled controls. Card previews use `max-height` and `object-contain` without a fixed aspect ratio; clicking opens the overlay instead of a new tab.

- [ ] **Step 4: Verify component and resource-page regressions**

Run: `npm --prefix frontend test -- --run frontend/components/resources/ImageLightbox.test.tsx frontend/app/resources/page.test.tsx`

Expected: PASS.

Run: `npm --prefix frontend run type-check`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/resources/ImageLightbox.tsx frontend/components/resources/ImageLightbox.test.tsx frontend/components/resources/CodeViewer.tsx frontend/components/resources/ResourceCard.tsx frontend/app/globals.css
git commit -m "feat: add zoomable image viewer"
```

### Task 11: Python code exercise submission and persistence

**Files:**
- Create: `backend/tutor/services/exercise_attempts/__init__.py`
- Create: `backend/tutor/services/exercise_attempts/schema.py`
- Create: `backend/tutor/services/exercise_attempts/store.py`
- Create: `backend/tutor/api/routers/exercises.py`
- Create: `backend/tests/api/test_exercises_router.py`
- Create: `backend/tests/services/exercise_attempts/test_store.py`
- Modify: `backend/tutor/api/main.py` (register exercise router)
- Modify: `backend/tutor/services/resource_package/schema.py` (`CodeSpec` on code questions)
- Modify: `backend/tutor/prompts/resource/zh/exercise_generator.yaml` (required code schema)
- Modify: `backend/tutor/agents/resource/code_sandbox.py` (submission execution entry point)
- Create: `frontend/components/resources/CodeExerciseEditor.tsx`
- Create: `frontend/components/resources/CodeExerciseEditor.test.tsx`
- Modify: `frontend/components/resources/ExerciseViewer.tsx`
- Modify: `frontend/lib/api.ts` (submit/list attempts)
- Modify: `frontend/lib/types.ts` (`CodeSpec`, `ExerciseAttempt`)

**Interfaces:**
- Consumes: canonical identity and existing restricted code sandbox.
- Produces: `CodeSpec(language: Literal["python"], starter_code: str, tests: list[CodeTestCase], time_limit_seconds: int = 5)`, `POST /api/exercises/{package_id}/{question_id}/attempts`, and `GET` attempt history.

- [ ] **Step 1: Write failing hidden-test and upload tests**

```python
async def test_python_attempt_runs_tests_and_persists_result(client):
    response = await client.post("/api/exercises/pkg-1/q-code/attempts", json={
        "user_id": "local-user",
        "session_id": "sess-code",
        "source_code": "def add(a, b): return a + b",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "passed"
    assert body["passed_tests"] == 2
    assert body["source_code"] == "def add(a, b): return a + b"
```

```tsx
it("accepts a .py file and submits its contents", async () => {
  const user = userEvent.setup();
  render(<CodeExerciseEditor question={codeQuestion} />);
  await user.upload(screen.getByLabelText("上传 Python 文件"), new File(["print(1)"], "answer.py", { type: "text/x-python" }));
  expect(screen.getByRole("textbox")).toHaveValue("print(1)");
  await user.click(screen.getByRole("button", { name: "运行并提交" }));
  expect(await screen.findByText("全部测试通过")).toBeVisible();
});
```

- [ ] **Step 2: Run API and UI tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/api/test_exercises_router.py backend/tests/services/exercise_attempts/test_store.py -v`

Expected: FAIL because attempt schema, store and endpoints do not exist.

Run: `npm --prefix frontend test -- --run frontend/components/resources/CodeExerciseEditor.test.tsx`

Expected: FAIL because code questions have no editor.

- [ ] **Step 3: Implement strict schema, sandbox tests and durable attempts**

```python
class CodeTestCase(BaseModel):
    name: str
    call: str
    expected_json: Any


class CodeSpec(BaseModel):
    language: Literal["python"] = "python"
    starter_code: str
    tests: list[CodeTestCase] = Field(min_length=1)
    time_limit_seconds: int = Field(default=5, ge=1, le=10)
```

The endpoint loads the question from the package instead of accepting tests from the client, validates a `.py` text payload up to 128 KiB, executes each test in the restricted sandbox, persists source, stdout, stderr, per-test outcomes, duration and terminal status, then appends an `exercise_scored` learning event. The frontend uses a monospace textarea, file input restricted to `.py`, explicit running state, test table and previous attempt list.

- [ ] **Step 4: Verify attempts, learning event and UI**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/exercise_attempts backend/tests/api/test_exercises_router.py backend/tests/services/learning_events -v`

Expected: PASS for passed, failed, timeout, syntax-error and repeat-attempt cases.

Run: `npm --prefix frontend test -- --run frontend/components/resources/CodeExerciseEditor.test.tsx`

Expected: PASS for editor, `.py` upload, submit, failure display and history restore.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/exercise_attempts backend/tutor/api/routers/exercises.py backend/tutor/api/main.py backend/tutor/services/resource_package/schema.py backend/tutor/prompts/resource/zh/exercise_generator.yaml backend/tutor/agents/resource/code_sandbox.py backend/tests/api/test_exercises_router.py backend/tests/services/exercise_attempts frontend/components/resources/CodeExerciseEditor.tsx frontend/components/resources/CodeExerciseEditor.test.tsx frontend/components/resources/ExerciseViewer.tsx frontend/lib/api.ts frontend/lib/types.ts
git commit -m "feat: support Python code exercise attempts"
```

### Task 12: Manim preflight, meaningful retries and complete failures

**Files:**
- Modify: `backend/tutor/services/manim_render/static_guard.py` (external asset AST scan)
- Modify: `backend/tutor/services/manim_render/code_retry.py` (retry hash and feedback)
- Modify: `backend/tutor/services/manim_render/executor.py` (UTF-8 full logs)
- Modify: `backend/tutor/services/manim_render/service.py` (error model and artifact key)
- Modify: `backend/tutor/agents/resource/manim_video.py` (prompt/asset policy)
- Modify: `backend/tutor/prompts/resource/zh/manim_video.yaml` (self-contained code)
- Modify: `backend/tests/services/manim_render/test_static_guard.py`
- Modify: `backend/tests/services/manim_render/test_code_retry.py`
- Modify: `backend/tests/services/manim_render/test_service.py`
- Modify: `frontend/components/resources/VideoViewer.tsx` (failure details and retry action)

**Interfaces:**
- Consumes: durable `video_render` child job from Task 5.
- Produces: `StaticGuardResult.external_assets: tuple[str, ...]`, `RenderFailure(error_code, summary, traceback_tail, log_artifact_key)`, and retry rejection when generated source hash is unchanged.

- [ ] **Step 1: Write failing regression around the supplied `SVGMobject` code**

```python
def test_guard_rejects_missing_literal_svg_assets(tmp_path):
    code = 'from manim import *\nclass MainScene(Scene):\n def construct(self):\n  self.add(SVGMobject("person_silhouette.svg"))'
    result = guard_manim_source(code, workdir=tmp_path)
    assert result.ok is False
    assert result.external_assets == ("person_silhouette.svg",)
    assert result.error_code == "missing_external_asset"
```

Add a retry test returning identical code twice; assert the second attempt ends with `unchanged_retry` and does not launch Manim again. Add a service failure whose root cause is at the traceback tail and assert it is preserved.

- [ ] **Step 2: Run Manim service tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render -v`

Expected: FAIL because external SVG references pass preflight, identical retries execute, and traceback storage truncates the root cause.

- [ ] **Step 3: Implement asset-aware AST checks and structured failure output**

```python
ASSET_CALLS = {"SVGMobject", "ImageMobject"}


def collect_literal_assets(tree: ast.AST) -> tuple[str, ...]:
    assets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node.func) in ASSET_CALLS and node.args:
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                assets.append(node.args[0].value)
    return tuple(dict.fromkeys(assets))
```

Reject absent literal assets before render. Prompt generation to use native Manim primitives unless an asset is included in the package. Hash normalized code before every retry; pass the structured `error_code`, last 120 traceback lines and required correction to the model; stop if the hash repeats. Store complete stdout/stderr as UTF-8 log artifacts and display concise summary plus expandable tail in `VideoViewer` with a user-triggered retry button that creates a new child job.

- [ ] **Step 4: Verify unit tests and a real minimal render**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/manim_render backend/tests/agents/resource/test_manim_video_inline_resource.py -v`

Expected: PASS.

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m manim --version`

Expected: exit 0 and prints the installed Manim version.

Run one fixture scene through `ManimRenderService`; expected: child status `succeeded`, MP4 artifact exists and has non-zero size. Run the missing-SVG fixture; expected: child status `failed` with `missing_external_asset` and no permanent “渲染中” state.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/manim_render backend/tutor/agents/resource/manim_video.py backend/tutor/prompts/resource/zh/manim_video.yaml backend/tests/services/manim_render backend/tests/agents/resource/test_manim_video_inline_resource.py frontend/components/resources/VideoViewer.tsx
git commit -m "fix: make Manim rendering diagnosable"
```

### Task 13: Per-conversation web search policy and UI toggle

**Files:**
- Create: `backend/tutor/services/search/__init__.py`
- Create: `backend/tutor/services/search/policy.py`
- Create: `backend/tests/services/search/test_policy.py`
- Modify: `backend/tutor/services/conversations/schema.py` (`web_search_enabled`)
- Modify: `backend/tutor/services/conversations/store.py` (persist setting)
- Modify: `backend/tutor/api/routers/conversations.py` (`PATCH` setting)
- Modify: `backend/tutor/services/jobs/schema.py` (`web_search_enabled` snapshot)
- Modify: `backend/tutor/services/jobs/runner.py` (copy conversation setting at submission)
- Modify: `backend/tutor/capabilities/tutoring.py` (gated tool access)
- Modify: `backend/tutor/capabilities/resource_generation.py` (gated tool access)
- Create: `frontend/components/chat/WebSearchToggle.tsx`
- Create: `frontend/components/chat/WebSearchToggle.test.tsx`
- Modify: `frontend/components/chat/ChatComposer.tsx`
- Modify: `frontend/lib/api.ts` (`setConversationWebSearch`)
- Modify: `frontend/lib/store.ts` (hydrate and mutate setting)
- Modify: `frontend/lib/types.ts` (`webSearchEnabled`)

**Interfaces:**
- Consumes: conversation aggregate and existing web-search tools.
- Produces: `SearchPolicy.allowed(conversation_enabled: bool, runtime_enabled: bool) -> bool`, `PATCH /api/conversations/{session_id}/settings { web_search_enabled: boolean }`, and an immutable per-job search flag.

- [ ] **Step 1: Write failing default-off, persistence and gate tests**

```python
def test_search_policy_requires_both_conversation_and_runtime_flags():
    policy = SearchPolicy()
    assert policy.allowed(False, True) is False
    assert policy.allowed(True, False) is False
    assert policy.allowed(True, True) is True


async def test_new_conversation_search_defaults_off(client):
    created = (await client.post("/api/conversations", json={"user_id": "local-user"})).json()
    assert created["web_search_enabled"] is False
```

```tsx
it("persists the switch for this conversation and resets for a new one", async () => {
  const user = userEvent.setup();
  render(<ComposerHarness />);
  await user.click(screen.getByRole("switch", { name: "联网搜索" }));
  expect(mockSetConversationWebSearch).toHaveBeenCalledWith("sess-current", true);
  await user.click(screen.getByRole("button", { name: "新建对话" }));
  expect(screen.getByRole("switch", { name: "联网搜索" })).not.toBeChecked();
});
```

- [ ] **Step 2: Run backend and frontend tests**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/search/test_policy.py backend/tests/services/conversations/test_conversations_router.py -v`

Expected: FAIL because the conversation has no persisted search preference.

Run: `npm --prefix frontend test -- --run frontend/components/chat/WebSearchToggle.test.tsx`

Expected: FAIL because the switch is absent.

- [ ] **Step 3: Persist the setting and gate tool registration**

```python
class SearchPolicy:
    def allowed(self, conversation_enabled: bool, runtime_enabled: bool) -> bool:
        return bool(conversation_enabled and runtime_enabled)
```

At job creation, read conversation settings and store `web_search_enabled` on the job so mid-run UI changes do not alter behavior. Tutoring/resource capabilities register or invoke web tools only when the snapshot and runtime service flag are true; result metadata records `search_used`, title, URL, excerpt, provider and retrieval time. Catch provider timeout/unavailability as a stage-level `WEB_SEARCH_UNAVAILABLE` outcome, persist the degradation notice, and continue with model knowledge or RAG without failing the main job. The toggle calls the PATCH endpoint optimistically, rolls back on failure, is hydrated by the aggregate, and a newly created conversation starts false.

- [ ] **Step 4: Verify persistence, capability gating and UI**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/services/search backend/tests/services/conversations backend/tests/capabilities/test_tutoring_capability.py backend/tests/capabilities/test_resource_generation_capability.py -v`

Expected: PASS; disabled jobs never call the mocked search tool, enabled jobs include sources, and provider failure emits `WEB_SEARCH_UNAVAILABLE` while the parent job succeeds.

Run: `npm --prefix frontend test -- --run frontend/components/chat/WebSearchToggle.test.tsx frontend/lib/api.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor/services/search backend/tutor/services/conversations backend/tutor/api/routers/conversations.py backend/tutor/services/jobs backend/tutor/capabilities/tutoring.py backend/tutor/capabilities/resource_generation.py backend/tests/services/search backend/tests/services/conversations backend/tests/capabilities frontend/components/chat/WebSearchToggle.tsx frontend/components/chat/WebSearchToggle.test.tsx frontend/components/chat/ChatComposer.tsx frontend/lib/api.ts frontend/lib/store.ts frontend/lib/types.ts
git commit -m "feat: add conversation web search control"
```

### Task 14: UTF-8 normalization, type cleanup and clean test output

**Files:**
- Modify: `backend/tutor/services/jobs/schema.py`
- Modify: `backend/tutor/services/resource_package/schema.py`
- Modify: `backend/tutor/services/manim_render/executor.py`
- Modify: `backend/tutor/agents/resource/code_sandbox.py`
- Create: `backend/tutor/services/logging/__init__.py`
- Create: `backend/tutor/services/logging/redaction.py`
- Create: `backend/tests/services/logging/test_redaction.py`
- Modify: `frontend/lib/types.ts`
- Modify: `frontend/lib/event-handler.ts`
- Modify: `frontend/lib/event-handler.test.ts`
- Modify: `frontend/lib/job-reducer-stage-lifecycle.test.ts`
- Modify: `frontend/components/chat/ChatMessages.tsx`
- Modify: `frontend/components/chat/ChatMessages.test.tsx`
- Modify: `frontend/components/layout/Sidebar.tsx`
- Modify: `frontend/hooks/useJobQueue.ts`
- Modify: `frontend/lib/store.ts`

**Interfaces:**
- Consumes: canonical `ClientJob`, `ArtifactRef`, `ResourcePackage`, `StreamEventType`, `CapabilityResult` and `RenderFailure` types from Tasks 3–13.
- Produces: zero TypeScript errors, no duplicate test object keys, no unmocked network calls, UTF-8-safe subprocess/database text, and `redact_sensitive(value: Mapping[str, Any]) -> dict[str, Any]` for API keys, hidden tests, private reasoning fields and full user code.

- [ ] **Step 1: Record the current compiler and stderr failures as regression assertions**

```ts
it("does not call persistence when append is supplied by the harness", async () => {
  const append = vi.fn().mockResolvedValue(undefined);
  await handleStreamEvent(resourceEvent, createHandlerContext({ appendConversationMessage: append }));
  expect(append).toHaveBeenCalledOnce();
  expect(global.fetch).not.toHaveBeenCalled();
});
```

Add Python encoding assertions with Chinese stderr/stdout and verify round-trip equality after process execution and store retrieval.

```python
def test_redaction_removes_secrets_hidden_tests_and_full_source():
    redacted = redact_sensitive({
        "api_key": "sk-secret",
        "hidden_tests": [{"call": "answer()"}],
        "private_reasoning": "internal",
        "source_code": "print('user answer')",
        "error_code": "FAILED_TEST",
    })
    assert redacted == {
        "api_key": "[REDACTED]",
        "hidden_tests": "[REDACTED]",
        "private_reasoning": "[REDACTED]",
        "source_code": "[REDACTED:20 chars]",
        "error_code": "FAILED_TEST",
    }
```

- [ ] **Step 2: Run the full static and focused encoding checks**

Run: `npm --prefix frontend run type-check`

Expected before fixes: FAIL with the recorded `ClientJob`, nullable events, course, retrieval scope, stream event, resource package and error-shape mismatches.

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests/agents/resource/test_code_sandbox_encoding.py backend/tests/services/manim_render/test_executor.py backend/tests/services/logging/test_redaction.py -v`

Expected: the new Chinese round-trip assertion fails wherever platform-default decoding remains.

- [ ] **Step 3: Collapse duplicate shapes into canonical types and force UTF-8**

```ts
export type StreamEventType =
  | "progress"
  | "stage"
  | "message"
  | "resource"
  | "result"
  | "error"
  | "job_terminal";

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
```

Use these types at API parsing, store, reducer and components; remove duplicate `source`/`stage` keys in fixtures; represent backend errors consistently as `{ code: string; message: string; details?: unknown }`. Pass `encoding="utf-8", errors="replace"` to subprocess capture and use UTF-8 for every JSON/text file operation. Apply `redact_sensitive` before structured job/error logging and retain full user code only in the access-controlled attempt store. Mock persistence adapters in event-handler tests instead of reaching port 8000.

- [ ] **Step 4: Run complete backend and frontend suites**

Run: `npm --prefix frontend run type-check`

Expected: PASS with zero errors.

Run: `npm --prefix frontend test`

Expected: PASS with no network-error stderr and no duplicate-key warnings.

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tutor backend/tests frontend/lib frontend/components frontend/app
git diff --cached --check
git commit -m "fix: align contracts and UTF-8 handling"
```

### Task 15: Execute migration and browser-level acceptance

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/reliability.spec.ts`
- Create: `docs/operations/local-data-migration.md`
- Modify: `frontend/package.json` (Playwright scripts)
- Modify: `README.md` (startup, migration and acceptance commands)
- Runtime data only after backup: `data/` via the migration CLI.

**Interfaces:**
- Consumes: every public API and UI behavior from Tasks 1–14.
- Produces: an auditable backup path, a migrated `local-user` dataset, reproducible E2E suite, and operational recovery instructions.

- [ ] **Step 1: Write the browser acceptance flow before migrating real data**

```ts
test("conversation and resources survive refresh and terminal states settle", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "消息" }).fill("生成一个包含折线图的 Python 示例");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText("已完成")).toBeVisible({ timeout: 120_000 });
  await expect(page.getByRole("button", { name: /查看 figure_1.png/ })).toBeVisible();
  await page.reload();
  await expect(page.getByText("生成一个包含折线图的 Python 示例")).toBeVisible();
  await expect(page.getByText("已完成")).toBeVisible();
  await expect(page.getByRole("button", { name: /查看 figure_1.png/ })).toBeVisible();
});
```

Add flows for `sess_ebb5a8f5dfdb` recovery, image zoom, `.py` upload and result restore, missing-SVG video failure, successful minimal video, profile/path creation, search default-off and per-session persistence. Run the core flow once at a desktop viewport and once at a 390×844 mobile viewport.

- [ ] **Step 2: Run E2E before final migration and confirm the fixtures identify missing behavior**

Run backend: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m tutor.api.run_server`

Run frontend: `npm --prefix frontend run dev`

Run tests: `npm --prefix frontend run test:e2e`

Expected before the final data operation: fixture-based tests pass; the real-session recovery test remains skipped through an explicit `TUTOR_E2E_REAL_DATA=1` guard.

- [ ] **Step 3: Document and execute the two-phase real-data migration**

```powershell
E:\Anaconda3\anaconda\envs\tutor\python.exe -m tutor.cli.main migrate-local-data --repo-root E:\github\TutorBot --target-user-id local-user --dry-run
E:\Anaconda3\anaconda\envs\tutor\python.exe -m tutor.cli.main migrate-local-data --repo-root E:\github\TutorBot --target-user-id local-user
```

The operations document records the printed backup directory, source directories, database row counts before/after, artifact copy counts, rollback command that restores the timestamped backup to a separate recovery directory, and verification queries for `sess_ebb5a8f5dfdb`. Do not delete either source data directory.

- [ ] **Step 4: Run the complete acceptance matrix**

Run: `E:\Anaconda3\anaconda\envs\tutor\python.exe -m pytest backend/tests -q`

Expected: PASS.

Run: `npm --prefix frontend run type-check`

Expected: PASS.

Run: `npm --prefix frontend test`

Expected: PASS without backend connection errors.

Run: `npm --prefix frontend run build`

Expected: PASS with a production Next.js build and no type or prerender errors.

Run: `$env:TUTOR_E2E_REAL_DATA='1'; npm --prefix frontend run test:e2e`

Expected: PASS for refresh/restart history, old session/resource recovery, terminal queue, Matplotlib viewer, code attempts, Manim success/failure, profile/path and web-search settings.

Restart both services and rerun the refresh/recovery test. Expected: messages, resources, exercise attempts, child task outcomes, profile and path remain visible; no job remains `running` after a terminal event.

- [ ] **Step 5: Commit documentation and acceptance tests**

```bash
git add frontend/playwright.config.ts frontend/e2e/reliability.spec.ts frontend/package.json docs/operations/local-data-migration.md README.md
git commit -m "test: cover end-to-end reliability workflow"
```

---

## Final Review Gate

- [ ] Confirm `git status --short` contains no unintended runtime outputs, Manim cache files, SQLite journals or generated media.
- [ ] Confirm `git diff --check` reports no whitespace errors.
- [ ] Confirm every job fixture has exactly one terminal event and every child job has a visible success/failure state.
- [ ] Confirm the migration backup path exists and source datasets remain untouched.
- [ ] Confirm `sess_ebb5a8f5dfdb` loads through the aggregate endpoint as `local-user` after a process restart.
- [ ] Confirm all seven original problem groups are covered by at least one automated acceptance assertion.
