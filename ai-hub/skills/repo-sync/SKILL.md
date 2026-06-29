---
name: repo-sync
description: >
  Safely sync this Wave Logistics Bot repo between machines and GitHub — pulling
  changes IN and committing + pushing changes OUT without breaking the running bot.
  Use whenever the user says "smart push", "sync the repo", "pull the changes",
  "merge and push", or otherwise wants to reconcile this repo with origin. Encodes
  the safety rules: always back up first, never force-push while behind (you'd lose
  the other machine's work), and restart the bot only when its code changed. NOTE:
  unlike the Wave Management Bot, this repo does NOT track its databases (bot.db,
  map_requests.db are gitignored), so the live-DB-overwrite danger does not apply
  here — making sync simpler.
---

# Repo Sync Skill — Wave Logistics Bot

Deterministic mechanics live in `ai-hub/scripts/wave_sync.py`. This skill is the
playbook. Always start with `status`.

```
python ai-hub/scripts/wave_sync.py status   # read-only assessment, run FIRST
python ai-hub/scripts/wave_sync.py backup    # timestamped backup branch
python ai-hub/scripts/wave_sync.py push      # commit + push + verify 0 0 (refuses if behind)
```

## The rules

1. **Back up before anything destructive** — a `backup-*` branch at HEAD. `push`
   does it automatically; before a pull/merge, run `backup` yourself.
2. **Never force-push while behind.** If you're behind origin, the other machine
   pushed something — pull/merge first, never `--force`, or you erase their work.
3. **Databases are NOT in git here** (`bot.db`, `map_requests.db` are gitignored),
   so a pull can't overwrite your live data. This is the big simplification vs the
   Management bot — no stop-the-bot-to-protect-the-DB dance needed.
4. **Runtime churn ≠ real work.** `Logs/`, `wave_logging_local/`, `queue_images/`,
   `proof_assets/`, `hitl_pending/`, `database_backups/`, `*.db` are machine-written.
   `status` separates these from real code edits.
5. **Restart the bot only if its code changed.** Changes under `Commands/`,
   `Tasks/`, `Database/`, `utils/`, or `Main.py` need a bot restart to go live.

## PUSH OUT (local → origin)
1. `wave_sync.py status`. If behind → do the PULL flow first.
2. `wave_sync.py push` (or `push -m "message"`). Backs up, commits, pushes, prints
   `behind=0 ahead=0`.
3. If bot code changed and the bot is running, restart it.

## PULL IN (origin → local)
1. `wave_sync.py status` and `wave_sync.py backup`.
2. `git pull` (or `git merge origin/master`). Because DBs aren't tracked, this is
   usually a clean fast-forward — no DB protection needed.
3. Resolve any code conflicts normally. Verify `behind=0 ahead=0`.
4. Restart the bot if its code changed.

## The pre-push safety hook (recommended)
Install once per machine:
```
python ai-hub/scripts/wave_sync.py install-hook
```
On every `git push`: safe (in sync / ahead) → passes silently; **behind** → blocked
with a pointer to this skill (interactive `[y/N]` override in a terminal; agent/CI
push blocked outright). The hook is only a guard — the judgment stays in this skill.
