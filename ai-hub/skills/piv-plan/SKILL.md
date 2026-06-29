---
name: piv-plan
description: Strict planning skill for the Plan-Implement-Validate loop. Enforces rigorous requirement gathering before any code is written.
---
# PIV Plan Skill
Always use this skill before implementing new features or making architectural changes. This is Phase 1 of the PIV loop.

## Workflow
1. **Requirement Gathering**: Analyze the user's request. Ask clarifying questions until you fully understand the goals, edge cases, and scope.
2. **Context Discovery**: Read relevant codebase files and `ai-hub/memory/` docs to understand the current state.
3. **Artifact Generation**: Create a detailed plan artifact and save it in the `ai-hub/plans/` directory (e.g., `ai-hub/plans/P-XXXX-feature-name.md`).
   The plan MUST include:
   - High-level goals and context.
   - Exact files to be created or modified.
   - Detailed, step-by-step implementation instructions.
   - Specific validation and testing steps for the validate phase, including running `python ai-hub/gates/validate.py`.
4. **User Approval**: Present the plan to the user. **DO NOT** proceed to implementation without explicit user approval.
5. **Handoff**: Once the user approves the plan, instruct the user or a subagent to invoke the `piv-implement` skill, providing the exact file path to the generated plan artifact.
