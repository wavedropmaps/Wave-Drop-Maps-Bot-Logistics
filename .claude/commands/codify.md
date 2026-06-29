---
description: Reconcile ai-hub/memory/global-memory/goals/ statuses to reality and append durable decisions to ai-hub/decisions.log (the write half of the goals + decisions memory system).
---
Run the **codify** workflow — the deliberate write step of the goals + decisions
memory system. The full instructions are the source of truth in
`ai-hub/skills/codify/SKILL.md`; read it if you need detail. In short:

1. **Reconcile `ai-hub/memory/global-memory/goals/`** — for work actually completed this session, update
   each matching goal's `status:` (`backlog` → `in-progress` → `review` → `done`)
   and tick (`- [x]`) any acceptance criteria now satisfied. If a new line of work
   has no goal, offer to create one from `_TEMPLATE.md` (next `NN-`, kebab name).

2. **Append durable decisions** to `ai-hub/decisions.log`, newest at the bottom,
   one line each, EXACT format — never edit existing lines:
   `YYYY-MM-DD | system | decision | why | rejected: alternatives`
   Only log decisions a future agent would re-litigate. Full bug/lesson
   post-mortems go to `ai-hub/memory/global-memory/context/` instead.

3. **Report** exactly what changed (statuses moved, criteria ticked, lines appended).

4. **Do not commit or push** unless I explicitly ask.

5. If nothing meaningful happened, **say so and change nothing**.

Use today's real date for any dates.
