---
name: deploy
description: Run the validation gate (if present), then commit and push to origin/master. Use when ready to ship changes to the live bot. Enforces validate → commit → push order. Never pushes if validation fails.
disable-model-invocation: true
---

## Deploy Skill — Wave Logistics Bot

**Order is mandatory:** validate → commit → push. Stop at first failure.

### Step 1 — Validation gate
If `ai-hub/gates/validate.py` exists, run:
```bash
python ai-hub/gates/validate.py
```
- If exit code is non-zero: **stop immediately**. Do not commit. Report what failed.
- If it doesn't exist, skip to Step 2.

### Step 2 — Stage and commit
- Run `git status` to show the user what will be committed.
- Ask the user for a commit message if they haven't provided one.
- Stage only the relevant changed files (never `git add -A` blindly — avoid staging `config.json`, `.env`, `bot.db`, `*.log`, or any file in `.gitignore`).
- Commit with the user's message.

### Step 3 — Push
```bash
git push origin master
```
- Report the push result.
- Remind the user to run `git pull` on the Windows machine to deploy.

### Safeguards
- Never push if validation failed.
- Never stage `config.json`, `.env`, `*.log`, `bot.db`, `credentials.json`, or any tunnel/token files.
- If anything looks wrong (merge conflict, diverged history, untracked secrets), stop and explain before proceeding.
