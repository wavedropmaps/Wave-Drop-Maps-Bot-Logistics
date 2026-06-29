# Proof Auto-Detection Pipeline
> Referenced from `AGENTS.md` → Codebase Map. End-to-end flow for how a submitted creator-code / Twitter proof image is intercepted, fingerprinted, run through the ML model cascade, and auto-granted, auto-rejected, or escalated to human review.

### Entry Point & Interception
- The pipeline is a Discord cog whose listener is `@commands.Cog.listener()` → `async def on_message` in `Tasks/proof_automation_tasks.py:990`.
- Guards (`:992`–`:1004`): ignores bots and DMs; looks up per-guild config via `GUILD_CONFIG.get(message.guild.id)`; if the message is in the HITL review channel it only refreshes the sticky and returns; it only processes messages whose `message.channel.id == cfg["watch_channel_id"]`.
- Automation can be toggled per guild (DB table `proof_automation_state.enabled`). When OFF, no replies/roles happen, but image fingerprints are still collected and stored.
- Per-guild behavior is hardcoded in `GUILD_CONFIG` (`:127`–`:255`): `watch_channel_id`, `hitl_review_channel_id`, role IDs, and the rejection/success message text.

### Fingerprinting (runs before classification)
- Every attachment is downloaded and fingerprinted: SHA-256 byte hash, perceptual hash (pHash), and EXIF metadata are extracted in `_process_images` (`:1329`+).
- `PHASH_HASH_SIZE = 16` (`:68`) → 256-bit perceptual hash, validated against 51 real proofs.
- `PHASH_DUPE_THRESHOLD = 20` (`:69`) → Hamming-distance tolerance for near-duplicate detection (re-encoded/resized copies land 0–2; genuinely different users ~46, leaving a large safety margin).
- `PHASH_CHECK_MIRROR = True` (`:99`) also computes a mirrored pHash to defeat horizontal-flip evasion.
- Fingerprints are persisted to `proof_submissions` (sha256, phash, attachment_id, filename, submitted_at) for cross-user theft comparison — always, even when automation is disabled (`:1380`).

### Stolen-Proof Detection
- Master switch `STOLEN_CHECKS_ENABLED = True` (`:73`); runs before the model cascade.
- Exact match (`EXACT_STOLEN_ENABLED`, `:77`) via `_find_exact_stolen()` (`:782`): first checks SHA-256 byte-identical match (`match_type="Exact file match (SHA-256)"`), then Discord attachment-ID reuse (`"Discord attachment reused"`). On hit (`:1338`–`:1353`): logs to `stolen_detections`, records a flag, replies `STOLEN_MSG`, and stops.
- Perceptual match (`PERCEPTUAL_STOLEN_ENABLED`, `:88`) via `_find_fuzzy_stolen()` (`:826`): compares incoming pHash (and mirror) against all *other* users' stored submissions; flags if best distance `<= PHASH_DUPE_THRESHOLD (20)`. Match types `"pHash image match"` / `"pHash image match (mirrored)"`.
- `STOLEN_MSG` (`:120`–`:124`, verbatim): "🚨 This proof has already been submitted by someone else. Submitting **stolen or copied proof is not allowed and can get you banned.** Please only submit your own original proof."
- `_record_flag_and_count()` (`:1233`) writes to `stolen_flags` and returns the user's prior exact/perceptual flag counts — these are **informational only** in the review embed and never drive an automated action.
- `OCR_USERNAME_ENABLED = False` (`:93`) — disabled to save RAM.

### The 8-Model Cascade (`Models/`)
- Models are loaded as nodes of an automation tree (see `automation-tree.md`); traversal starts at `Model1_Gatekeeper`. Each `Models/` file backs one node:
  - `model 1.pt` → **Model1_Gatekeeper** — top-level image-type classifier; routes to Twitter / Creator-Code or rejects garbage/invite spam.
  - `Model 2Desktop or mobile.pt` → **Model2_TwitterRouter** — desktop vs mobile Twitter screenshot.
  - `Model 3 following or either set cof .pt` → **Model3a_MobileCheck1** — "Following only" vs ambiguous "either".
  - `model 3.safetensors` → **Model3b_MobileCheck2** — a **ViT** (HuggingFace `vit-base-patch16-224-in21k`, safetensors) doing the deep Following+Liking check.
  - `Model 4 desktop twitter.pt` → **Model4_DesktopCheck** — desktop Twitter proof validation.
  - `Model 5 gatekeeper of proofs.pt` → **Model5_UIRouter** — creator-code UI type (Epic website / iPhone shop / phone photo / screenshot).
  - `phone photo code proof.pt` → **Model6_PhonePhoto** — validates a phone photo of the in-game shop showing the code.
  - `Screenshot code proof.pt` → **Model7_Screenshot** — validates a direct screenshot of the shop using the code.

### Confidence Thresholds → HITL
- All seven YOLO nodes use `if confidence < 0.99: → HITL` (`utils/model_nodes.py:30,48,60,72,88,102,116`). Anything under 99% certainty is escalated to humans rather than auto-actioned.
- The single ViT node (Model3b) uses a more lenient `if confidence < 0.70: → HITL` (`utils/model_nodes.py:188`), reflecting the different architecture's calibration.
- Any model output that doesn't map to a known class also returns `HITL` (the `else` branch in every node).

### Instant Grant / Instant Reject / Route-to-HITL Decision
- A terminal `GRANT_LEVEL_1` / `GRANT_LEVEL_2` triggers **instant approval** (`:1388`–`:1393`): the first image in a batch that grants access executes `_execute_decision` (assign role(s) + send a rotating success message) and **stops processing** the remaining images.
- `GRANT_LEVEL_1` assigns only the first role ID; `GRANT_LEVEL_2` assigns all configured creator-code role IDs (`_assign_creator_roles`, `:1207`). Success messages rotate through a pool via `_get_next_success_message` (`:756`, index stored in `proof_automation_state.creator_code_index`).
- Terminal `REJECT_*` actions auto-reply with a specific message: `REJECT_DYNAMIC` (localized garbage reply via `DynamicHandlers`), `REJECT_INVITE`, `REJECT_FOLLOWING_ONLY`, `REJECT_LIKING_ONLY`, `REJECT_PRESS_SEARCH`, `REJECT_ZOOM_OUT`, `REJECT_WRONG_CODE` (`:1513`+).
- If no image grants but some are uncertain, those `HITL` images are batched and routed to staff; reject-only batches just send the rejection (`:1395`–`:1404`).
- Uncertain images are staged to the `hitl_pending/` directory and posted to the review channel (see `hitl-review-queue.md`).
