#!/usr/bin/env python3
import sys
import shutil
import subprocess
import os

def get_repo_root():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(script_dir, '..', '..'))

def main():
    print("Running Validation Gate for Wave Logistics Bot...")
    
    repo_root = get_repo_root()
    script_dir = os.path.dirname(os.path.abspath(__file__))

    print("Running Security Check...")
    try:
        subprocess.run(
            [sys.executable, os.path.join(script_dir, "security_check.py")],
            cwd=repo_root,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print("Security check failed.")
        sys.exit(e.returncode)

    print("Linting Commands/, Tasks/, utils/, and Database/...")

    # Check if uvx is available
    if shutil.which("uvx") is None:
        print("uvx could not be found. Please ensure uv is installed.")
        sys.exit(1)

    try:
        # Run ruff check using uvx
        subprocess.run(
            ["uvx", "ruff", "check", "--ignore=E701,E702,E722,F841,F811,F541,F401,E402,E721", "Commands/", "Tasks/", "utils/", "Database/"], 
            cwd=repo_root, 
            check=True
        )
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)

    print("Validation passed! You are clear to proceed.")

if __name__ == "__main__":
    main()
