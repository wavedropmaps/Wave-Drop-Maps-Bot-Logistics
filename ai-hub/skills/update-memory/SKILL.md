---
name: update-memory
description: Use this skill WHENEVER the user tells you to "update your memory," "learn from this," "remember this mistake," or asks you to permanently save a lesson so future agents don't repeat the same error. This skill enforces the Supercomputer Context Protocol, ensuring rules are saved with full historical context.
---

# Supercomputer Memory Updater

When the user asks you to learn a lesson, do NOT just append a one-line bullet point to `lessons-learned.md`. You must follow the Context Protocol so future agents understand *why* the rule exists.

## The Workflow

### Step 1: Write the Context File
Create a new markdown file inside `ai-hub/memory/global-memory/context/`. Name it sequentially or descriptively (e.g., `002-database-locks.md`).

The file MUST contain these three sections:
- **The Symptom:** What went wrong? What did the user see?
- **The Root Cause:** Why did it happen? What was the technical or logical failure?
- **The Lesson Learned:** What is the new rule to prevent this?

### Step 2: Update the Index
Open `ai-hub/memory/global-memory/lessons-learned.md`.
Find the appropriate category (or create a new one). Add a one-sentence summary of the rule as a bullet point.
**CRITICAL:** You must hyperlink the bullet point to the context file you created in Step 1.

Example of a correct index entry:
`- **[Always Use Pathlib](context/003-pathlib.md):** Never use hardcoded backslashes for paths, as the bot runs on Windows but devs use Mac. Read the linked context for the full history.`

### Step 3: Confirm with the User
Let the user know that the Supercomputer Memory has been updated, and provide them with a clickable link to the new context file.
