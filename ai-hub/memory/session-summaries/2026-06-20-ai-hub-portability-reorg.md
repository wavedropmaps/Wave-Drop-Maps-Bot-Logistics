# AI-Hub Portability Reorg

**Date:** 2026-06-20
**Topic:** Reorganizing the Wave Logistics Bot into a portable, cross-agent `ai-hub/` structure mirroring the Wave Management Bot.

---

## What we built / discussed
- Cleaned junk: fixed a `.gitignore` typo, untracked + ignored 53 committed `hitl_pending/` runtime images, deleted 4 loose scratch scripts (`fix_indent.py`, `test_db.py`, `test_yolo_classes.py`, `test_all_classes.py`).
- Studied the Wave Management Bot's AI-tooling architecture (via two recon sub-agents) and mirrored it here.
- Built `ai-hub/` with `skills/ docs/ memory/ plans/ research/ scratch/ deprecated/`.
- Moved AI notes in: research docs → `ai-hub/research/`, `STOLEN_DETECTION_PLAN.md` → `ai-hub/plans/`, `old code/` → `ai-hub/deprecated/old-automation-project/`.
- Copied the 21 portable skills from the Management bot into `ai-hub/skills/`.
- Promoted `CLAUDE.md` → `AGENTS.md` (the lean HQ router) and added a 9-line `CLAUDE.md` shim (`@AGENTS.md`).
- Added cross-agent wiring: `.mcp.json` (github + context-mode) and `.agents/` (AGENTS.md shim + `skills.json` pointer).
- Seeded the memory brain: `SUPERCOMPUTER.md`, `lessons-learned.md`, and three post-mortems (cross-bot proof deletion, ViT version coupling, stolen-proof loophole).
- Wrote six `bot-infrastructure/` system docs from the real code (via three recon sub-agents), plus an architecture diagram in `docs/architecture/`.

## Key decisions
- Migrate the old `claude/`-folder convention to the portable `ai-hub/` layout (rewrote the file-org rule in `AGENTS.md`).
- All work done in an isolated git worktree off `master` (branch `portable-setup`) so the live repo is untouched until reviewed.
- Keep `old code/` as the archive — relocated into `ai-hub/deprecated/` rather than deleted.

## Files changed
- New: `AGENTS.md` (router), `CLAUDE.md` (shim), `.mcp.json`, `.agents/AGENTS.md`, `.agents/skills.json`, and all of `ai-hub/`.
- Moved: research docs, `STOLEN_DETECTION_PLAN.md`, `old code/`.
- Edited: `.gitignore` (typo fix + `hitl_pending/`).
- Deleted: 53 `hitl_pending/` images (worktree), 4 loose scratch scripts (live folder).

## Things to remember
- `Models/` (~340 MB ML cascade) stays gitignored and local-only — never commit it.
- The two Wave bots share channels/queues; their automated actions can collide (see `global-memory/context/001-cross-bot-proof-deletion.md`).
- Nothing is committed yet — changes live on the `portable-setup` worktree branch pending review.
