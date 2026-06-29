#!/usr/bin/env python3
"""
wave_sync.py — safe git sync mechanics for the Wave Management Bot repo.

This is the DETERMINISTIC engine behind the `website-sync` skill. It does the
parts that are always the same and always safe; the judgment parts (merge
strategy, conflict resolution, stopping the live bot) stay with the agent/skill.

Cross-platform (Windows-first per repo rules): uses pathlib + subprocess only,
no third-party deps.

Subcommands:
  status   Assess divergence with origin, classify changes (code vs runtime),
           check whether the live bot_database.db is locked, and whether a
           backup branch exists. Read-only — run this FIRST, always.
  backup   Create a timestamped backup branch at HEAD and print its name.
  push     Commit everything (real changes + runtime churn) and push, then
           verify local == remote. Refuses if behind origin (pull first).

What it deliberately does NOT do: merge, resolve conflicts, overwrite the live
DB, or stop processes. Those need the skill's playbook.
"""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent  # ai-hub/scripts -> repo root
# Main live DB differs per repo (Management: bot_database.db, Logistics: bot.db).
# Auto-detect so this one script works verbatim in either repo.
_DB_CANDIDATES = ["bot_database.db", "bot.db", "wave_bot.db"]
DB_FILE = next((REPO / c for c in _DB_CANDIDATES if (REPO / c).exists()),
               REPO / "bot_database.db")

# Files that are machine-generated churn, not real edits. Used to decide whether
# uncommitted changes are "just the bot/supervisor writing" (safe to commit) or
# real source the agent should look at. Hard-won list from real merges.
RUNTIME_PATTERNS = [
    r"\.db$", r"\.db-wal$", r"\.db-shm$", r"\.log$",
    r"wave_logging_local/", r"website/data/.*\.json$", r"json_data/",
    r"database_backups/", r"_worker\.js$",           # supervisor rewrites TUNNEL_URL
    r"twitter_feed", r"duties_totals", r"guilds\.json", r"last_backup",
    r"_rank_snapshot", r"\.wrangler/", r"data/.*\.db$",
    # Logistics-bot runtime dirs (harmless no-ops in the Management repo):
    r"/?Logs/", r"queue_images/", r"proof_assets/", r"hitl_pending/",
    r"map_requests", r"route_files/",
]
_RUNTIME_RE = re.compile("|".join(RUNTIME_PATTERNS))


def git(*args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=str(REPO),
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"git {' '.join(args)} failed ({r.returncode})")
    return r.stdout.strip()


def is_runtime(path: str) -> bool:
    return bool(_RUNTIME_RE.search(path))


def db_locked() -> bool:
    """True if bot_database.db can't be opened exclusively (bot is running)."""
    if not DB_FILE.exists():
        return False
    try:
        # Exclusive open fails on Windows if another process holds the file.
        with open(DB_FILE, "r+b"):
            pass
        return False
    except OSError:
        return True


def changed_files() -> list[str]:
    # Raw output — do NOT strip(): the leading space of the first porcelain
    # line ("  M path") is significant, and stripping it shifts the path.
    r = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO),
                       capture_output=True, text=True)
    files = []
    for line in r.stdout.splitlines():
        if not line:
            continue
        # porcelain v1: XY<space>path  (rename shows "old -> new")
        p = line[3:]
        if " -> " in p:
            p = p.split(" -> ", 1)[1]
        files.append(p.strip().strip('"'))
    return files


def ahead_behind() -> tuple[int, int]:
    out = git("rev-list", "--left-right", "--count", "origin/master...HEAD")
    behind, ahead = (out.split() + ["0", "0"])[:2]
    return int(behind), int(ahead)


def cmd_status() -> None:
    git("fetch", "-q", "origin")
    behind, ahead = ahead_behind()
    print(f"== sync vs origin/master ==  behind={behind}  ahead={ahead}")
    if behind:
        print("\nincoming commits:")
        print(git("log", "--oneline", f"HEAD..origin/master") or "  (none)")
        print("\nincoming NON-runtime (code/website) files:")
        inc = git("diff", "--name-only", "HEAD..origin/master").splitlines()
        code = [f for f in inc if not is_runtime(f)]
        for f in code[:60]:
            print(f"  {f}")
        if not code:
            print("  (none — incoming is all runtime churn)")

    files = changed_files()
    runtime = [f for f in files if is_runtime(f)]
    real = [f for f in files if not is_runtime(f)]
    print(f"\n== local uncommitted ==  total={len(files)}  "
          f"real-code={len(real)}  runtime-churn={len(runtime)}")
    for f in real[:60]:
        print(f"  REAL: {f}")

    print(f"\n== live bot DB ==  bot_database.db locked (bot running)? "
          f"{'YES' if db_locked() else 'no'}")
    backups = [b.strip() for b in git("branch", "--list", "backup-*").splitlines()]
    print(f"== backups ==  {len(backups)} backup branch(es) present")

    print("\n== guidance ==")
    if behind and ahead:
        print("  DIVERGED — use the website-sync skill's PULL playbook (backup, "
              "pick strategy, protect live DB, restart if backend changed).")
    elif behind:
        print("  Behind only — a pull is needed. If the live DB is locked and the "
              "incoming commit touches bot_database.db, use the skill's stop/restore steps.")
    elif ahead or files:
        print("  Ahead / local changes — `wave_sync.py push` will commit + push safely.")
    else:
        print("  In sync, nothing to do.")


def cmd_backup() -> None:
    # Date is intentionally read from git (no system clock dep needed here).
    short = git("rev-parse", "--short", "HEAD")
    name = f"backup-{short}-{git('rev-list', '--count', 'HEAD')}"
    git("branch", "-f", name, "HEAD")
    print(f"backup branch created: {name}")


def cmd_push(message: str | None) -> None:
    git("fetch", "-q", "origin")
    behind, _ = ahead_behind()
    if behind:
        raise SystemExit("REFUSING: you are behind origin. Pull first "
                         "(use the website-sync skill PULL playbook), then push.")
    cmd_backup()
    git("add", "-A")
    files = changed_files()
    if files or git("diff", "--cached", "--name-only"):
        msg = message or "Sync local state (code + runtime)"
        r = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(REPO),
                           capture_output=True, text=True)
        # commit may be a no-op if nothing staged; ignore that case
    subprocess.run(["git", "push", "origin", "master"], cwd=str(REPO))
    git("fetch", "-q", "origin")
    behind, ahead = ahead_behind()
    print(f"\npost-push sync: behind={behind} ahead={ahead} "
          f"({'OK — identical' if behind == ahead == 0 else 'NOT in sync — check above'})")
    if DB_FILE.exists() and DB_FILE.stat().st_size > 50 * 1024 * 1024:
        print("note: bot_database.db > 50MB — GitHub warns but accepts it "
              "(intentional full-sync). Consider Git LFS if it's ever rejected.")


def cmd_pre_push_check() -> int:
    """Safety gate for the git pre-push hook. Returns an exit code:
    0 = allow the push, non-zero = block it.

    Blocks ONLY when you're behind origin (a plain push would clobber the other
    machine's work or be rejected). Normal in-sync / ahead-only pushes pass."""
    try:
        git("fetch", "-q", "origin")
    except SystemExit:
        print("[pre-push] warning: could not fetch origin — allowing push.")
        return 0
    behind, ahead = ahead_behind()
    if behind == 0:
        return 0  # in sync or ahead -> safe, allow
    print("\n" + "=" * 64)
    print(f"[pre-push] BLOCKED - you are {behind} commit(s) BEHIND origin/master.")
    print("Pushing now would need a careful merge (and where the DB is tracked,")
    print("protecting the live DB). Don't force-push - you'd lose the other machine.")
    print("\nFix: ask Claude to run the `repo-sync` skill -")
    print('     e.g. "pull the changes" / "smart sync the repo".')
    print("=" * 64)
    if sys.stdin.isatty():
        try:
            ans = input("Override and push anyway? [y/N] ").strip().lower()
        except (EOFError, OSError):
            ans = ""
        return 0 if ans.startswith("y") else 1
    return 1  # non-interactive (agent/CI) -> block


def cmd_install_hook() -> None:
    """Activate the TRACKED hook via core.hooksPath (no copying). This way the
    hook lives in git, updates travel automatically on pull, and there's nothing
    to re-install when it changes. Run once per machine — git intentionally never
    auto-activates a cloned repo's hooks (security), so one command is unavoidable."""
    rel = "ai-hub/scripts/hooks"
    git("config", "core.hooksPath", rel)
    hook = REPO / rel / "pre-push"
    try:
        hook.chmod(0o755)  # needed on Mac/Linux; harmless on Windows
    except OSError:
        pass
    # Retire any old copied hook so there's no confusion about which one runs.
    git_dir = Path(git("rev-parse", "--git-dir"))
    if not git_dir.is_absolute():
        git_dir = REPO / git_dir
    old = git_dir / "hooks" / "pre-push"
    if old.exists():
        try:
            old.unlink()
        except OSError:
            pass
    print(f"activated tracked hook via core.hooksPath -> {rel}")
    print("The hook now lives in git; updates arrive automatically on pull.")
    print("(One command per machine — git won't auto-activate cloned hooks, by design.)")


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    if cmd == "status":
        cmd_status()
    elif cmd == "backup":
        cmd_backup()
    elif cmd == "push":
        msg = None
        if "-m" in args:
            msg = args[args.index("-m") + 1]
        cmd_push(msg)
    elif cmd == "pre-push-check":
        raise SystemExit(cmd_pre_push_check())
    elif cmd == "install-hook":
        cmd_install_hook()
    else:
        print(__doc__)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
