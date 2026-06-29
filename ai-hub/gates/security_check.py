#!/usr/bin/env python3
import sys
import os
import subprocess
import time

def get_repo_root():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, '..', '..'))

def check_git_diff():
    repo_root = get_repo_root()
    
    max_retries = 5
    for attempt in range(max_retries):
        try:
            # Check if protected files were modified (staged or unstaged)
            result = subprocess.run(
                ['git', 'status', '--porcelain', '-z', '-uall'],
                capture_output=True, 
                text=True, 
                cwd=repo_root,
                check=True
            )
            break  # Success
        except FileNotFoundError:
            print("Failed to run git status: git executable not found.")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            # If it's an index.lock issue, retry
            if e.stderr and "index.lock" in e.stderr:
                if attempt < max_retries - 1:
                    time.sleep(0.5)
                    continue
            # If it's any other error, or we ran out of retries, fail CLOSED immediately
            print(f"Failed to execute git status: {e.stderr or e}")
            sys.exit(e.returncode if e.returncode != 0 else 1)
    else:
        print("Failed to acquire git lock after multiple retries.")
        sys.exit(1)

    forbidden_basenames = {'.env', 'config.json', 'credentials.json'}
    forbidden_dirs = {'Models/'}
    
    parts = result.stdout.split('\0')
    files = []
    
    i = 0
    while i < len(parts) - 1:
        part = parts[i]
        if not part:
            i += 1
            continue
        status = part[:2]
        path = part[3:]
        files.append((status, path))
        
        # In -z mode, renamed or copied entries take two parts (new path, then old path)
        if status[0] in ('R', 'C') or status[1] in ('R', 'C'):
            i += 1 # skip old path
        i += 1

    violations = []
    for status, f in files:
        # Skip entirely-untracked files (status '??'). We only care about
        # staged, tracked-and-modified, or force-added protected files.
        # An untracked config.json in a backup dir is not a security concern.
        if status == '??':
            continue
        basename = os.path.basename(f)
        # A normal gitignored .env sitting on disk does NOT appear in this output, so editing
        # your own local .env never trips the gate. A protected file only shows up here if it
        # is staged, tracked-and-modified, or force-added (git add -f) — exactly what we block.
        # Check exact basename matches to catch nested files (e.g. nested/dir/config.json)
        if basename in forbidden_basenames:
            violations.append(f)
        # Check if the file is inside any forbidden directory
        for d in forbidden_dirs:
            if f.startswith(d) or f == d.rstrip('/'):
                violations.append(f)

    # De-duplicate violations just in case
    violations = sorted(list(set(violations)))

    if violations:
        print(f"SECURITY VIOLATION: Agent attempted to modify protected files: {', '.join(violations)}")
        sys.exit(1)
    print("Security check passed.")

if __name__ == "__main__":
    check_git_diff()
