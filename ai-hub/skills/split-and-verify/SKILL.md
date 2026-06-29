---
name: split-and-verify
description: Split a multi-part task into parallel session prompts — each loops until its goal is met, then spawns a fresh subagent to verify before stopping
when_to_use: When the user describes multiple distinct bugs or changes that can be worked on in separate Claude sessions, especially when they say "give me prompts", "split this up", or "sessions for each"
---

## What this skill does

Takes a multi-part task and outputs one ready-to-paste session prompt per sub-task. Each prompt is:

1. **Self-contained** — no assumed context, all repo paths and file names included
2. **Goal-driven** — a numbered checklist of specific, testable criteria
3. **Loop-enforced** — the session keeps working until every goal criterion passes
4. **Subagent-verified** — at the end the session spawns a fresh Agent (no context from the session) with a cold verification prompt; only stops when that subagent returns PASS

## How to invoke

Say any of:
- "split this up into session prompts"
- "give me a prompt for each"
- "sessions for each of these fixes"
- or just list bugs/changes and ask for prompts

## Session prompt template (copy this structure for each sub-task)

```
REPO: [Path to Wave Logistics Bot]
GOAL (all must be true before you stop):
1. [specific, grep-checkable or visually verifiable criterion]
2. ...

CONTEXT:
[What system this touches. What changed and why. Current broken state → expected fixed state.]

FILES TO EDIT:
- path/to/file.py
- path/to/file.html

INSTRUCTIONS:
1. Read every file listed above in full before touching anything.
2. Make all changes required to satisfy every GOAL item.
3. After each edit, verify it (py_compile for Python, grep for strings, visual check for HTML).
4. Fix anything that still fails a goal criterion — keep looping until all pass locally.
5. Once all criteria look satisfied, spawn a fresh Agent with ZERO context from this session
   using exactly this prompt (copy verbatim, add no session context):

--- VERIFICATION PROMPT ---
[Self-contained prompt. Must include: repo path, files to check, each goal criterion as a yes/no question, instruction to return PASS if all yes or FAIL + list of what failed.]
--- END VERIFICATION PROMPT ---

6. If the subagent returns FAIL, fix the flagged items and spawn it again.
7. Only report DONE to the user when the subagent returns PASS.

RULES:
- Do not skip the subagent step.
- Do not pass any session context to the subagent — the prompt must stand alone.
- Do not stop if any goal criterion is still unmet.
```

## Tips for writing good goal criteria

- Make each criterion grep-able or file-checkable: "the string 'VBucks' no longer appears in lines 500-600 of events.html"
- Or visually checkable: "the shop header shows WP balance only, no VBucks balance"
- Avoid vague criteria like "looks right" — the subagent needs to verify cold

## Tips for writing the verification prompt

- Include the repo path
- List every file the session was supposed to touch
- Turn each GOAL item into a yes/no question the subagent can answer by reading the file
- Tell the subagent to return exactly "PASS" or "FAIL — [list of what failed]"
- Do NOT reference "the previous session" or "what was discussed" — the subagent has no context
