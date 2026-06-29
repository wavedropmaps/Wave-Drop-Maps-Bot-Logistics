---
name: piv-validate
description: Strict validation skill for the Plan-Implement-Validate loop. Enforces rigorous testing and user-approval before claiming task completion.
---
# PIV Validate Skill
Always use this skill to complete the Plan-Implement-Validate (PIV) loop after the implementation phase is done. This is Phase 3 of the PIV loop.

## Workflow
1. **Plan Verification**: Review the original plan artifact (`ai-hub/plans/P-XXXX.md`). Verify that ALL step-by-step implementation instructions and requirements were met.
2. **Gate Enforcement**: Run `python ai-hub/gates/validate.py` and ensure a 0 exit code. **This is mandatory per AGENTS.md.**
3. **Automated/Local Testing**: Execute any relevant automated tests for the code modified. If automated tests cannot be run, rigorously test the functionality locally based on the validation steps in the plan.
4. **CRITICAL**: You must request and receive explicit user approval OR confirm completely successful automated verification before ever claiming a task is done.
5. **Completion**: Do not close out the task or assume completion if validation steps fail or are skipped. If they fail, return to the `piv-implement` phase.
