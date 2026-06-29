---
name: decision-framework
description: Rigorous analysis framework for any non-trivial decision, feature proposal, architecture change, or system design. Use whenever evaluating code changes, database schemas, API design, infrastructure, feature proposals, or any choice with hidden assumptions and failure modes. The skill applies an analytical lens that surfaces assumptions, failure points, and alternatives — stress-testing ideas before committing. Triggers on design questions, "should we...", proposals, and situations where you want expert-level thinking.
compatibility: None
---

# Decision Framework

A structured analytical framework for evaluating any proposal, architecture decision, system interaction, or non-trivial choice. Before recommending or implementing anything significant, run it through this lens.

## When to Use

- **Feature proposals**: "Should we add caching to this endpoint?" — Analyze assumptions, failure modes, alternatives
- **Architecture decisions**: "Migrate from REST to GraphQL?" — Stress-test the tradeoff
- **Code/system changes**: "Refactor this module?" — Identify root causes and what could break
- **API design**: "What should this endpoint return?" — Find hidden coupling and edge cases
- **Database changes**: "Add an index? Denormalize this table?" — Surface migration risks and performance implications
- **Infrastructure/DevOps**: "Switch to Kubernetes? Add a message queue?" — Evaluate operational complexity
- **Specs/design docs**: When you want to validate that a design actually covers the edge cases

## The Four-Section Framework

Always structure your analysis into exactly these four sections. This forces rigor:

### 1. **Best Analysis**
Your core insight after stress-testing the idea. What's actually true here, and what does it mean for the Wave-Management-Bot?

- Identify the core win or the real problem it solves
- Surface the key constraints (guild multiplicity, async DB ops, DM queue semantics, rate limits, etc.)
- Call out what makes this specific to Wave-Management-Bot (3-guild structure, the staff coordination model, etc.)
- Be concise: 2-3 tight paragraphs, not a novel

### 2. **Assumptions to Validate**
Hidden or stated assumptions the proposal rests on. The things that would break the whole idea if they're wrong.

- What about the current codebase, database schema, or Discord API are you assuming to be true?
- What about user behavior (staff members, guild structure, daily drops) are you assuming?
- Are these assumptions actually documented, or just folklore? (Check `AGENTS.md`, memory, code comments, recent git history)
- List them explicitly — force the user to say "yes, that's confirmed" or "actually no, that's wrong"

### 3. **Alternative Approaches**
Other valid ways to solve the same problem. Pick 2-3.

- What if you solved it at a different layer (database vs. command vs. background task)?
- What if you leaned on Discord features differently (reactions, threads, role color coding)?
- What if you punted on this entirely and addressed a symptom instead of the root cause?
- For each: briefly note the trade-off (simpler but less flexible, more maintainable but slower, etc.)

### 4. **Expert Disagreements**
What would a Discord bot architect, a systems designer, and a staff-coordination expert each argue about this?

- Would a systems person worry about consistency? Latency? Cascading failures?
- Would a Discord expert worry about rate limits, API contract changes, or guild-specific behavior?
- Would a staff-coordination expert worry about fairness, transparency, or game-ability?
- Flag the real tensions, not strawmen

## Notes for the Analyst

- **Cite sources and code.** If you reference a system, architecture pattern, or tradeoff, point to the relevant code, docs, or memory so the user can verify.
- **Don't shy away from "this is risky."** If you spot a genuine failure point (race condition, cascading failure, missing error handling, performance cliff), name it. That's the whole point.
- **Conciseness matters.** Each section should be readable in 30 seconds. If you're writing paragraphs of background, assume the user knows their own codebase — cut it.
- **Question clarity.** If the original request is unclear or under-specified, call it out in the "Assumptions" section. Make the user think harder.

## Example Structure

```
# Analysis: [Brief Title]

## 1. Best Analysis
[2-3 sentences: the core insight]

## 2. Assumptions to Validate
- Assumption A: [Explain what this is, how to verify]
- Assumption B: [...]

## 3. Alternative Approaches
**Option A:** [Brief name + trade-off]
**Option B:** [...]
**Option C:** [...]

## 4. Expert Disagreements
- **Systems architect** would argue: [consistency/latency/failure mode concern]
- **Database expert** would argue: [performance/scaling/migration concern]
- **Security engineer** would argue: [vulnerability/threat model concern]
```
