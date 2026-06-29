---
name: stfu
description: Activate or toggle terse mode. Suppress all narration, progress updates, and "I'm now doing X" commentary—output only brief one-sentence summaries when work completes. Use when you want focused responses without running commentary. Invoke again to deactivate terse mode.
compatibility: No special tools required
---

# STFU — Terse Mode Toggle

## What It Does
Activates **terse mode**: suppresses narration, progress updates, and task commentary. Claude outputs only brief one-sentence summaries when work completes, then nothing else.

## How to Use

**First invoke:** Terse mode ON  
**Second invoke:** Terse mode OFF

## The Mode Instruction
When active, this instruction is injected into your context:

> **TERSE MODE ACTIVE:** No narration, no progress updates, no "I'm now doing X" commentary. Output ONE brief summary when work completes. Nothing else.

## When to Use
- Long repetitive tasks where you don't need updates
- Tight token budgets
- When you're skipping Claude's explanations anyway
- Debugging where clean output matters

## Example

**Without STFU:**
```
Let me read the file first...
[reads file]
Now I'll look for the bug...
Found it on line 42. Here's what the issue is...
```

**With STFU:**
```
✓ Fixed bug on line 42.
```
