# 004 — Compound proof ("follow AND like") was rejected when split across images/messages

**Date:** 2026-06-23
**System:** proof automation (`Tasks/proof_automation_tasks.py`, model cascade)

## Symptom
A user submitted two screenshots to satisfy the Twitter "follow **AND** like"
reward — image 1 = following proof, image 2 = liking proof. The bot rejected
image 1 as "Following only" and never combined it with the liking proof, so the
user was stuck despite having done everything. Same failure when the two proofs
arrived in two separate messages.

## Root Cause
Each image was evaluated independently through the Automation Tree, and the
batch loop in `_process_images` early-returned on the first grant / replied only
about `decisions[0]`. The compound requirement ("following" + "liking") was
never reconstructed across images, and there was zero state between messages —
each `on_message` was fully isolated (temp files unlinked at end of call).

## Fix
1. **Phase 1 (single message):** after the per-image loop, map confident
   terminal rejects to components (`REJECT_FOLLOWING_ONLY`→following,
   `REJECT_LIKING_ONLY`→liking) and grant Level 1 if the set covers both.
2. **Phase 2 (cross-message):** new `partial_proofs` table in `roles.db`
   (PK `guild_id,user_id,component`) remembers a proven half for 12h
   (`PARTIAL_PROOF_TTL`). The complementary half arriving within the window
   combines → auto-grant + clear rows. Otherwise the half is UPSERTed and the
   user gets a "got X, send Y within 12h" progress reply instead of a flat
   rejection. Lazy expiry (`submitted_at > now-TTL`) guarantees correctness;
   the 24h cleanup loop just keeps the table tiny.
3. If any required image is uncertain (HITL), the whole batch still goes to the
   human queue — now with a "Bot already recognized: Following ✅" note so staff
   only verify the missing half.

## Lesson
When a reward needs N independent components, never judge each proof image in
isolation — reconstruct the component SET (within the message and, for split
submissions, in a short-lived per-user buffer). Each half being ≥99%-confident
makes auto-granting the combination exactly as safe as a single-image grant.
Keep cross-message state in the DB, not memory — this bot restarts often.
Creator-code proofs are explicitly excluded (no partial combining; first grant
wins). See `[[002-vit-transformers-version-coupling]]` for the ≥99% threshold
context.
