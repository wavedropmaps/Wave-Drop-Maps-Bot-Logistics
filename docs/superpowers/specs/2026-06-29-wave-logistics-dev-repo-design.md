# Wave Logistics Bot — Public Dev Repo Setup
**Date:** 2026-06-29  
**Status:** Approved (auto-mode, design confirmed)

## Goal
Create a clean public dev repo at `C:\Users\kiere\Desktop\Wave Logistics Bot Dev` → https://github.com/wavedropmaps/Wave-Drop-Maps-Bot-Logistics, replicating the Wave Management Bot Dev pattern.

## Approach
Single clean commit, no history. Private repo stays untouched. A sync script in the private repo copies only safe files; a pre-push hook on the dev repo blocks secrets at push time.

## Files to create
| File | Location |
|---|---|
| `.gitignore` | `Wave Logistics Bot Dev/` |
| `sync_to_dev.py` | `Wave Logistics Bot/ai-hub/scripts/` |
| `pre-push` hook | `Wave Logistics Bot Dev/.git/hooks/` |

## Exclusion list (confirmed for this bot)
- Secrets: `.env`, `.env.*`
- DBs: `bot.db`, `map_requests.db` (caught by `*.db`)
- Logs: `wave_logging_local/`, `Logs/` (capital L — see fix #1)
- Runtime data: `hitl_pending/`, `proof_assets/`, `queue_images/`
- Machine scripts: `run_bot.bat` (caught by `*.bat`)
- Models: `Models/` (gitignored)

## Fixes applied vs. prescribed script
1. **CRITICAL**: Added `"Logs"` (capital L) to `EXCLUDE_PARTS` — the actual folder name
2. **MEDIUM**: Added `hitl_pending`, `proof_assets`, `queue_images` to `EXCLUDE_PARTS`
3. **RESOLVED**: `server_config.json` is safe (Discord IDs only, no tokens)
4. **BELT**: Added `Logs/` to `.gitignore`

## Verification criteria
- `git log --oneline` shows exactly 1 commit
- `git ls-files | grep -E "\.db$|credentials|\.env$|cloudflared"` returns empty
- Push confirmed at https://github.com/wavedropmaps/Wave-Drop-Maps-Bot-Logistics on `main`
