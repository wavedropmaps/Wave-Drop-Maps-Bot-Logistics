---
name: code-reviewer
description: Sub-agent persona for reviewing code changes against AGENTS.md rules and implementation plans.
---
# Code Reviewer Skill
This skill equips a sub-agent to review code.
1. Check diffs against the master branch.
2. Ensure validation gates (like `python ai-hub/gates/validate.py`) have been passed.
3. Enforce the strict rules listed in `AGENTS.md`.
