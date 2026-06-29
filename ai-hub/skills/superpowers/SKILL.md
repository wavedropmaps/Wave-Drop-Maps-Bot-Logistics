---
name: superpowers
description: >
  Structured agentic development framework — use this skill whenever the user wants to brainstorm/design a feature before coding, debug a bug systematically, do proper TDD (test-driven development), build something with subagents, or verify work is actually done before claiming completion. Triggers on: /brainstorm, /debug, /tdd, /build, /verify, or any request to design/plan before coding, fix a bug properly, write tests first, or confirm something works.
---

# Superpowers — Agentic Skills Framework

This skill bundles 5 structured workflows. Pick the one that matches the task.

---

## /brainstorm — Design before you code

**Hard gate: Do NOT write any code until the user has approved a design.**

1. Review existing files and recent context
2. Ask clarifying questions **one at a time** — purpose, constraints, success criteria
3. Propose 2–3 approaches with tradeoffs and a recommendation
4. Present a design doc, get approval on each section
5. Save spec to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`
6. Self-review spec for placeholders, contradictions, ambiguity
7. Only after user approves → proceed to writing a plan

**Counter the "too simple to design" trap.** Even simple projects hide unexamined assumptions. A brief design doc is always worth it.

---

## /debug — Systematic debugging (no guessing)

**Rule: NO FIXES WITHOUT ROOT CAUSE INVESTIGATION FIRST.**

### Phase 1 — Root cause investigation
- Read the full error message carefully
- Reproduce the bug consistently before touching anything
- Check what changed recently (git log)
- Gather evidence at component boundaries
- Trace data flow backward through the call stack

### Phase 2 — Pattern analysis
- Find working examples of similar code
- Compare against references completely — don't skim
- Identify exact differences

### Phase 3 — Hypothesis and testing
- Form a specific hypothesis
- Test with one variable at a time
- Verify results before proceeding

### Phase 4 — Fix
- Write a failing test that captures the bug
- Implement the minimal fix addressing the root cause
- Verify it passes

**Red flag:** If 3+ fixes have failed, stop. The architecture itself may be wrong — don't keep patching.

---

## /tdd — Test-driven development

**Rule: NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST.**

The cycle:
1. **RED** — Write a minimal failing test for the desired behaviour
2. **GREEN** — Write the simplest code that makes it pass
3. **REFACTOR** — Clean up while keeping tests green

If you wrote code before the test: delete it entirely. Don't adapt it. Start from the failing test.

**Why watching the test fail matters:** If the test passes immediately, you don't know if it tests the right thing.

Before claiming done, verify:
- Every new function has a test
- Every test was observed failing first
- All tests pass with clean output
- Edge cases are covered
- Real code is tested (mocks only when truly unavoidable)

---

## /build — Subagent-driven development

Use when you have a complete plan and mostly independent tasks.

1. Read the plan once and extract all tasks with complete context
2. For each task: dispatch a subagent with isolated, focused instructions (not full session history)
3. When subagent responds:
   - **DONE** → spec compliance review, then code quality review
   - **DONE_WITH_CONCERNS** → address correctness concerns first, then review
   - **NEEDS_CONTEXT** → provide missing info and re-dispatch
   - **BLOCKED** → restructure the task or escalate
4. Run final comprehensive code review after all tasks complete
5. Do NOT pause for human check-ins between tasks — run continuously

**Key:** Fresh subagent per task keeps context clean. Don't give subagents the full session history.

---

## /verify — Verification before claiming completion

**Rule: NEVER claim work is done without running fresh verification commands.**

Before any "it's done" statement:
1. Identify which command actually proves your assertion
2. Run it fresh (not a prior run's output)
3. Read the full output including exit codes
4. Confirm the output supports the claim
5. Only then state the result with evidence

**Red flag language to avoid:**
- "should work", "probably fine", "seems to"
- Satisfaction statements before testing
- Relying on subagent reports without your own check

This applies before every commit, PR, or task closure — no exceptions.
