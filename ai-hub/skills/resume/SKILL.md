---
name: resume
description: >
  Resume an interrupted session by reading .claude/progress.json and continuing from
  exactly where the last session stopped. Use this skill whenever the user says /resume,
  "pick up where we left off", "continue from last time", "what were we doing", or
  starts a session that has a progress.json file present. Always check for progress.json
  at the start of any session before doing any exploration — if it exists, read it first.
---

# Resume Skill

Pick up an interrupted session without re-doing exploration. The previous session left a checkpoint in `.claude/progress.json` — read it and continue from the exact next action it recorded.

## Step 1: Read the checkpoint

```bash
cat .claude/progress.json
```

If the file doesn't exist:
> No checkpoint found at `.claude/progress.json`. What would you like to work on?

Stop there and wait for the user's response.

## Step 2: Summarise and confirm

Show the user a compact summary — don't paste the raw JSON:

```
Resuming from checkpoint (2026-06-19 10:34 UTC)

Task: Add cancel flow to surge route commands matching the loot route pattern

Done:
  ✓ Read cancelroute implementation (commands/loot_route_commands.py)
  ✓ Added cancelsurge with 3-button confirm flow
  ✓ Updated database_surge.py cancel handler

Next: Test the cancel flow end-to-end, then commit and push

Files touched: commands/surge_route_commands.py, database_surge.py

Continuing now...
```

Then immediately start the next action — don't wait for the user to say "go ahead" unless the next action is destructive (a push, a delete, a migration).

## Step 3: Continue without re-exploring

The `completed_steps` and `files_modified` tell you what's already been done. Don't re-read files that were already read and unchanged. Jump straight to `next_action`.

If the next action requires reading a file you haven't seen this session, read only that file — not the whole codebase.

## Step 4: Checkpoint again after each step

Once you complete the next action, write a new checkpoint immediately (use the checkpoint skill). Keep checkpointing as you go so the session stays resumable at all times.

## Step 5: Mark complete

When the overall task is done, update the checkpoint with `"status": "complete"` and let the user know. A completed checkpoint is kept (not deleted) so there's a record of what was done.
