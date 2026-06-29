---
name: session-summary
description: >
  Wave Management Bot session summary writer. When a work session ends (or the
  user wants to wrap up / record what was done), write a concise markdown summary
  of the session and save it to ai-hub/memory/session-summaries/ named after the topic.

  Trigger this skill whenever the user says things like "save a summary",
  "write up what we did", "log this session", "summarise the chat", "record
  this", "session summary", or anything that sounds like they want to capture
  what happened in this conversation for later reference. Also trigger when
  wrapping up before a context clear. No scripts needed — this is a pure
  writing task.
---

# Session Summary

Saves a concise record of what happened in this session to `ai-hub/memory/session-summaries/`.
One file per session, named after the topic, so it's easy to find later.

**Summaries live in `ai-hub/memory/session-summaries/`** — tracked in git, syncs via push/pull.

---

## What to do

1. **Infer the topic** from the conversation. If it's not obvious, ask the user
   for a short title (e.g. "surge-route-hold-pool" or "market-research-skill").
   Convert it to kebab-case for the filename.

2. **Write the summary** to `ai-hub/memory/session-summaries/<topic>.md`.

3. **Tell the user** the file path so they know where it landed.

---

## Summary format

Use this exact structure — keep it tight, no waffle:

```markdown
# <Title>

**Date:** YYYY-MM-DD  
**Topic:** one-line description of what this session was about

---

## What we built / discussed

- bullet points — what actually happened

## Key decisions

- decision and why (skip this section if nothing notable)

## Files changed

- `path/to/file.py` — what changed and why (skip if no code was touched)

## Things to remember

- gotchas, constraints, non-obvious choices worth recalling next time
```

### Tips for each section

- **What we built / discussed** — be specific. "Added pending-pool to loot route
  auto-assign so maps hold when no maker is free" beats "worked on loot routes".
- **Key decisions** — include the *reason*, not just the outcome. Future-you needs
  the why.
- **Files changed** — list files that were actually edited. Skip if it was a
  pure planning/research session.
- **Things to remember** — the most valuable section. Tricky edge cases, IDs
  that keep coming up, constraints discovered during the session.

Omit any section that has nothing to say. Don't pad it.

---

## Example output

```markdown
# Surge Route Hold Pool

**Date:** 2026-06-15  
**Topic:** Added hold/pending-pool to surge route auto-assign (mirrors loot routes)

---

## What we built / discussed

- Added `surge_pending_maps` table to hold maps when no maker is free
- `find_next_available_user(allow_fallback=False)` returns None → hold
- `drain_pending_pool()` is lock-guarded, called on startup / done / new maker / away-return
- ⏳ reaction added to held maps so staff can see what's waiting

## Key decisions

- Oldest-first drain order (priority by hold time, not submission order) — keeps queue fair
- Shared the same logic pattern as loot routes rather than re-inventing

## Files changed

- `tasks/surge_routes.py` — added drain_pending_pool(), wired into 4 trigger points
- `database_surge.py` — new surge_pending_maps table + hold/drain helpers

## Things to remember

- drain is lock-guarded — don't call it from two places without the lock or you'll double-assign
- pending maps save the image to disk; path stored in DB so bot restarts don't lose it
```
