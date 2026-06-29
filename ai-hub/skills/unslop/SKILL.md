---
name: unslop
description: >-
  Entry point for auditing work for AI-generated "slop" tells. A simple, menu-driven
  coordinator: it asks what you're auditing — a website/UI, prose/writing, source code,
  or all three — and routes you to the right specialist skill (unslop-ui, unslop-text,
  unslop-code). Use whenever the user wants something de-slopped, wants to know if
  something "looks/reads/sounds AI-generated," "looks vibe-coded," "is too generic," or
  asks to audit/review design, writing, or code for AI tells but hasn't said which one —
  or wants all of them. If the user is already clearly pointing at a single content type,
  you may invoke that specialist directly instead of this menu.
---

# unslop — AI-aesthetics auditor (router)

This is the **entry point** for de-slopping work. It does no auditing itself. Its only
job is to figure out **what** is being audited and hand off to the specialist that owns
that content type. The specialists hold all the real knowledge (the tell catalogs, the
scan scripts, the "fit the project instead of the default" guidance).

The three specialists live alongside this skill:

| Specialist | Audits | Lives at |
|---|---|---|
| **unslop-ui** | Websites, landing pages, web-app UI, dashboards, front-end components | `ai-hub/skills/unslop-ui/SKILL.md` |
| **unslop-text** | Prose for a reader — posts, emails, essays, articles, READMEs, marketing copy | `ai-hub/skills/unslop-text/SKILL.md` |
| **unslop-code** | Source code — any language, when it should fit the project rather than the model's default | `ai-hub/skills/unslop-code/SKILL.md` |

## How to run this skill

### 1. If the content type is already obvious, skip the menu
If the user's request already points clearly at one type, go straight to that
specialist — don't make them answer a menu they've effectively already answered.

- They name or paste a **website / component / CSS / Tailwind / shadcn / a screenshot of a page** → **unslop-ui**
- They name or paste **prose** (an email, README copy, post, article, announcement) → **unslop-text**
- They name or paste **source code** (a function, file, module, diff) → **unslop-code**

### 2. Otherwise, present the menu
Ask one question and wait for the answer:

> **What are we auditing for AI tells?**
> 1. **UI** — a website / page / front-end component
> 2. **Text** — writing meant for a reader
> 3. **Code** — source code
> 4. **All three** — audit a project across UI, text, and code

### 3. Route based on the choice

| Choice | Action |
|---|---|
| **1 / UI** | Read and follow `ai-hub/skills/unslop-ui/SKILL.md` |
| **2 / Text** | Read and follow `ai-hub/skills/unslop-text/SKILL.md` |
| **3 / Code** | Read and follow `ai-hub/skills/unslop-code/SKILL.md` |
| **4 / All three** | Run each specialist in turn (suggested order: **code → text → ui**), then give one combined summary of what was flagged and changed across all three |

When routing, actually open the specialist's `SKILL.md` and follow its instructions —
including its `references/` (the tell catalogs) and `scripts/` (the scanners). This
wrapper only chooses the door; the specialist does the work.

## Quick reference — which skill for which content

| If the user gives you… | Use |
|---|---|
| A live site, page, route, or screenshot of a UI | **unslop-ui** |
| HTML / CSS / Tailwind / shadcn / a styled component's look | **unslop-ui** |
| An email, post, essay, article, announcement, marketing copy | **unslop-text** |
| A README or docs prose (the writing, not the code blocks) | **unslop-text** |
| A function, file, module, PR diff, or "this code" | **unslop-code** |
| Naming, comments, error handling, structure of code | **unslop-code** |
| A whole project / repo "make it not look AI-made" | **All three** |
| They just say "de-slop this" with no target | **Ask the menu** |

## Notes
- Each specialist is self-contained and can also be invoked on its own; this wrapper
  exists for the common case where the user hasn't yet said which kind of slop they mean.
- None of these skills impose a style or hand you taste — they remove the tells that mark
  work as model-default and push toward a deliberate, project-specific choice.
