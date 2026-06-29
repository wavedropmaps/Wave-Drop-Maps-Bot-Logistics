"""
sync_to_dev.py — Sync between private and public dev repo.
Usage:
    python ai-hub/scripts/sync_to_dev.py          # push your changes → dev repo
    python ai-hub/scripts/sync_to_dev.py --pull   # pull dev changes → your main folder

The script never lets secrets, DBs, logs, or machine-specific files cross either direction.
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

PRIVATE_DIR = Path(r"C:\Users\kiere\Desktop\Wave Logistics Bot")
DEV_DIR     = Path(r"C:\Users\kiere\Desktop\Wave Logistics Bot Dev")
DEV_BRANCH  = "main"

EXCLUDE_PARTS = {
    ".git",
    ".gitignore",
    ".env",
    ".headroom",
    "__pycache__",
    "node_modules",
    "database_backups",
    "wave_logging_local",
    "logs",
    "Logs",           # actual folder name is capital-L on Windows
    "hitl_pending",   # runtime user data (pending review images)
    "proof_assets",   # runtime user data (submitted proof images)
    "queue_images",   # runtime user data (map request queue images)
    "Models",         # ML model weights — large binaries, never public
    ".ruff_cache",
    ".wrangler",
    ".tmp_screenshot",
    ".DS_Store",
    "screenshots",
    "data",
}

EXCLUDE_PATHS = {
    Path("ai-hub/scratch"),
    Path("ai-hub/deprecated/yolo-watermark-detector/weights"),
    Path("tooling/.venv"),
    Path("mac side stuff"),
}

EXCLUDE_ROOT_FILES = {
    ".env",
    "credentials.json",
    "tunnel_credentials.json",
    "cloudflared_config.yml",
    "staff_hub_port.txt",
    "deep_check.png",
    "bot_startup_check.log",
    "kill_port_5000.log",
    "dev_server.log",
    "commands_export.csv",
    "commands_export.md",
}

EXCLUDE_EXTENSIONS = {".db", ".db-wal", ".db-shm", ".pyc", ".pyo", ".log", ".err.log", ".bat", ".ps1", ".pt", ".safetensors"}
EXCLUDE_ROOT_PREFIXES = ("verification_", "bot_restart_", "bot_startup")

SECRET_FILENAMES = {
    ".env", "credentials.json", "tunnel_credentials.json", "cloudflared_config.yml",
}
SECRET_EXTENSIONS = {".db", ".db-wal", ".db-shm"}
SECRET_FOLDERS = {"database_backups", "wave_logging_local"}


def is_excluded(path: Path) -> bool:
    parts = path.parts
    if len(parts) == 1:
        name = parts[0]
        if name in EXCLUDE_ROOT_FILES:
            return True
        if any(name.startswith(p) for p in EXCLUDE_ROOT_PREFIXES):
            return True
    for part in parts:
        if part in EXCLUDE_PARTS:
            return True
    for ex_path in EXCLUDE_PATHS:
        try:
            path.relative_to(ex_path)
            return True
        except ValueError:
            pass
    if path.suffix in EXCLUDE_EXTENSIONS:
        return True
    return False


def security_scan_dev(base: Path) -> list[str]:
    violations = []
    for f in base.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(base)
        if rel.parts[0] == ".git":
            continue
        if f.name in SECRET_FILENAMES:
            violations.append(str(rel))
        if f.suffix in SECRET_EXTENSIONS:
            violations.append(str(rel))
        for folder in SECRET_FOLDERS:
            if folder in rel.parts:
                violations.append(str(rel))
                break
    return list(set(violations))


def sync_push():
    print("Syncing private → dev repo...")
    for src in PRIVATE_DIR.rglob("*"):
        rel = src.relative_to(PRIVATE_DIR)
        if is_excluded(rel):
            continue
        dst = DEV_DIR / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for dst in list(DEV_DIR.rglob("*")):
        rel = dst.relative_to(DEV_DIR)
        if rel.parts[0] == ".git":
            continue
        if is_excluded(rel):
            continue
        src = PRIVATE_DIR / rel
        if not src.exists() and dst.is_file():
            print(f"  Removing deleted file: {rel}")
            dst.unlink()

    print("Running security scan...")
    violations = security_scan_dev(DEV_DIR)
    if violations:
        print("\n SECURITY SCAN FAILED — sensitive files detected in dev folder:")
        for v in violations:
            print(f"  X {v}")
        print("\nAborting push. Remove these files before syncing.")
        sys.exit(1)
    print("  Security scan passed.")

    result = subprocess.run(["git", "add", "-A"], cwd=DEV_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"git add failed: {result.stderr}")
        sys.exit(1)

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=DEV_DIR)
    if result.returncode == 0:
        print("Nothing changed — dev repo is already up to date.")
        return

    subprocess.run(["git", "commit", "-m", "chore: sync from private repo"], cwd=DEV_DIR, check=True)
    subprocess.run(["git", "push", "origin", DEV_BRANCH], cwd=DEV_DIR, check=True)
    print("Done. Dev repo updated.")


def sync_pull():
    print("Pulling dev repo changes → private folder...")
    subprocess.run(["git", "pull", "origin", DEV_BRANCH], cwd=DEV_DIR, check=True)

    pulled_violations = []
    for src in DEV_DIR.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(DEV_DIR)
        if rel.parts[0] == ".git":
            continue
        if src.name in SECRET_FILENAMES or src.suffix in SECRET_EXTENSIONS:
            pulled_violations.append(str(rel))

    if pulled_violations:
        print("\n PULL SAFETY CHECK FAILED — dev repo contains sensitive files:")
        for v in pulled_violations:
            print(f"  X {v}")
        print("\nAborting pull.")
        sys.exit(1)

    for src in DEV_DIR.rglob("*"):
        rel = src.relative_to(DEV_DIR)
        if rel.parts[0] == ".git":
            continue
        if is_excluded(rel):
            continue
        dst = PRIVATE_DIR / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    print("Done. Changes copied to your private folder.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pull", action="store_true")
    args = parser.parse_args()
    if args.pull:
        sync_pull()
    else:
        sync_push()
