# Wave Logistics Bot — Agent HQ

> **STRICT VALIDATION RULE:** Never complete a feature or claim a task is done without first running `python ai-hub/gates/validate.py` and ensuring a 0 exit code.

> **This is the single source of truth for every AI agent** working on this repo (Claude Code, Antigravity/Gemini, Cursor, Copilot, …). Every other entry file (`CLAUDE.md`, `.agents/`) is a thin shim that points here. Edit rules HERE — never duplicate them into a shim.

The Wave Logistics Bot is a Python `discord.py` bot for the Wave gaming community.

## 🧭 Start here
- **The brain:** read [`ai-hub/memory/SUPERCOMPUTER.md`](ai-hub/memory/SUPERCOMPUTER.md) — the master index to everything this project has learned, all folder structure, the Context Protocol, and how to keep memory healthy.
- **This file** stays lean (a router). Deep detail lives in `ai-hub/` docs, linked below.

## 🗺️ Codebase Map — where the deep docs live
For detail on an area, read its doc (plain markdown — any agent can):

| Working on… | Read |
|---|---|
| Entry point, cog loading, the DB layer — `Main.py`, `Database/` | `ai-hub/memory/bot-infrastructure/core-files.md` |
| Proof auto-detection: the model cascade + grant/reject/HITL flow | `ai-hub/memory/bot-infrastructure/proof-automation.md` |
| The Automation Tree decision architecture (99% threshold, model nodes) | `ai-hub/memory/bot-infrastructure/automation-tree.md` |
| The human review queue (single-claim workflow, `hitl_pending/`) | `ai-hub/memory/bot-infrastructure/hitl-review-queue.md` |
| The map-request queue — priority sorting, DROP MAP vs LOOT ROUTE, embeds | `ai-hub/memory/bot-infrastructure/map-queue.md` |
| Background loops — cross-bot DM queue, cleanup, logging, streaks | `ai-hub/memory/bot-infrastructure/background-tasks.md` |
| How the two Wave bots coordinate — shared DM DB, queue→channel bridges, shared proof channel | `ai-hub/memory/bot-infrastructure/cross-bot-interaction.md` |

**Live code locations:** `Commands/` (slash-command cogs), `Tasks/` (background jobs & listeners), `utils/` (shared helpers — the Automation Tree, queue priority/encoding, loggers), `Database/` (the DB layer), `Models/` (the ML model cascade — large, gitignored, local-only on the bot's PC).

## 🧠 The memory brain — how to keep it
`ai-hub/memory/` is the unified AI brain, indexed by `SUPERCOMPUTER.md`. Three pillars:
- `bot-infrastructure/` — how the live code works today (the Codebase Map targets above).
- `global-memory/` — rules + lessons. **Never store a naked lesson.** Follow the Context Protocol: write a dated post-mortem in `global-memory/context/NNN-slug.md` (Symptom / Root Cause / Lesson), then add a one-line **linked** bullet to `global-memory/lessons-learned.md`. (The `update-memory` skill does this.)
- `session-summaries/` — one recap per work session.

**Active work-tracking (lightweight layer on top of the brain):**
- `ai-hub/memory/global-memory/goals/` — one file per active task, with a `status:` lifecycle. The SessionStart hook surfaces `in-progress`/`review` goals automatically, so you start knowing what's live.
- `ai-hub/decisions.log` — append-only, one line per durable decision (`YYYY-MM-DD | system | decision | why | rejected: …`). Only for choices a future agent would re-litigate; full post-mortems still go to `global-memory/context/`.
- When a chunk of work wraps, run **`/codify`** to flip goal statuses to reality and append any decision — markdown files don't update themselves.

## 📂 Sorting rule — where does a new file go? (top to bottom, first match wins)
1. **Code the bot imports/runs, or referenced by a path in code?** (a cog, a task, a model file the bot loads, a `.db` a command reads) → leave it where the code expects it (`Commands/`, `Tasks/`, `utils/`, `Models/`, …). **NEVER move it into `ai-hub/`** — that breaks the path the code uses.
2. **Agent/tool config an IDE auto-discovers at a fixed spot?** (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.mcp.json`, `.agents/`) → repo **root**, never moved.
3. **Otherwise it's AI work product → `ai-hub/`**, filed by type:
   - a reusable skill → `ai-hub/skills/`
   - a plan, spec, or roadmap (forward-looking — what to BUILD/change) → `ai-hub/plans/` (superseded → `ai-hub/plans/old-plans/`)
   - general docs or architecture models → `ai-hub/docs/`
   - bot systems/infrastructure docs → `ai-hub/memory/bot-infrastructure/`
   - rules / mistakes to avoid → `ai-hub/memory/global-memory/`
   - research / data-gathering output → `ai-hub/research/<topic>/`
   - a session summary → `ai-hub/memory/session-summaries/`
   - a throwaway experiment → `ai-hub/scratch/`
   - retired / superseded / dead-but-kept → `ai-hub/deprecated/`

When in doubt between "live tooling" (rule 1) and "AI work product" (rule 3): if removing the file would break the bot or a command, it's rule 1.

## 🛠️ Skills & MCP tools — where they live & how each agent finds them
- **Skills** → all live in **`ai-hub/skills/`** (one portable hub; travels with the repo).
- **MCP tools** → defined in **`.mcp.json`** at the repo root (tokens come from the gitignored `.env`).

**There is NO universal skills-discovery standard yet.** Each agent auto-looks only in its own default spot (Claude Code → `.claude/skills/` or plugins; Antigravity → `.agents/skills.json`), and none auto-check `ai-hub/skills/`. So if your skills don't appear:
1. They ARE here — read/use them directly from `ai-hub/skills/`.
2. To make them auto-discover (e.g. show in a `/` menu), add YOUR agent's native pointer aimed at `ai-hub/skills/`. Existing pointer: **Antigravity → `.agents/skills.json`**. If you're a new agent, set up your native skills pointer to `ai-hub/skills/` — a one-time, tiny config that then travels with the repo.

## 🗂️ ai-hub/ at a glance
```
ai-hub/
├── skills/        portable skills (40+)
├── plans/         forward-looking specs/roadmaps
├── docs/          general docs + docs/architecture/ (project diagram)
├── research/      research output, by topic
├── memory/        the brain (SUPERCOMPUTER.md + 3 pillars)
│   └── global-memory/
│       └── goals/  active task tracking
├── gates/         validation scripts (pre-commit checks)
├── scripts/       developer automation tools
├── scratch/       throwaways
└── deprecated/    the attic (e.g. old-automation-project/)
```

## 📖 Folder reference
Each ai-hub/ subfolder has docs explaining what goes there. Click to navigate:

- [`memory/SUPERCOMPUTER.md`](ai-hub/memory/SUPERCOMPUTER.md) — The AI brain (master index with full folder map, Context Protocol, goals, everything)
  - [goals/README.md](ai-hub/memory/global-memory/goals/README.md) — Active task tracking and acceptance criteria
- [`plans/README.md`](ai-hub/plans/README.md) — Forward-looking specs, roadmaps, implementation plans
- [`docs/README.md`](ai-hub/docs/README.md) — Architecture, system overviews, technical guides
- [`gates/README.md`](ai-hub/gates/README.md) — Validation scripts and pre-commit checks
- [`scripts/README.md`](ai-hub/scripts/README.md) — Developer automation and workflow helpers
- [`research/README.md`](ai-hub/research/README.md) — Data-driven analysis and investigations
- [`scratch/README.md`](ai-hub/scratch/README.md) — Throwaway experiments and early drafts
- [`deprecated/README.md`](ai-hub/deprecated/README.md) — Retired code and historical reference

## ⚙️ Run / infra notes
- Start: `python Main.py` (see `package.json` → `scripts.start`). Python ≥ 3.8. Deps in `requirements.txt`.
- `config.json` holds the shared cross-bot DM-queue DB path + the log channel id.
- The bot RUNS on a **Windows** PC; development is on **Mac** — use `pathlib` for paths (see `global-memory/lessons-learned.md`).
- `Models/` (the ML cascade, ~340 MB) is gitignored and lives only on the bot's PC. **Never commit it.**
