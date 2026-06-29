---
name: checkpoint
description: >
  Save progress to .claude/progress.json so work survives a session-limit interruption.
  Use this skill whenever the user invokes /checkpoint, or after any meaningful step in
  a multi-step task — file edited, feature implemented, test passing, commit made.
  Also use it proactively at the start of any task that will take more than 2-3 steps,
  so a fresh session can resume without re-exploring.
---

# Checkpoint Skill

Write a durable progress snapshot to `.claude/progress.json` so a future `/resume` session can continue exactly where this one stopped.

## When to checkpoint

Checkpoint after every meaningful unit of work:
- A file has been created or edited
- A feature or fix has been implemented
- A commit or push has been made
- A phase of a multi-step plan is complete
- Any time the user says `/checkpoint`

Also write an **initial checkpoint** at the start of any task with 3+ steps — this records the plan before anything can interrupt it.

## What to write

Collect this information (infer from context — don't ask the user):

- **task**: One sentence describing the overall goal
- **completed_steps**: Bullet list of what has been done so far (be specific — file paths, what changed)
- **files_modified**: List of file paths touched in this session
- **next_action**: The exact next step — specific enough that a fresh session can act on it without re-reading the codebase
- **timestamp**: Current UTC time in ISO 8601 format
- **status**: `"in_progress"` or `"complete"`

## How to write (atomic)

Write atomically to prevent a half-written file from corrupting the checkpoint:

```bash
# 1. Write to a temp file first
cat > .claude/progress.tmp << 'EOF'
{ ...json... }
EOF

# 2. Rename atomically (this is the safe step — rename is atomic on most filesystems)
mv .claude/progress.tmp .claude/progress.json
```

Always create `.claude/` if it doesn't exist:
```bash
mkdir -p .claude
```

## Output format

`.claude/progress.json`:
```json
{
  "task": "Add cancel flow to surge route commands matching the loot route pattern",
  "completed_steps": [
    "Read cancelroute implementation in commands/loot_route_commands.py (lines 340-410)",
    "Added cancelsurge command to commands/surge_route_commands.py with 3-button confirm flow",
    "Updated database_surge.py cancel_surge_assignment() to mark status=cancelled"
  ],
  "files_modified": [
    "commands/surge_route_commands.py",
    "database_surge.py"
  ],
  "next_action": "Test the cancel flow end-to-end, then commit and push to origin/master",
  "timestamp": "2026-06-19T10:34:00Z",
  "status": "in_progress"
}
```

## After writing

Confirm to the user in one line:
```
✓ Checkpoint saved — next: [next_action]
```

Do not narrate the process. Just write the file and confirm.
