---
description: Run the 4-phase autonomous development orchestrator (plan → build → review → remember). The full instructions are in ai-hub/skills/harness/SKILL.md.
---
Run the **harness** workflow for the task in $ARGUMENTS. The source of truth is
`ai-hub/skills/harness/SKILL.md` — read it and follow it exactly. In short:

1. **Plan** — `/brainstorm` (superpowers, gets design approval) → `/piv-plan`
   (writes `ai-hub/plans/P-XXXX-*.md`) → `/wave-analyst` only if the task is
   genuinely complex (see thresholds doc).

2. **Build** — pick ONE: `/piv-implement` (default), `/split-and-verify`
   (`--parallel`, independent subtasks), or `python ai-hub/scripts/ralph.py
   --worktree --db-isolate` (`--ralph`, vague 6+ hr research).

3. **Review** — run `/verify`, `/review`, `python ai-hub/gates/validate.py`, and
   `/code-sparring`. `validate.py` non-zero is a HARD STOP — never advance.
   CONCERNS (with validate passing) → ask the user to fix or accept the risk.

4. **Remember** — `/codify`, `/update-memory`, then `/consolidate-memory` only if
   3+ related lessons. Skip this phase if `--skip-memory`.

Honor the flags: `--ralph`, `--parallel`, `--research`, `--skip-memory`.
Do NOT commit or push unless I explicitly ask.
