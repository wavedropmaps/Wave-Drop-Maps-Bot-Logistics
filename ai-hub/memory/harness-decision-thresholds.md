---
name: harness-decision-thresholds
description: "Exact thresholds, heuristics, and criteria for every harness decision point"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7edafa36-7802-47fe-8991-f773a1b263fa
---

# Harness Decision Thresholds — Exact Criteria

When harness runs, it makes decisions at multiple gates. These are the exact thresholds.

---

## PHASE 1: DECISION POINT 1 — Should /wave-analyst run?

**Question:** Does this task need hard research or a major decision analysis?

### Threshold Triggers (ANY of these):

1. **Keywords in description:**
   - "research", "investigate", "explore", "decide", "should we"
   - "evaluate", "compare", "alternatives", "analyze"
   - Example: "research how to improve drop-map" → YES

2. **Explicit flag:**
   - User passes `--research` or `--decision`
   - Example: `/harness --research "..."` → YES

3. **Prompt size + complexity (INFERRED):**
   - **Large prompt** (> 200 words):
     - Indicates complex task, multiple considerations
     - Example: Multi-paragraph description → likely needs analysis
   - **Prompt mentions multiple aspects:**
     - "approach A vs approach B"
     - "pros and cons"
     - "trade-offs"
     - "implications"
     - Example: "should we retrain or tune? pros: X, cons: Y" → YES
   - **Prompt asks questions (not statements):**
     - Contains "how", "what if", "should we", "which"
     - Multiple question marks
     - Example: "How do we improve? What if we retrain? Should we?" → YES

4. **Brainstorm output mentions:**
   - "major decision"
   - "uncertain approach"
   - "multiple alternatives"
   - "high risk"
   - "non-obvious"
   - Example: Brainstorm output says "not sure if we should retrain or tune" → YES

5. **Plan complexity:**
   - Plan has `> 5 tasks` (indicates complex work)
   - Plan has conditional branches ("if X then A, else B")
   - Plan has "TBD" or "unknown" in tasks
   - Plan has non-linear dependencies (not just 1→2→3)
   - Example: Plan has 8 tasks with 3 conditional branches → YES

### When to Skip (/wave-analyst = NO):

- Single-sentence simple fix ("fix typo on line 42")
- Brief, clear feature ("add button to UI")
- Bug with known root cause and fix
- Refactor with explicit, clear scope
- All tasks in plan are concrete (no unknowns)
- Prompt < 50 words AND doesn't contain questions

### Complexity Scoring (Helper)

Estimate task complexity by counting indicators:

```
Score 0-2 (Low): Run wave-analyst = NO
  - Short prompt (< 100 words)
  - No research keywords
  - No questions asked
  - Simple, direct statement
  - Example: "fix typo" = 0 points

Score 3-4 (Medium): Run wave-analyst = MAYBE
  - Medium prompt (100-200 words)
  - Some research keywords
  - Few questions
  - Has some unknowns
  - Example: "research detection" = 3-4 points

Score 5+ (High): Run wave-analyst = YES
  - Long prompt (> 200 words)
  - Multiple research keywords
  - Multiple questions or decision points
  - Multiple unknowns
  - Multiple aspects to consider
  - Example: "Should we retrain YOLO or tune? What are trade-offs?" = 5+ points
```

### Decision Logic (Code-like):

```python
run_wave_analyst = (
    "research" in description.lower() OR
    "investigate" in description.lower() OR
    "decide" in description.lower() OR
    "--research" in flags OR
    "major decision" in brainstorm_output OR
    len(plan_tasks) > 5 OR
    has_conditional_branches(plan) OR
    has_unknowns_in_plan(plan) OR
    
    # NEW: Prompt size + complexity inference
    len(description) > 200 OR  # Large prompt
    (
        len(description) > 100 AND
        (has_multiple_questions(description) OR 
         mentions_tradeoffs(description) OR
         mentions_pros_and_cons(description))
    ) OR
    count_question_marks(description) > 1 OR
    inferred_complexity_score(description) >= 5
)
```

### Examples with Complexity Scoring

| Prompt | Size | Keywords? | Questions? | Complexity | wave-analyst? |
|--------|------|-----------|-----------|-----------|---|
| "fix typo on line 42" | 6 words | NO | NO | 0 (Low) | ❌ NO |
| "add drop-map button" | 4 words | NO | NO | 0 (Low) | ❌ NO |
| "research how to improve accuracy" | 6 words | YES | YES | 4 (Med) | ✅ YES |
| "Should we retrain YOLO or tune it? What are the trade-offs? Pros and cons?" | 15 words | YES | YES | 6 (High) | ✅ YES |
| "We need to refactor the drop-map system. It's scattered across 3 files. New features are slow. Should we refactor incrementally or rewrite? What are the risks? What are alternatives?" | 28 words | YES | YES | 7 (High) | ✅ YES |
| "Refactor drop-map system incrementally using pattern X" | 8 words | NO | NO | 1 (Low) | ❌ NO |
| "Investigate why accuracy dropped. Is it data drift? Model degradation? Labeling errors? How do we diagnose this? What's the quickest fix?" | 21 words | YES (investigate) | YES | 5 (Med-High) | ✅ YES |

---

## PHASE 2: DECISION POINT 1 — Is task breakable into parallel subtasks?

**Question:** Can this task be split into N independent subtasks that don't depend on each other?

### Threshold Triggers (ALL of these must be true):

1. **Description contains split keywords:**
   - "split into:", "separate:", "parallel:", "independent"
   - "refactor (A, B, C)", "build (module1, module2)"
   - Example: "refactor drop-map system (split into: data, ui, scoring)" → YES

2. **Subtasks are truly independent:**
   - Task A doesn't wait for Task B
   - Task B doesn't use output from Task A
   - No shared state being modified
   - Can merge results without conflicts
   - Example: "build UI + add tests + update docs" → NOT independent (all depend on code being done first)

3. **User explicitly marks with flag:**
   - User passes `--parallel`
   - Example: `/harness --parallel "..."` → YES

4. **Number of subtasks is reasonable:**
   - 2-5 subtasks optimal
   - < 2 = not worth parallelizing (sequential faster)
   - > 8 = too many context switches, use sequential

5. **Subtasks have clear boundaries:**
   - Each subtask touches different files
   - No complex merge logic needed
   - Can verify each independently
   - Example: "UI in `commands/ui.py`, scoring in `core/scoring.py`" → YES

### When to NOT use parallel:

- Refactoring single file (sequential, one logical flow)
- Features with dependencies (A depends on B's output)
- Any task where steps must happen in order
- Tasks with shared state mutations (need locking/careful merge)

### Test: "Independence Checklist"

```
□ Task A outputs X
□ Task B doesn't need X
□ Task C doesn't need X
□ Results can merge without conflicts?
□ Can verify each independently?

If 4+ boxes checked: Probably parallelizable
If < 3 boxes checked: Probably sequential
```

### Decision Logic:

```python
is_parallelizable = (
    ("split into:" in description OR "separate:" in description) AND
    tasks_are_truly_independent(plan) AND
    len(subtasks) >= 2 AND
    len(subtasks) <= 8 AND
    no_shared_state_mutations() AND
    ("--parallel" in flags OR inferred_parallel)
)
```

---

## PHASE 2: DECISION POINT 2 — Is this a vague/research task 6+ hours?

**Question:** Can this task be done autonomously by an agent figuring it out (ralph.py), or does it need a detailed plan upfront?

### Threshold 1: Task Vagueness (How clear is the spec?)

**Vague (ralph.py candidate):**
- Description is open-ended ("figure out", "research", "explore")
- Implementation path is unknown
- Multiple valid approaches, agent needs to try them
- "Let me see what works" tasks
- Example: "research how to improve detection accuracy" → VAGUE

**Clear (piv-implement candidate):**
- Exact files to modify are known
- Exact tasks are specified
- Implementation path is clear
- "Build X using pattern Y" tasks
- Example: "add a new leaderboard column for accuracy streak" → CLEAR

### Threshold 2: Time Estimate (How long will this take?)

**Short (< 6 hours):**
- Simple bug fix (< 1 hr)
- Single feature (2-4 hrs)
- Clear refactor (2-3 hrs)
- Example: "fix the typo", "add a button to UI" → SHORT

**Long (6+ hours):**
- Extensive research (8+ hrs)
- Major feature with unknowns (8+ hrs)
- Complex optimization (10+ hrs)
- Model training/tuning (2+ hrs)
- Example: "research and implement improved detection", "retrain YOLO model" → LONG

### Threshold 3: Explicit Flag

- User passes `--ralph`
- Example: `/harness --ralph "..."` → FORCE ralph.py

### When to Use ralph.py (AUTONOMOUS LOOP)

```
IF (vague description) AND (> 6 hours) [AND (--ralph flag)]:
  → Use ralph.py --worktree
```

**Decision Logic:**

```python
use_ralph = (
    is_vague_description(description) AND
    estimated_time_hours > 6 AND
    (
        "--ralph" in flags OR
        has_research_keywords() OR
        has_open_ended_language()
    )
)
```

### Examples:

| Description | Vague? | Time | ralph.py? |
|---|---|---|---|
| "fix typo on line 42" | Clear | < 1 hr | ❌ NO |
| "add drop-map claim button" | Clear | 2 hrs | ❌ NO |
| "refactor scoring system" | Clear | 4 hrs | ❌ NO |
| "research better detection" | Vague | 8 hrs | ✅ YES |
| "figure out YOLO edge cases" | Vague | 10 hrs | ✅ YES |
| "improve accuracy (method TBD)" | Vague | 6 hrs | ✅ YES |
| "add feature X using pattern Y" | Clear | 8 hrs | ❌ NO (use piv-implement) |

---

## PHASE 3: DECISION POINT 1 — Is validate.py a hard FAIL?

**Question:** Did validate.py exit with code 0, or non-zero?

### Threshold (Binary):

```
IF exit_code == 0:
  → PASS (continue to next check)
  
IF exit_code != 0:
  → FAIL (stop harness, user must fix)
```

**What causes FAIL:**
- Security check found secrets (`.env` file staged)
- Ruff lint found syntax errors
- Ruff lint found import issues
- Skill sync failed (can't update agent skills)

**What doesn't cause FAIL:**
- Code review CONCERNS (still let user decide)
- Functional test failures (user can debug)
- Code-sparring findings (user can ignore)

**User's responsibility after FAIL:**
- Read error message
- Fix the issue (remove secret, fix import, etc.)
- Re-run `python ai-hub/gates/validate.py` locally
- Re-run `/harness` command

---

## PHASE 3: DECISION POINT 2 — Are there CONCERNS from any reviewer?

**Question:** Did `/review`, `/verify`, or `/code-sparring` report CONCERNS (not just PASS)?

### Threshold (Vote):

```
IF review = PASS AND verify = PASS AND code-sparring = PASS:
  → All checks passed, continue to Phase 4
  
IF review = CONCERNS OR verify = CONCERNS OR code-sparring = CONCERNS:
  → At least one concern found
  → Print all concerns
  → Ask user: "Fix or accept risk?"
```

### How User Decides (Accept or Fix):

**User chooses "FIX":**
- Go back to Phase 2
- Re-run `/harness` with updated task description
- Or manually edit the code and re-run `/harness`

**User chooses "ACCEPT":**
- Note the risk
- Continue to Phase 4
- Document in /update-memory what risk was accepted

### Examples of CONCERNS vs PASS:

**PASS:**
- review: "All AGENTS.md rules met"
- verify: "All tests pass, feature works"
- code-sparring: "No bugs found"

**CONCERNS:**
- review: "Line 42 uses os.path instead of pathlib.Path"
- verify: "Concurrent test times out under load"
- code-sparring: "Potential memory leak in iteration 3"

---

## PHASE 4: DECISION POINT 1 — Should /consolidate-memory run?

**Question:** Are there multiple related lessons learned?

### Threshold (ANY of these):

1. **Number of lessons:**
   - 3+ lessons created by /update-memory → YES
   - < 3 lessons → NO

2. **Lesson relationships:**
   - Lessons touch same system ("async pool" + "DM queue" + "rate limits") → YES
   - Lessons are in different domains (UI lesson + database lesson) → NO

3. **Lesson complexity:**
   - Any lesson is "complex" (multi-faceted, many connections) → YES
   - All lessons are simple (single rule, single pattern) → NO

4. **User explicit flag:**
   - User says "consolidate this" or "these are related" → YES

### Decision Logic:

```python
run_consolidate = (
    (len(lessons) >= 3) AND
    (lessons_are_related(lessons) OR lessons_span_same_system(lessons))
) OR (
    "consolidate" in user_input
)
```

### When to Skip:

- Only 1-2 lessons learned
- Lessons are in completely different domains
- Lessons don't have clear connections

### Examples:

**YES, run consolidate:**
- 3 lessons: "async pool limits", "DM queue pattern", "rate limit checks"
  → All are infrastructure patterns, related
- 4 lessons: "Windows pathlib", "cross-platform Path", "relative paths", "absolute paths"
  → All are path-handling patterns, clearly related

**NO, skip consolidate:**
- 2 lessons: "async pool limits", "UI button styling"
  → Unrelated domains, not worth consolidating
- 1 lesson: "always use semaphore"
  → Single lesson, nothing to consolidate

---

## COMPLETE DECISION TREE (Pseudo-code)

```
harness(description, flags):
  
  # PHASE 1
  run_brainstorm()
  run_piv_plan()
  
  if has_research_keywords(description) OR "--research" in flags:
    run_wave_analyst()
  
  # PHASE 2
  if is_parallelizable(plan) AND "--parallel" in flags:
    path = split_and_verify()
  
  elif is_vague(description) AND estimated_time > 6 hours AND "--ralph" in flags:
    path = ralph_worktree()
  
  else:
    path = piv_implement()
  
  # PHASE 3
  review_result = run_review()
  verify_result = run_verify()
  validate_result = run_validate()
  sparring_result = run_code_sparring()
  
  if validate_result.exit_code != 0:
    halt("Fix validation errors, then re-run")
  
  if any([review_result, verify_result, sparring_result] == CONCERNS):
    user_choice = ask_user("Fix or accept risk?")
    if user_choice == "fix":
      goto PHASE 2 (re-implement)
    elif user_choice == "accept":
      note_concerns()
  
  # PHASE 4
  run_codify()
  run_update_memory()
  
  if len(lessons) >= 3 AND lessons_related():
    run_consolidate_memory()
  
  print("Ready to push. All done.")
```

---

## Quick Reference: All Thresholds

| Decision | Threshold | Trigger |
|----------|-----------|---------|
| Run /wave-analyst? | Task has research keywords OR > 5 plan tasks OR has unknowns | YES if ANY |
| Use /split-and-verify? | Subtasks independent + 2-8 tasks + --parallel flag | ALL must be true |
| Use ralph.py? | Vague description + > 6 hours + --ralph flag | ALL must be true |
| Use /piv-implement? | Clear description + < 6 hours + NOT parallel/ralph | Default if others don't match |
| validate.py FAIL? | Exit code ≠ 0 | STOP if true |
| CONCERNS found? | ANY of review/verify/code-sparring say CONCERNS | Ask user if true |
| Run /consolidate-memory? | 3+ lessons + related + --consolidate or user says so | YES if ANY |

---

**Last updated:** 2026-06-22  
**Status:** All decision thresholds documented with exact criteria
