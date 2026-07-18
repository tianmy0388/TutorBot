# Local Validation Follow-up Reliability Design

**Date:** 2026-07-19

**Branch:** `codex/tutorbot-reliability`

**Status:** Approved for implementation

## Context

Local validation exposed a second set of failures at the boundary between live
job events, durable resource data, and restored frontend state:

- a completed job removes its visible Agent/stage progress;
- some generated Mermaid mindmaps contain invalid quoted nodes;
- text-only code examples preload Matplotlib and emit misleading font-cache
  diagnostics, while the resource contract cannot say whether a figure was
  expected;
- streamed exercise options are replaced with `[TRUNCATED]`, and exercise
  drafts disappear when their component unmounts;
- Manim retries can corrupt otherwise valid source with ambiguous substring
  patches;
- draft sessions, stale job rows, and unowned Markdown image references produce
  avoidable 404 responses.

The fixes below retain the current capability/Agent topology. They strengthen
the deterministic contracts around Agent output instead of replacing the
multi-Agent design.

## Product decisions

1. An unsubmitted exercise answer is a durable draft. It can be restored after
   navigation or refresh, but it does not affect the learner profile.
2. Only an explicit submission is scored and published as learning evidence.
3. A failed Manim render is terminal and visible. Automatic LLM substring
   patching is not used to hide the failure.
4. The video resource exposes an **智能修复并重新渲染** action. It sends the
   complete failed source and sanitised render diagnostics to the LLM, asks for
   a complete replacement program, validates it, and then renders it as a
   durable background job.
5. A failed regeneration never overwrites the last playable video or the last
   syntactically valid source.

## 1. Durable workflow feedback

### Canonical workflow snapshot

When a root job becomes terminal, the frontend reducer derives one structured
snapshot from the event timeline:

```text
job_id
capability
terminal_status
duration_seconds
stages[] = { name, status: completed | failed | incomplete }
partial_resources[]
```

The snapshot is attached to a stable `workflow_timeline` assistant message.
The message id is derived from `job_id`, so terminal replay is idempotent. The
same message is inserted into the current Zustand state immediately and
persisted to the conversation API. Aggregate restoration renders the same
metadata rather than reconstructing a different view.

Terminal jobs are never treated as live jobs. Their spinner stops, while the
snapshot remains as a collapsible completed/failed workflow card.

### Session and job convergence

- A newly minted client session is marked as a draft. Mount-time aggregate load
  is attempted only for a restored durable session.
- Aggregate 404 for a restored stale id converts it back to a draft without a
  console error storm; other HTTP failures remain visible.
- Job deletion removes the row atomically from both the queue hook and Zustand.
  Backend 404 is treated as an idempotent delete success.

## 2. Safe public resource projection

The generic public redactor currently truncates exercise option values at its
depth limit. Resource events and terminal package payloads will use a
schema-aware projection:

1. validate the payload as `Resource`/`ResourcePackage`;
2. create a detached public dump;
3. recursively scrub sensitive keys and credential-shaped strings without
   replacing valid schema fields because of nesting depth;
4. retain explicit item and string-size bounds.

The frontend validates streamed resources before replacing a canonical
resource. Exercise options that are non-objects, empty, or exactly
`[TRUNCATED]` are rejected and recovered from REST detail. Option React keys use
question id, option label, and position as a defensive composite key.

## 3. Mermaid and Markdown media

### Mermaid

- The multimedia prompt forbids standalone quoted mindmap nodes.
- A backend mindmap normaliser converts unsafe labels to deterministic shaped
  nodes such as `node_4["激活函数 a=σ(z)"]`, while preserving already valid
  `root((...))` and shaped nodes.
- The resource stores a structured outline alongside `mermaid_dsl`. This is the
  canonical text fallback if Mermaid rendering fails.
- The viewer clears stale errors before each render, replaces raw parser blobs
  with a concise message, and displays the outline on failure.

### Markdown images

Generated relative image references are usable only when they resolve to an
owned, registered resource artifact. The generation/assembly boundary replaces
an unowned reference with a visible “图片未提供” description. The frontend image
renderer repeats that check so a legacy resource cannot trigger requests such
as `/dyna_diagram.png`.

## 4. Code example output contract

`CodeResource` gains `output_kind: text | figure`.

- The prompt selects `figure` only when a visual output is pedagogically part
  of the example and must then include real plotting code.
- Python AST inspection determines whether the code imports/uses Matplotlib.
- Text-only execution uses a minimal wrapper and does not import Matplotlib or
  NumPy for dependency probing.
- Figure execution installs the existing Agg capture hook and persists every
  captured figure as an owned artifact.
- A successful `figure` execution with no image produces the typed failure
  `FIGURE_EXPECTED_BUT_NOT_PRODUCED` and is eligible for one generation repair.
- Only exact runner-owned font-cache/Agg messages are filtered. User warnings
  are preserved.

The viewer treats failed/timeout execution as an error, successful execution
with user warnings as a warning, and successful text output with no image as a
normal result.

## 5. Durable exercises and learning feedback

### Storage model

A general exercise-response store persists:

- current draft per `(user_id, package_id, resource_id, question_id)`;
- immutable submitted attempts;
- question type, session/course/concept context, submitted answer, score,
  correctness, timestamps, and optional linked code-attempt id;
- an event-publication watermark for crash repair.

Draft writes are upserts and never publish learning events. Submission is
idempotent through a client submission id.

### API and UI

- `GET` loads draft and submitted state for a resource/question.
- `PUT` saves a draft.
- `POST` submits and scores an answer on the server.
- Choice, multiple choice, true/false, fill, and short-answer questions use the
  same flow. Code questions keep the safe code runner and link its terminal
  attempt to the general response.
- The viewer debounces draft persistence, restores it after navigation, and
  shows the latest submitted state plus attempt history.

The public pre-submission projection does not need to expose the canonical
answer. Submission returns the correctness/explanation required by the UI.

### Feedback loop

Every explicit terminal submission publishes one deterministic
`EXERCISE_SCORED` event. Assessment already reads these events directly.
Tutoring additionally receives a bounded recent-exercise evidence summary so
it can respond to a misconception before the next batched profile update.

The first scored exercise for a user schedules profile creation/update and a
learning-path build. Later evidence retains the existing bounded batching
policy. This provides visible first-use behaviour without rebuilding the path
after every click.

## 6. User-triggered Manim regeneration

### Initial render

The initial generated program runs static validation and one normal render.
Failure is persisted immediately with a safe summary, a private log artifact,
the failed source version, and `render_status=failed`. Both the workbench and
resource centre show that terminal state.

### Intelligent repair action

The video resource action creates a durable child job with an idempotency key
bound to the resource and failed source version. The repair prompt contains:

- the complete failed Manim program;
- the scene class and installed Manim/Python versions;
- a bounded, sanitised traceback tail and stable failure code;
- explicit constraints learned from preflight (valid Manim Community APIs,
  positive run times, available constants/fonts/assets, no external files);
- an instruction to return one complete replacement program, not a patch.

The returned program must pass:

1. source extraction and normalisation;
2. AST parsing and `py_compile`;
3. sandbox/security checks;
4. Manim-specific deterministic checks, including bound method objects inside
   `VGroup`, non-positive literal run times, missing assets, and unavailable
   imported Manim names;
5. the real render.

Validation failure can be included in one bounded internal regeneration retry;
the user still sees one durable repair job. A successful render publishes a new
source/video version and updates the resource. A failed repair retains its
source and log as a diagnostic version but leaves the last playable version
unchanged. Manual retry starts from the latest syntactically valid failed
version and its latest failure, not the original program.

Refresh/restart recovery uses the existing durable child-job mechanism.

## 7. Delivery strategy

To shorten elapsed time without weakening tests, implementation is split into
independent workstreams:

1. frontend workflow/session/job convergence;
2. Mermaid, Markdown media, and code-output contract;
3. exercise persistence and learning feedback;
4. Manim regeneration pipeline and video UI.

Each workstream starts with focused failing tests, then implementation and a
local review. Shared-schema changes land before dependent frontend work.

## 8. Verification

Required evidence before completion:

- backend unit/integration suites for public projection, resource generation,
  exercise response/event repair, and Manim regeneration;
- frontend component/reducer/API tests for terminal stages, resource recovery,
  drafts, code output, video retry, and idempotent deletion;
- Mermaid 11.14 parse coverage for the reported quoted-node input;
- real execution using the local `tutor` conda interpreter for Matplotlib and
  Manim smoke cases;
- refresh/navigation/restart recovery tests;
- the existing MiniMax MCP web-search acceptance test;
- a focused local end-to-end run covering resource generation, exercise submit,
  failed video, intelligent repair, and restored history.
