# TutorBot Brand Specification

## Design authority

TutorBot uses Web Design Engineer Skill 1.3.0 in `Redesign · Overhaul` mode.

- Primary recipe: `references/style-recipes/headspace-meditation.md`
- Knowledge recipe: `references/style-recipes/notion-pre-ai.md`
- Global shell, mood, whitespace, radius and ambient motion follow Headspace.
- Knowledge libraries, resource galleries, properties, blocks and detail views follow Notion pre-AI.
- Headspace wins on ordinary product surfaces. Notion wins inside knowledge surfaces. The dark-mode rules below override both.

## Product voice

TutorBot is a calm learning space for Chinese university students. The interface speaks only about continuing study, current gaps, course material and the next useful step. Student-facing copy must not mention competitions, A3, agents, multi-agent orchestration, dimension counts, trust review, technical proof or internal implementation roles.

There is no separate showcase experience. Product capability is communicated through the working product itself.

## Visual system

- Headspace ground: warm peach `#FFE2C5` and cream `#FFEDD5`.
- Primary surface: coral `#F4A573`; primary ink `#1B3A47`; secondary ink `#5C6B7A`.
- One auxiliary color per scene only: lavender `#B0A5D1`, sage `#9DB67A` or salmon `#F5867B`.
- Knowledge canvas: `#FFFFFF`; panel `#F7F6F3`; ink `#37352F`; secondary `#787774`; hairline `#E9E9E7`.
- Body typography: Noto Sans SC. Knowledge headings: Noto Serif SC. Code: JetBrains Mono. These are the only three typography systems.
- Headspace radii: 16 / 24 / 32px and capsule buttons. Notion controls: 4 / 8 / 16px with 8px cards.
- Headspace spacing: 8 / 16 / 24 / 40 / 64 / 96px. Knowledge surfaces may use 4px micro-spacing.
- Normal transitions: 400–600ms for page atmosphere, 200–300ms for knowledge feedback. Breathing decoration uses a 3.2s cycle and is disabled by reduced-motion preferences.

## Dark mode

Dark mode is strictly achromatic. Backgrounds use `#101010`, `#181818` and `#232323`; borders use `#383838`; body text uses `#F5F5F5`; secondary text uses `#A6A6A6`.

All dark `oklch()` tokens must have chroma `0`. Brand, status, tag, logo, chart and decorative colors are grayscale. Success, failure and progress are differentiated with icon, copy, luminance, line style and weight—not hue.

## Structural rules

- Desktop navigation is an approximately 240px workspace sidebar; mobile navigation is a bottom bar.
- The three primary destinations are Home, Learning and Library. Settings sits outside primary navigation.
- Home uses an airy, organic Headspace composition and only real persisted data or honest empty states.
- Learning tasks use document flow rather than chat bubbles.
- Knowledge and resource collections use Notion-like gallery/list views, property rows, filtering and side details.
- Sources, citations and failure explanations live in a collapsed “来源与说明” section.
- No fabricated people, metrics, product screenshots or decorative brand illustrations.
- Never drift back to Microsoft/Azure enterprise styling, generic dashboard card grids, glassmorphism, neon glow or AI sparkle motifs.

## Protected contracts

The redesign must preserve existing REST and WebSocket paths, Job terminal semantics, conversation recovery, knowledge-base polling, resource Viewer APIs, persistence keys and partial-result recovery. `submission/` is user-owned and must not be modified by the redesign.
