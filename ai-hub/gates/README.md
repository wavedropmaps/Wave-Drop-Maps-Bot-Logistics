# ai-hub/gates — Validation & Pre-Commit Checks

Automated validation scripts and quality gates that run before commits or deployments.

## What goes here

- **Validation scripts** — `validate.py` (main gate run before every commit)
- **Security checks** — `security_check.py` (linting, secret detection, vulnerability scanning)
- **Pre-commit hooks** — custom checks that block broken commits

## Current files

- `validate.py` — Runs full validation suite; exit code 0 = pass, non-zero = fail
- `security_check.py` — Scans for security issues, secret leaks, code quality violations

## How to use

**Before claiming a task is done:**
```bash
python ai-hub/gates/validate.py
```
Exit code must be **0**. This is a STRICT VALIDATION RULE in AGENTS.md.

**For security audits:**
```bash
python ai-hub/gates/security_check.py
```

**In CI/CD:** These scripts should run automatically on every PR or commit.

## When to add

Add a new gate when:
- You want to automate a quality check that blocks broken commits
- You want pre-deployment validation (e.g., "no uncommitted changes")
- You want to catch categories of bugs early (e.g., missing imports, unused variables)

## Common patterns

- Exit code 0 = pass ✓
- Exit code non-zero = fail ✗
- Print clear error messages to stderr so agents understand what failed
- Log results to stdout so CI can capture them

## See also

- [AGENTS.md — Validation Rule](../../AGENTS.md) — "Never complete a feature or claim a task is done without running `python ai-hub/gates/validate.py`"
- [`ai-hub/scripts/`](../scripts/README.md) — developer automation tools
