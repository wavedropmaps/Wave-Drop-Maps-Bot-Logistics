# Removed Logic (Old Automation Proof System)

This directory contains the original monolithic implementation of the Proof Automation bot prior to the "Cascading Multi-Model Clean Architecture" rebuild.

## What Was Ripped Out

### 1. The Single Model Architecture
The old system used a single YOLO model (`proof_best.pt`) to handle all proofs. It was loaded dynamically via a lazy-loader `_get_yolo()` and executed via `_run_yolo()`.
The single model had classes like `SCAM_CLASS` (0), `CREATOR_CODE_CLASS` (1), and various other flat class mappings that were tied to a priority system `class_priority` inside `GUILD_CONFIG`.

All of this was gutted from `Tasks/proof_automation_tasks.py` to make way for a modular, node-based tree of 8 specialized models.

### 2. Discord Embed Logging
The old system posted visual logs (embeds with side-by-side comparison images) to dedicated staff channels (`1512090290922586272`, etc.) via `_post_stolen_review()`, `_post_scam_alert()`, and `_post_heads_up()`.
These functions were removed entirely. The visual embed logging was replaced by a pure backend SQLite database table (`stolen_detections`), creating a permanent forensic record without cluttering Discord.

### 3. Old Database Flags
The old system relied heavily on a simple count-based table called `stolen_flags`. While this table was kept for backward compatibility (just storing user IDs and flag counts), the actual reporting logic was completely rebuilt.

## Files Archived
- `Tasks/proof_automation_tasks.py`: The massive 1400+ line monolith.
- `Commands/proof_automation_commands.py`: The commands cog tied to the old configuration.

*(See the `implementation_plan.md` in the agent's brain directory for the exact lines that were removed and the full diagram of the system that replaced it).*
