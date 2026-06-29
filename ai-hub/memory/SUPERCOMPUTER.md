# 🧠 The Supercomputer Memory

Welcome to the unified AI Brain for the **Wave Logistics Bot** project. If you are an AI agent, **start reading here** to understand how our memory is structured.

## The Four Pillars of Memory

The `ai-hub/memory/` folder is divided into four core areas:

1. **`bot-infrastructure/`**
   Deep-dive documentation on how the live code works today. If you need to touch a system (proof automation, the Automation Tree, the HITL review queue, the map queue, background tasks, or the database), read its doc here FIRST. These docs are the targets of the `AGENTS.md` → Codebase Map table.
   - `core-files.md` — Entry point, cog loading, DB layer
   - `proof-automation.md` — ML model cascade + grant/reject/HITL flow
   - `automation-tree.md` — The decision architecture (99% threshold)
   - `hitl-review-queue.md` — Single-claim workflow, message updates
   - `map-queue.md` — Priority sorting, DROP MAP vs LOOT ROUTE
   - `background-tasks.md` — DM queue, cleanup, logging, streaks
   - `cross-bot-interaction.md` — how Wave Logistics ⇄ Wave Management coordinate (shared DM DB, queue→channel bridge, shared proof channel). Mirrored identically in both repos.

2. **`global-memory/`**
   The continuous learning center — rules, best practices, and lessons learned from past mistakes, so future agents don't repeat them.
   - `lessons-learned.md` — Index of all rules + hyperlinks to post-mortems
   - `context/` — Dated post-mortems (`YYYY-MM-DD-slug.md`): Symptom, Root Cause, Lesson
   - `goals/` — Active task tracking (one file per task, status lifecycle: backlog → in-progress → review → done)

3. **`session-summaries/`**
   A historical archive of past chat sessions — what was built or changed, and on what date. One recap per work session.

## The Context Protocol — How to Learn

We do NOT store naked bullet points without context. If you discover a bug, make a mistake, or learn a new pattern, follow the **Context Protocol**:

### When you find a bug or lesson
1. Write a detailed post-mortem in `global-memory/context/YYYY-MM-DD-slug.md` with:
   - **Symptom** — what went wrong / what we noticed
   - **Root Cause** — why it happened
   - **Lesson** — the rule we learned
2. Add a one-line hyperlinked entry to `global-memory/lessons-learned.md`

**Why?** Future agents understand *why* a rule exists, not just that it does.

### When you start a task
- Create a goal file in `global-memory/goals/NN-slug.md` (use `_TEMPLATE.md`)
- Record acceptance criteria and status
- SessionStart hook auto-surfaces `in-progress` and `review` goals

### When work finishes
- Run `/codify` to reconcile goal statuses and append decisions to `ai-hub/decisions.log`
- If you learned something, write the post-mortem (above) and run `/update-memory`

### Full workflow example
```
Task starts → create goals/01-fix-indexerror.md (status: in-progress)
Task in progress → update acceptance criteria as you go
Bug found → write global-memory/context/2026-06-24-indexerror-cascade.md
Task done → run /codify (updates goals status → done, appends decision to decisions.log)
Lesson learned → run /update-memory (adds linked bullet to lessons-learned.md)
```

If you don't know how to do this, read `ai-hub/skills/update-memory/` or `ai-hub/skills/codify/SKILL.md`.

## When to Read What

| Situation | Read |
|---|---|
| "I'm starting work on the bot" | This file (SUPERCOMPUTER.md) |
| "I need to understand how [system] works" | `bot-infrastructure/[system].md` (e.g., `proof-automation.md`) |
| "What rules should I follow?" | `global-memory/lessons-learned.md` + click the hyperlinks |
| "What's in flight right now?" | SessionStart hook prints this; or `global-memory/goals/` |
| "What happened in the last session?" | `session-summaries/` (most recent file) |
| "How do I create a goal or lesson?" | This file (The Context Protocol section) |

## File count & scale

- **bot-infrastructure/**: 6–10 files (one per major system)
- **global-memory/context/**: 10–20 post-mortems (grows as you learn)
- **global-memory/goals/**: 3–5 active at a time, archive when done
- **session-summaries/**: one per session (~20–30 total)
- **Total**: ~50–70 files in a mature project

## Complete Folder Map

```
ai-hub/memory/
├── SUPERCOMPUTER.md           ← you are here (master index)
├── harness-decision-thresholds.md
├── bot-infrastructure/        ← BEFORE touching a system, read here
│   ├── core-files.md
│   ├── proof-automation.md
│   ├── automation-tree.md
│   ├── hitl-review-queue.md
│   ├── map-queue.md
│   ├── background-tasks.md
│   └── cross-bot-interaction.md
├── global-memory/             ← continuous learning from mistakes + active work
│   ├── lessons-learned.md (the index of all rules)
│   ├── context/               ← dated post-mortems (YYYY-MM-DD-slug.md)
│   └── goals/                 ← active task tracking
│       ├── README.md
│       ├── _TEMPLATE.md
│       └── NN-*.md (active tasks)
└── session-summaries/         ← one recap per work session
    ├── YYYY-MM-DD-*.md
    └── ...
```

## Where Does Everything Go?

| What you learned | Where it goes | Format |
|---|---|---|
| Bug or lesson (WHY something failed) | `global-memory/context/YYYY-MM-DD-slug.md` | Symptom / Root Cause / Lesson |
| A rule or best practice | `global-memory/lessons-learned.md` (link to context file) | One-liner + hyperlink |
| An active task or sprint item | `global-memory/goals/NN-slug.md` | Frontmatter + acceptance criteria |
| A session recap (what we built) | `session-summaries/YYYY-MM-DD-slug.md` | Via `/session-handoff` skill |
| How a live bot system works | `bot-infrastructure/system.md` | Deep-dive architecture doc |
