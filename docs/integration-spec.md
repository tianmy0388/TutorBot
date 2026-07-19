# TutorBot Local + Upstream Integration Specification

## Authority

- Integration source: `upstream/main@589fb728b4f0814ab06d69746996de81759c95d1`.
- Product structure, visual language, student-facing copy and navigation follow `main-front-end`.
- Reliability, persistence, public projections, recovery and security contracts follow the upstream snapshot.
- Shared capabilities are implemented once. Compatibility routes delegate to the canonical services.

## Preserve Locally

- Headspace-led shell and Notion-style knowledge surfaces defined in `docs/brand-spec.md`.
- Noto Sans SC interface typography, relaxed Chinese tracking and achromatic dark mode.
- Home, document-style learning workspace, knowledge library, resource gallery and side details.
- Course-backed RAG, teacher analytics, `ai_introduction` default course and current knowledge bases.
- Ports `8000` and `3010`, Windows start/stop scripts, Spark and local-hash compatibility.
- Existing REST/WebSocket paths, persistent browser keys, partial-result recovery and `submission/`.

## Import From Upstream

- Single terminal owner, CAS, replay-safe jobs, durable follow-up tasks and workflow snapshots.
- Structured redaction, portable artifact keys and bounded public resource projections.
- Durable learning events, profile watermarks and persisted learning paths.
- General exercise drafts/submissions, Python exercise attempts and server-owned scoring events.
- Per-conversation web-search policy and immutable job submission snapshots.
- Resource validation, image lightbox, Markdown/Mermaid media safety and typed code output.
- Durable Manim render synchronization and user-triggered full video regeneration.

## Manual Fusion Points

- Resource generation uses the upstream workflow graph while retaining local course retrieval.
- Conversation and job state use upstream contracts while rendering through the local task workspace.
- Resource viewers use upstream state machines and APIs with local Notion visual tokens.
- Learning compatibility endpoints delegate to the upstream workflow; they never write to a second store.
- Teacher analytics consumes canonical scored events and remains separate from student-facing UI.

## Exclusions

- `/demo`, competition pages, showcase fixtures and presentation-only dependencies.
- Legacy Sidebar, ConversationSidebar, SettingsModal, StageIndicator and chat-bubble presentation.
- Student-facing Agent, orchestration, dimension-count, confidence-review or technical-proof copy.
- Personal handoff reports, machine-specific paths, fixed real-data fixtures and browser harnesses.
- Unused `framer-motion`, `html2canvas`, `jspdf` and Playwright dependencies.

## Data Policy

- Canonical owner after migration: `local-user`.
- The only source owner permitted for this migration is `u_6446b325c76a4cfd`.
- The migration must back up every source database and artifact before writing.
- Competition, demo, smoke and unrelated generated users remain only in the untouched backup/source data.
- `backend/data/`, backups and audit output are never deleted or committed.
