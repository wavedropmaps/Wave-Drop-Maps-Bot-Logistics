---
name: prompt-engineering-deep-dive
description: Deep questioning and pushback for prompt engineering. When you type "95" at the end of any message containing a prompt, question, or idea, this skill activates to ask clarifying questions, challenge assumptions, and iterate toward a stronger final prompt. Use this whenever you want to stress-test your prompt before shipping it — especially for API calls, complex tasks, or anything mission-critical. The skill will keep you honest about what you're actually trying to accomplish.
---

# Prompt Engineering Deep Dive

A skill for rigorously questioning and stress-testing prompts before they ship.

## How It Works

**Trigger:** Type "95" at the end of any message containing a prompt, question, or idea you want to refine.

When triggered, the skill goes into deep-dive mode on that **specific context** and:

1. **Asks 4-5 clarifying questions** to fully understand what you're building
2. **Gives direct pushback** on approach, clarity, and technique
3. **Iterates with you** until the prompt is tight and actually works

## The Questioning Framework

When you trigger with "95", I'll ask about:

### Clarity & Intent
- What's the actual goal (not just the surface ask)?
- Who/what uses this prompt (Claude API? Direct chat? Another LLM)?
- What does success look like, specifically?

### Constraints & Context
- What data/context is feeding into this?
- Are there edge cases or failure modes you're worried about?
- What happens if the model misunderstands?

### Technique & Structure
- Is the prompt over-engineered or under-specified?
- Would examples, step-by-step thinking, or output formatting help?
- Is there a better technique for this kind of task?

## The Pushback

I'll challenge:
- **Ambiguity** that'll confuse the model
- **Over-complexity** you don't need
- **Missing context** the model needs to succeed
- **Better techniques** that would actually work better
- **Whether you're solving the real problem** (not just the stated one)

## Iteration Loop

This isn't a one-pass review. Once we identify issues, we refine together:
- You refine the prompt based on questions
- I poke holes again
- Repeat until you're confident it'll work

## When to Use This

- Building an API call with specific output requirements
- Engineering a complex prompt for a high-stakes task
- Testing a prompt before shipping it to production
- Iterating on something that isn't working yet
- Challenging whether your approach is the best one

## When NOT to Use This

- Simple one-off questions that don't need refinement
- Tasks where you just need a quick answer
- Casual conversation (save your "95" for the serious stuff)
