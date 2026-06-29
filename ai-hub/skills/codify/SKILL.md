---
name: codify
description: The write half of the goals + decisions memory system. Run when real work finishes to reconcile goal statuses to reality and append durable decisions to ai-hub/decisions.log. Trigger on "/codify", "codify this", "record what we decided", or when wrapping up a chunk of work.
---
# Codify — reconcile goals & log decisions

Markdown files do not update themselves. This is the deliberate write step the user
runs when meaningful work finishes. Do NOT run it on every turn — only when there is
real, completed change to record.

## What to do

1. **Reconcile goal files** in `ai-hub/memory/global-memory/goals/`:
   - For work done this session, update the matching goal's `status:`
     (`backlog` → `in-progress` → `review` → `done`) to reflect reality.
   - Tick (`- [x]`) acceptance criteria that are now satisfied.
   - If a brand-new line of work started and has no goal, offer to create one from
     `ai-hub/memory/global-memory/goals/_TEMPLATE.md` (next `NN-`, kebab name). Don't create noise for
     trivial edits.

2. **Append durable decisions** to `ai-hub/decisions.log`, newest at the bottom,
   one line each, EXACT format:
   ```
   YYYY-MM-DD | system | decision | why | rejected: alternatives
   ```
   Only log a decision a future agent would otherwise re-litigate (an architecture
   or approach choice). Skip routine edits. Never edit or delete existing lines.
   Full bug/lesson post-mortems belong in `ai-hub/memory/global-memory/context/`
   (the Context Protocol), not here.

3. **Report in chat** exactly what changed: which goals moved status, which criteria
   were ticked, which decision lines were appended.

4. **Do NOT commit or push** unless the user explicitly asks.

5. **If nothing meaningful happened**, say so plainly and change nothing.

## Date
Use today's real date for `created:` / log lines (the environment provides it).
