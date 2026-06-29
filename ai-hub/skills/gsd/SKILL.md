---
name: gsd
description: >
  Get Shit Done (GSD) — structured context-engineering and spec-driven development framework. Use this skill whenever the user wants to start a new project, plan a feature, execute work with subagents, verify code actually works, or ship a PR. Also use when the user mentions context rot, context getting too long, losing track of progress, resuming after a break, or working across sessions. Triggers on: /gsd, /plan, /execute, /verify, /ship, or any request to structure a big task, run parallel agents, or pick up where we left off.
---

# GSD — Get Shit Done

A 5-phase loop that prevents context rot and keeps work structured across sessions.

**Core idea:** Heavy work goes to fresh-context subagents. The main session stays lean and focused.

---

## The 5-Phase Loop

Repeat this loop for each milestone or major feature:

```
Discuss → Plan → Execute → Verify → Ship
```

---

## Phase 1: /gsd-discuss — Capture decisions before planning

Before any planning or code:
1. Ask clarifying questions to understand scope, constraints, success criteria
2. Document implementation decisions — architecture choices, tradeoffs accepted, non-goals
3. Write decisions to `ai-hub/skills/gsd/STATE.md` under a `## Decisions` section
4. Only proceed to Plan when decisions are documented and user confirms

**Why:** Decisions made during planning get forgotten. Writing them first means subagents stay aligned.

---

## Phase 2: /gsd-plan — Research and decompose

1. Spawn a fresh-context subagent to research the codebase (don't pollute main context)
2. Decompose work into independent tasks — each task should be completable without the others
3. Write the plan to `ai-hub/plans/PLAN.md`:
   - Milestone name and goal
   - Task list with clear inputs/outputs for each
   - Dependencies between tasks (if any)
   - Definition of done
4. Review the plan — check for gaps, ambiguity, missing edge cases
5. User approves before moving to Execute

**MVP mode:** If the user wants to move fast, plan only the minimum tasks needed to prove the idea.

---

## Phase 3: /gsd-execute — Run tasks with fresh subagents

1. Read `gsd/PLAN.md` once, extract all tasks
2. Group independent tasks into waves — tasks in the same wave run in parallel
3. For each task, dispatch a subagent with:
   - The specific task (not the full session history)
   - Relevant file paths and context only
   - Clear output expectations
4. As subagents complete, collect results and check for blockers
5. Update `ai-hub/skills/gsd/STATE.md` with completed tasks
6. Run waves continuously — don't pause between tasks for check-ins

**Statuses to handle:**
- `DONE` → proceed to next wave
- `DONE_WITH_CONCERNS` → address concerns before proceeding
- `NEEDS_CONTEXT` → provide missing info, re-dispatch
- `BLOCKED` → restructure the task or escalate to user

---

## Phase 4: /gsd-verify — Test before claiming done

**Rule: Never claim work is done without running verification commands fresh.**

1. Run the project's tests: note exact commands, read full output including exit codes
2. Smoke test the happy path manually if possible
3. For each task in the plan, confirm its definition of done is actually met
4. If failures: diagnose root cause (don't guess), fix, re-verify
5. Write a `## Verification` section in `ai-hub/skills/gsd/STATE.md` with evidence — what was run, what passed

Only move to Ship after all verification passes with fresh evidence.

---

## Phase 5: /gsd-ship — Create PR and archive

1. Summarise all changes made in this phase
2. Create a PR with:
   - Title: what changed (imperative, under 70 chars)
   - Body: why it changed, what was decided, how to test
3. Archive the phase: move `ai-hub/plans/PLAN.md` to `ai-hub/plans/archive/PLAN-<date>-<milestone>.md`
4. Reset `ai-hub/skills/gsd/STATE.md` for the next phase

---

## Session persistence (resuming work)

GSD uses two files to survive session breaks and `/compact`:

**`ai-hub/skills/gsd/STATE.md`** — current snapshot:
```markdown
## Phase
Execute — Wave 2 of 3

## Completed tasks
- [x] Set up database schema
- [x] Write API endpoints

## In progress
- [ ] Frontend components (subagent dispatched)

## Decisions
- Using SQLite not Postgres (simpler deploy)
- No auth in MVP

## Verification
- Tests: `npm test` passed (47/47) on 2026-06-15
```

**`ai-hub/skills/gsd/CONTEXT.md`** — project knowledge base (machine-readable facts, not prose).

To resume after a break: read `gsd/STATE.md`, tell the user where things are, and continue from the right phase.

---

## Quick commands

| Command | When to use |
|---|---|
| `/gsd-new-project` | Starting from scratch — initialise STATE.md and CONTEXT.md |
| `/gsd-progress` | "Where am I? What's next?" — auto-routes to right phase |
| `/gsd-explore` | Socratic ideation before committing to a direction |
| `/gsd-spike` | Quick feasibility experiment — returns VALIDATED or INVALIDATED |
| `/gsd-resume-work` | Pick up after a session break |
| `/gsd-code-review` | Review phase changes for bugs and security issues |

---

## Context rot prevention

Signs you're getting context rot:
- Claude starts forgetting earlier decisions
- Responses get vaguer or contradictory
- Quality drops noticeably mid-session

Fix: `/gsd-pause-work` → write STATE.md → start a fresh session → `/gsd-resume-work`

The rule: **heavy research and implementation goes to subagents with fresh 200k-token contexts. Main session = coordination only.**
