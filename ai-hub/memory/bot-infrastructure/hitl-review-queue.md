# Human-In-The-Loop Review Queue
> Referenced from `AGENTS.md` → Codebase Map. The single-claim staff workflow for proofs the cascade couldn't decide with high enough confidence — staging, review UI, claim→resolve flow, and the channel sticky.

### When a proof lands in review
- Any terminal `HITL` decision (confidence under the model's threshold, an unmapped class, or an evaluation exception) routes the image to the staff review channel instead of auto-actioning.
- `_execute_hitl_batch` (`Tasks/proof_automation_tasks.py:1410`) stages up to 10 images per batch. Each is copied into the `hitl_pending/` directory with a unique name: `hitl_pending/hitl_{message.id}_{uuid}_{i}{ext}` (`:1420`–`:1422`), and attached as a `discord.File` to the review embed.
- A 24-hour `@tasks.loop(hours=24)` `cleanup_hitl_pending` (`:1177`) deletes any `hitl_pending/` file older than 86400s (`:1183`), so staging never accumulates.

### The review card (embed + view)
- The embed (`:1437`) is titled "⚠️ HITL Review Required", links back via `message.jump_url`, and shows fields **User** (mention + ID), **Failed Node**, **Confidence** (`{:.2%}`), and a footer `Batch of N`.
- The DB record is created via `register_hitl_review()` storing `start_node`, `valid_classes`, and the `hitl_filenames`, so a restart can rebuild the exact decision buttons.
- A fresh card carries only `HITLClaimView` (`:258`): a single primary "Claim" button (🙋, `custom_id=f"hitl_claim_{message_id}"`). The view is attached, then registered persistently via `self.bot.add_view(...)` (`:1453`–`:1455`).

### Single-claim workflow
- `_on_claim` (`:274`) calls `await claim_hitl(message_id, user.id)`; if it returns false (already claimed) it replies ephemerally "⚠️ This review was just claimed by someone else." (`:278`–`:282`) and does nothing else — this is the atomic guard against double-claims.
- On a successful claim the card swaps to `HITLActionView` (`:373`), built from the stored `valid_classes`: row 0 has one decision button per valid class (`custom_id=f"hitl_act_{message_id}_{i}"`), and row 1 has global override buttons — e.g. a danger "Garbage" button wired to `REJECT_DYNAMIC` (`:428`–`:433`).
- `HITLActionView.interaction_check` (`:465`) enforces ownership on every button press: if the claim is already `resolved` it disables all buttons and shows "✅ Review already completed." (`:469`–`:478`); if `claimed_by_id` is set and the presser isn't that user, it rejects with "❌ This review is claimed by another staff member." (`:480`–`:483`). Buttons are disabled once resolved (per recent commit `c44dee4`).
- The chosen action runs `_execute_decision` against the **original** message (assign roles + send the success/reject reply), exactly as an auto-decision would have.

### Single-image vs multi-image (2+) reviews — the model must NOT re-judge a batch
- **Why it matters:** every model only ever sees ONE image, so a proof made of 2+ images (e.g. a Twitter "follow" screenshot + a separate "like" screenshot) can never be decided by re-running a model on one of them.
- **Single image:** a class-pick still hands the corrected node back to the cascade — `make_callback` → `resume_processing(hitl_filenames[0], …)` forces the staff class at `start_node` and lets the next AI finish (the blue "send to next AI" buttons).
- **Multi-image (`len(hitl_filenames) > 1`):** the batch is staged with `start_node = "Model1_Gatekeeper"` (`_execute_hitl_batch`, the `if len(analyses) > 1` guard) so staff start from the top, and `make_callback` routes every confirm through `HITLActionView._manual_walk_step` instead — **no inference at all**:
  - A **routing** class (`node.route(cls, 1.0, cfg)` → `ROUTE`) advances the card to the next node's buttons: persists the step via `update_hitl_node`, rebuilds the `HITLActionView` for `next_node` (labels from `_classes_for_node`, which loads weights only to read `model.names` — never to classify), keeps **all** images, deletes nothing.
  - A **terminal** class (`GRANT_*` / `REJECT_*`) resolves the proof with parity to the global-override path: `resolve_hitl` + a single `review_completed` log, then `_execute_decision` with no interaction (so it isn't double-counted), then cleans up every staged file.
- **Restart/timeout safe:** because each routing step writes the new `start_node`/`valid_classes` via `update_hitl_node`, a 5-min claim-timeout release or a bot restart rebuilds the claim card at the *current* step of the walk.
- **Historical bug (fixed):** before this, a class-pick on a 2-image batch ran `resume_processing(hitl_filenames[0], …)` — the model re-decided off image #1 and the `for f in hitl_filenames[1:]: os.unlink(f)` cleanup **deleted image #2 unseen**.

### Claim timeout & restart recovery
- `@tasks.loop(seconds=30)` `_hitl_claim_timeout_check` (`:1138`) uses a 300-second cutoff (`:1143`): a claim older than 5 minutes is released and the card reverts to claim-only so another staffer can pick it up.
- `cog_load` (`:962`, runs on startup) clears all interrupted claims (`UPDATE hitl_claim_state SET claimed_by=NULL ... WHERE resolved=0`) and re-registers a `HITLClaimView` for every unresolved review so buttons keep working after a restart.

### Sticky message
- A single pinned "sticky" in the review channel summarizes the queue. `_update_sticky` (`:1094`) fetches pending reviews via `get_pending_hitl`, filters to unclaimed, and renders one line per item with a jump link, the original user's mention, and an age string.
- Age is derived from the Discord snowflake (`created_ms = (msg_id >> 22) + 1420070400000`, `:1108`); urgency markers escalate ` ⚠️` after 10 min and ` 🔴` after 30 min (`:1116`). Empty queue renders "✅ All reviews done — nothing pending."
- The sticky is refreshed by deleting the old message (`get_hitl_sticky` → `fetch_message` → `delete`) and posting a new one, then storing its ID via `set_hitl_sticky` (`:1127`–`:1136`). It is also refreshed whenever any message arrives in the review channel (`on_message`, `:999`–`:1001`).

### Staff queue commands (`Commands/review_queue_commands.py`)
- All are prefix `-z` commands (not slash), gated by an `@is_authorized()` Administrator/Management check.
- `-z reviewqueue` (aliases `reviewpending`, `pendingreviews`; `:120`) lists unresolved reviews with user, age, claim status, and jump link.
- `-z clearreview <message_id>` (alias `reviewclear`; `:155`) clears one stale review: marks it resolved, deletes the card, and logs an audit-only `review_cleared` event that deliberately does **not** count as a completed review.
- `-z clearreviewqueue confirm` (alias `reviewqueueclear`; `:201`) clears every pending review in the guild; the literal word `confirm` is required to avoid accidents, after which it refreshes the sticky.
