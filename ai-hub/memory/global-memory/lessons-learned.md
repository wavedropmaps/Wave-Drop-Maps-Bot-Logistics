# Global Memory: Lessons Learned & Rules

This file is part of the agent's **Procedural Memory**. It holds project-specific rules and lessons learned from past mistakes.

> **Agent Instruction:** Before starting complex tasks, check this file so you don't repeat past mistakes. When you make a mistake or solve a tricky problem, append the lesson here — and a linked post-mortem in `context/` per the Context Protocol (see `SUPERCOMPUTER.md`).

## General Rules
- Always use `pathlib.Path` for file operations — the bot RUNS on a Windows PC but is DEVELOPED on Mac. Hardcoded `/` or `\` paths break on the other platform. (Note: `config.json` already contains a Windows-only path, `C:/Users/.../dm_shared_queue.db`.)

## Cross-Bot Coordination (Wave Management Bot)
- This bot shares a proof/review channel and a cross-bot DM queue with the SEPARATE **Wave Management Bot**. Their automated actions can collide.
- **[Proofs vanish from the proof channel](context/001-cross-bot-proof-deletion.md):** the Management bot's `reply_dm_outbound` treats ANY bot reply as a staff reply and arms a 5-minute auto-delete that purges the original proof; this bot's auto-reply to a proof triggers it. Read the linked context for the full history.

## ML / Model Gotchas
- **[ViT version coupling silently randomizes weights](context/002-vit-transformers-version-coupling.md):** the ViT model (`Models/model 3.safetensors`, Model3b) is tightly coupled to the `transformers` library version. Loading it under a mismatched version can silently re-initialize weights to random — the model "loads" but predicts garbage, a false-grant risk on the auto-decision path. Pin the version. Read the linked context.

## Proof Detection
- **[Stolen proofs must not become reference data](context/003-stolen-proof-loophole.md):** a loophole stored stolen/duplicated proofs as legitimate references, polluting future detection queries. Validate provenance BEFORE storing any proof as a known-good reference; an exact-match (SHA-256 / attachment-ID) layer now guards this. Read the linked context.
- Auto-decisions require a **99% confidence threshold** on the YOLO nodes (the ViT node uses 0.70) — below it, route to the HITL review queue rather than auto-granting.

## Discord.py Gotchas
- HITL embed updates have hit `IndexError` when a message's embeds list is empty/missing — guard before subscripting `message.embeds[0]`.
- The HITL "sticky" status message has had race conditions on concurrent updates and was being deleted+resent instead of edited in place — guard concurrent updates.
- The shared DM queue drops `file`/`files`/`view`/`delete_after` kwargs across the shared-DB round-trip — only `content`/`embed`/`embeds` survive. Don't route attachments through `member.send` and expect them to arrive.

## Proof Automation
- A compound reward (e.g. Twitter "follow AND like") must reconstruct the component SET, never judge each proof image alone — combine confident halves within a message AND across messages via a short-lived per-user buffer (`partial_proofs` in roles.db, 12h TTL). Cross-message state goes in the DB, not memory (this bot restarts often). Creator-code proofs are excluded (first-grant-wins, no combining). See [context/004](context/004-compound-proof-batch-validation.md).
