# ai-hub/memory/global-memory/goals — Active Task Tracking

Lightweight, visible to-do list for work in flight. One file per goal, with frontmatter that drives automation. Lives inside the memory brain alongside lessons learned and session summaries.

## What goes here

- **Active tasks** — one per goal file (one goal = one unit of work)
- **Acceptance criteria** — definition of done for that task
- **Decisions** — the "why" behind architectural choices (linked from here to `decisions.log`)

## What does NOT go here

- Session recaps → `../../session-summaries/`
- Detailed post-mortems → `../context/` (same folder)
- Forward-looking specs → `../../../../plans/`
- Rules / lessons → `../lessons-learned.md` (same folder)

## How to use

**Create a goal:** Copy `_TEMPLATE.md` to `NN-short-name.md` (next number, kebab-case name).

**Track progress:** Edit the `status:` field as work moves (`backlog` → `in-progress` → `review` → `done`).

**At session start:** Hook (`../../../../scripts/goals_status.py`) auto-prints all `in-progress` and `review` goals, so you see what's live.

**When work finishes:** Run `/codify` — it reconciles `status:` to reality and appends durable decisions to `../../../../decisions.log`.

## Status lifecycle

- `backlog` — not started
- `in-progress` — actively being worked on
- `review` — done but waiting for approval
- `done` — finished, archived

Only `in-progress` and `review` are surfaced at session start.

## Relationship to memory

This is a lightweight **work-tracking layer** within the memory brain. It is NOT a second memory system. For durable knowledge, use other parts of the memory folder:

| Need | Location |
|---|---|
| Track an active task | A goal file **here** |
| One-line durable decision | `../../../../decisions.log` |
| Full bug/lesson post-mortem | `../context/` (same global-memory folder) |
| Big spec or roadmap | `../../../../plans/` |
| Bot infrastructure docs | `../../bot-infrastructure/` |
| Session history | `../../session-summaries/` |

## See also

- [`..`](..) — global-memory (lessons learned, context post-mortems)
- [`../..`](../..) — memory (SUPERCOMPUTER.md is the master index)
- [`../../SUPERCOMPUTER.md`](../../SUPERCOMPUTER.md) — full folder map and Context Protocol
- [`../../../../plans/`](../../../../plans/README.md) — forward-looking specs
- [`../../../../scripts/goals_status.py`](../../../../scripts/README.md) — automation that reads goal files
