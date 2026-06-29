---
name: harness
description: 4-phase autonomous feature development orchestrator for the Wave Logistics Bot — plan → build → review → remember. Triggers on /harness.
---

# /harness — Autonomous 4-Phase Development Orchestrator

**Usage:**
```bash
/harness "your task description"
/harness --ralph "research task (6+ hours, open-ended)"
/harness --parallel "task (split into: A, B, C)"
/harness --research "complex decision analysis"
/harness --skip-memory "task (testing only — skips Phase 4)"
```

Orchestrates a full development cycle in 4 phases. Every skill it calls already
exists in this repo — `/harness` only sequences and gates them.

| Phase | Purpose | Skills |
|---|---|---|
| 1. Plan | What to build | `/brainstorm` (superpowers) → `/piv-plan` → `/wave-analyst` (cond.) |
| 2. Build | How to build it | `/piv-implement` (default) \| `/split-and-verify` \| `ralph.py` |
| 3. Review | Is it good? | `/verify` (superpowers) + `/review` + `validate.py` + `/code-sparring` (parallel) |
| 4. Remember | What did we learn | `/codify` → `/update-memory` → `/consolidate-memory` (cond.) |

**Time:** ~15 min (quick fix) to 2+ hours (research task).

> **STRICT RULE (per AGENTS.md):** the run is not complete until
> `python ai-hub/gates/validate.py` exits 0. The validate gate inside Phase 3 is
> a hard stop — never advance to Phase 4 on a non-zero exit.

---

## Phase 1 — Plan

### 1.1 `/brainstorm` (ALWAYS)
Provided by the **superpowers** skill. Design before code — clarifying questions,
2–3 approaches with tradeoffs, an approved design doc under
`docs/superpowers/specs/`. Hard gate: no code until the design is approved.

```
Print: "Phase 1.1 — Brainstorming design…"
Invoke /brainstorm with the task description.
Store: design_doc (in context).
```

### 1.2 `/piv-plan` (ALWAYS)
Writes `ai-hub/plans/P-XXXX-<slug>.md` with exact files to read/modify/create,
ordered tasks, validation steps (incl. `validate.py`), and acceptance criteria.

```
Print: "Phase 1.2 — Writing the plan…"
Invoke /piv-plan (reads design_doc from context).
Store: plan_file = ai-hub/plans/P-XXXX-<slug>.md
```

### 1.3 `/wave-analyst` (CONDITIONAL)
Run when the task is genuinely complex or a real decision. Trigger if **any**:
- description contains: research, investigate, decide, "should we", evaluate, compare, alternatives, trade-offs, pros and cons
- `--research` or `--decision` flag
- description > 200 words, or more than one `?`
- plan has > 5 tasks, conditional branches, or unknowns/TBDs
- brainstorm output flags "major decision" / "uncertain" / "high risk"

Otherwise print `Phase 1.3 — Skipped (task is clear)`. See
`ai-hub/memory/harness-decision-thresholds.md` for exact scoring.

**Phase 1 done →** print ✓ design, ✓ plan, [✓ analysis], then choose the Phase 2 path.

---

## Phase 2 — Build (pick ONE path)

```
IF is_parallelizable(plan) AND --parallel:        → 2A /split-and-verify
ELIF is_vague(description) AND est_hours > 6 AND --ralph: → 2B ralph.py
ELSE:                                             → 2C /piv-implement   (DEFAULT)
```

### 2A — Parallel (`/split-and-verify`)
Use only when subtasks are truly independent (no shared state, touch different
files, 2–8 of them) and `--parallel` is set. Spawns subagents, each runs
`/piv-implement` on its slice, merges branches.
```
Invoke /split-and-verify with plan_file. Wait (long-running).
```

### 2B — Autonomous (`ralph.py`)
For vague, open-ended research that's 6+ hours, with `--ralph`. Runs the loop in
an isolated worktree so it can't corrupt the working tree or the DB.
```
Print: "Phase 2 — Autonomous loop (unattended). Monitor: tail -f ralph/ralph.log"
Run: python ai-hub/scripts/ralph.py --worktree --db-isolate
Store: branch_to_review = ralph/run-<timestamp>
```

### 2C — Linear (`/piv-implement`) — DEFAULT
Clear task, < 6 hrs. Executes the plan task-by-task, committing as it goes.
```
Invoke /piv-implement with plan_file. Wait.
```

**Phase 2 done →** note the branch (master, or `ralph/run-*` if 2B) and commit count.

---

## Phase 3 — Review (all run, gate at the end)

Run these together, then gate. `/verify` here is the superpowers verify pass; you
may instead use `/piv-validate` (it formally closes the PIV loop AND runs
`validate.py` internally) — if you do, you still run the standalone `validate.py`
check below as the hard gate of record.

| Check | Source | Verdict |
|---|---|---|
| `/verify` | superpowers | does it actually work? PASS / CONCERNS |
| `/review` | command → `code-reviewer` subagent | AGENTS.md rules. PASS / CONCERNS |
| `validate.py` | `python ai-hub/gates/validate.py` | exit 0 / non-zero (HARD GATE) |
| `/code-sparring` | adversarial bug hunt | PASS / CONCERNS |

**Logistics-bot review focus** (for `/review` + sparring): `pathlib.Path` for the
Windows runtime; the priority-based map queue (NOT FIFO — see
`ai-hub/memory/bot-infrastructure/map-queue.md`); the cross-bot DM queue and the
Management-bot deletion collision (memory: two-bot-deletion-collision); the 99%
Automation Tree threshold; single-claim HITL workflow; never commit `Models/`.

### Gate logic
```
IF validate.py exit != 0:
    Print "❌ VALIDATION GATE FAILED", print errors,
    "Fix, then re-run /harness."  → HALT (do not enter Phase 4)

ELIF verify == PASS AND review == PASS AND sparring == PASS:
    Print "✅ All checks passed."  → Phase 4

ELSE:  # one or more CONCERNS (but validate passed)
    Print every concern. Ask the user: "Fix these or accept the risk?"
      "fix"    → return to Phase 2 and re-implement
      "accept" → record the accepted risk, continue to Phase 4
```

---

## Phase 4 — Remember

Skipped entirely if `--skip-memory`.

### 4.1 `/codify` (ALWAYS)
Reconcile `ai-hub/memory/global-memory/goals/` statuses to reality; append durable decisions to
`ai-hub/decisions.log` (`YYYY-MM-DD | system | decision | why | rejected: …`).

### 4.2 `/update-memory` (ALWAYS)
Follow the Context Protocol: write a dated post-mortem in
`ai-hub/memory/global-memory/context/NNN-slug.md` (Symptom / Root Cause / Lesson),
then add one linked bullet to `global-memory/lessons-learned.md`. Never store a
naked lesson.

### 4.3 `/consolidate-memory` (CONDITIONAL)
Run if 3+ lessons this cycle AND they share a system, or the user says
"consolidate". Otherwise skip.

**Phase 4 done →**
```
ALL PHASES COMPLETE ✅
Next: git push origin master  (or merge ralph/run-<timestamp> if Phase 2B)
Do NOT push unless the user asks.
```

---

## Flags
```
--ralph        Force the autonomous loop (vague, 6+ hr tasks)
--parallel     Force the parallel path (independent subtasks)
--research     Force /wave-analyst in Phase 1
--skip-memory  Skip Phase 4 (testing only)
```

## State files a run touches
```
docs/superpowers/specs/…-design.md   ← Phase 1.1
ai-hub/plans/P-XXXX-<slug>.md         ← Phase 1.2
reports/<slug>-review.md              ← Phase 3 /review
ai-hub/memory/global-memory/goals/…   ← Phase 4 /codify
ai-hub/decisions.log                  ← Phase 4 /codify
ai-hub/memory/global-memory/context/  ← Phase 4 /update-memory
ralph/ralph.log + ralph/run-*         ← if Phase 2B
```

---

**Pairs with:** brainstorm + verify (superpowers), piv-plan, wave-analyst,
piv-implement, split-and-verify, ralph.py, review (code-reviewer), piv-validate,
validate.py, code-sparring, codify, update-memory, consolidate-memory.
**Thresholds:** `ai-hub/memory/harness-decision-thresholds.md`.
