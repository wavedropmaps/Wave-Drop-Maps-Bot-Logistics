# ai-hub/scripts — Developer Automation Tools

Utility scripts that automate common development tasks, hook integrations, and workflow helpers.

## What goes here

- **Developer scripts** — automation to speed up common work (e.g., syncing, running checks)
- **Hook integrations** — scripts triggered by git hooks or system events
- **Workflow helpers** — scripts that generate reports, reconcile state, or manage goals

## Current files

- `goals_status.py` — Reports on active goals (called by SessionStart hook)
- `ralph.py` — Main automation orchestrator (10KB+, likely handles complex workflows)
- `wave_sync.py` — Cross-bot syncing and state reconciliation
- `hooks/` — Git hook scripts (pre-commit, post-merge, etc.)

## When to add

**Add a script when:**
- You want to automate a repetitive task (e.g., "check all goals are updated")
- You need a hook trigger (e.g., "on every commit, run validation")
- You're building a developer tool that multiple agents will use

**Use if:**
- It saves time or prevents human error
- It's referenced in AGENTS.md or called by a hook
- Other agents would benefit from running it

## Common patterns

- **Exit code:** 0 = success, non-zero = failure (follow Unix convention)
- **Output:** Print results to stdout (for logs) and errors to stderr
- **Logging:** Use Python `logging` module for detailed debug info
- **Python version:** Match the bot's requirements (Python ≥ 3.8)

## Hooks structure

The `hooks/` subdirectory contains trigger scripts:
- `pre-commit` — runs before each commit
- `post-merge` — runs after git merge
- (Add more as needed)

See also: [`ai-hub/gates/`](../gates/README.md) — validation gates are complementary to automation scripts.

## See also

- [`ai-hub/gates/`](../gates/README.md) — validation scripts (run before commits)
- [`ai-hub/memory/global-memory/goals/README.md`](../memory/global-memory/goals/README.md) — the `goals_status.py` integration point
