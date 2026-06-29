# P-0001 — Wave Logistics Bot: Public Dev Repo Setup
**Date:** 2026-06-29  
**Status:** Approved (auto-mode)  
**Design doc:** `docs/superpowers/specs/2026-06-29-wave-logistics-dev-repo-design.md`

## Goal
Create a clean public dev repo at `C:\Users\kiere\Desktop\Wave Logistics Bot Dev` → https://github.com/wavedropmaps/Wave-Drop-Maps-Bot-Logistics — replicating the Wave Management Bot Dev pattern. Single clean commit, no history. Private repo untouched.

## Files to Create/Modify
| Action | Path |
|---|---|
| CREATE | `C:\Users\kiere\Desktop\Wave Logistics Bot Dev\` (new folder) |
| CREATE | `C:\Users\kiere\Desktop\Wave Logistics Bot Dev\.gitignore` |
| CREATE | `C:\Users\kiere\Desktop\Wave Logistics Bot Dev\.git\hooks\pre-push` |
| CREATE | `C:\Users\kiere\Desktop\Wave Logistics Bot\ai-hub\scripts\sync_to_dev.py` |

## Implementation Steps

### Step 1 — Create dev folder and init git
```powershell
New-Item -ItemType Directory "C:\Users\kiere\Desktop\Wave Logistics Bot Dev"
cd "C:\Users\kiere\Desktop\Wave Logistics Bot Dev"
git init
git remote add origin https://github.com/wavedropmaps/Wave-Drop-Maps-Bot-Logistics.git
git checkout -b main
```

### Step 2 — Write .gitignore to dev folder
Key additions vs. prescribed: added `Logs/` (capital L — actual folder name).

### Step 3 — Write sync_to_dev.py into private repo
File: `C:\Users\kiere\Desktop\Wave Logistics Bot\ai-hub\scripts\sync_to_dev.py`

Key additions vs. prescribed:
- `"Logs"` added to `EXCLUDE_PARTS` (capital L — Python string match is case-sensitive)
- `"hitl_pending"`, `"proof_assets"`, `"queue_images"` added to `EXCLUDE_PARTS` (runtime user data)

### Step 4 — Write pre-push security hook
File: `C:\Users\kiere\Desktop\Wave Logistics Bot Dev\.git\hooks\pre-push`
Must be executable (Git Bash handles this on Windows automatically for hooks).

### Step 5 — Run sync script
```powershell
cd "C:\Users\kiere\Desktop\Wave Logistics Bot"
python ai-hub/scripts/sync_to_dev.py
```
STOP if security scan reports violations. Do NOT push.

### Step 6 — Verify no sensitive files tracked
```bash
cd "C:\Users\kiere\Desktop\Wave Logistics Bot Dev"
git ls-files | grep -E "\.db$|\.db-wal$|\.db-shm$|\.env$|credentials|tunnel_credentials|cloudflared_config|\.bat$|\.ps1$"
```
Must return empty. If anything appears, remove + re-sync.

### Step 7 — Wipe history and force-push single clean commit
```bash
git checkout --orphan clean-start
git add -A
git commit -m "chore: initial clean public dev repo — Wave Logistics Bot"
git branch -D main
git branch -m main
git push origin main --force
```

## Validation Steps
1. `python ai-hub/gates/validate.py` — must exit 0 (HARD GATE)
2. `git log --oneline` in dev folder — must show exactly 1 commit
3. `git ls-files | grep -E "\.db$|credentials|\.env$|cloudflared"` — must return empty
4. Confirm push landed at https://github.com/wavedropmaps/Wave-Drop-Maps-Bot-Logistics on `main`

## Acceptance Criteria
- [ ] Dev folder exists at `C:\Users\kiere\Desktop\Wave Logistics Bot Dev`
- [ ] `.gitignore` and pre-push hook in place
- [ ] `sync_to_dev.py` exists in private repo's `ai-hub/scripts/`
- [ ] Security scan passed (0 violations)
- [ ] `git log` shows exactly 1 commit
- [ ] No secrets tracked in git ls-files
- [ ] GitHub repo shows clean `main` branch
- [ ] `validate.py` exits 0
