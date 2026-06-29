# Context: Proofs Vanishing from the Proof Channel (Cross-Bot Auto-Delete)

**Date:** 2026-06-20
**Topic:** Why submitted proof messages disappear from the proof channel

## The Symptom
Members' proof screenshots were vanishing from the proof channel (`#┃❗・proof・❗┃`) shortly after submission. Local logs showed repeated `[ReplyDM] Auto-deleted message` entries spanning weeks (May–June). The Wave Logistics Bot would then sometimes report "original proof not found," falling back to a `MockMessage` path.

## The Root Cause
- The proof channel is configured as the **Wave Management Bot's** `reply_dm_channel`, with auto-delete enabled on its 5-minute, reply-triggered path.
- The Management bot's `reply_dm_outbound` `is_staff` check intentionally includes `message.author.bot` — so it treats *any* bot's reply as a "staff reply."
- When the **Logistics bot** auto-replies to a proof (grant / reject / etc.), the Management bot interprets that as a staff reply, arms a 5-minute auto-delete timer, and performs a "clean sweep" that deletes the original proof (and can sweep other prior unreplied messages from that member in the channel).
- This is NOT the 12-hour expiry path and NOT a human action — it is one bot's automated reply triggering the other bot's delete mechanism. Log evidence showed the 5-minute "Auto-deleted" path as the dominant culprit.

## The Lesson Learned
- The two Wave bots share channels and load similar task files; assume their automated actions can trigger each other.
- Fix options (in the **Management** bot): (1) remove the proof channel from `reply_dm_channel_id` entirely, or (2) add a guard in `reply_dm_outbound.on_message` to skip auto-delete when the replier is the Logistics proof bot (surgical — preserves staff DM relay). **Option 2 is preferred.**
- When debugging "messages deleting themselves," check the OTHER bot's listeners before assuming the bug is local.
