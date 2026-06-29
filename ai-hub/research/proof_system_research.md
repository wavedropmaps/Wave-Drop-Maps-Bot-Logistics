# Proof System Research Notes

This document provides a detailed breakdown of how the **Proof Automation**, **Proof Archival**, and **DM Reply** systems operate across the two bots running on the system:
1. **Wave Management Bot** (`>`)
2. **Wave Logistics Bot** (`-z`)

---

## 👁️ System Overview

The proof processing workflow is a coordinated interaction between a public server's proof channel, staff reviews, automated YOLO image classification, duplicate/stolen prevention checks, local image archival, and DM feedback.

```mermaid
graph TD
    User[Member] -->|Uploads Screenshot| Ch[Proof Channel]
    
    subgraph Wave Logistics Bot (-z)
        Ch -->|on_message| PA[Proof Automation Task]
        PA -->|1. Stolen Check| DB1[(roles.db)]
        PA -->|2. YOLO Classify| YOLO[YOLO: proof_best.pt]
        YOLO -->|Class 4: Correct| Role[Grant Access Role]
        YOLO -->|Class 0, 3, 5| Reply[Auto-Reply Warning/Message]
        
        Ch -->|Role Giver Replies| PR[Proof Archival Task]
        PR -->|Verify Magic Bytes| Save[Save to proof_assets/]
    end
    
    subgraph Wave Management Bot (>)
        Ch -->|Staff Replies| DMOut[Reply DM Outbound]
        DMOut -->|Arm Note| DB2[(bot_database.db)]
        DMOut -->|Queue DM| DMQueue[Shared DM Queue]
        DMOut -->|Auto-Delete| Purge[5-Min Clean & 12-Hr Expiry]
        
        User -->|DMs Bot| DMIn[Reply DM Inbound]
        DMIn -->|Check Armed Note| DB2
        DMIn -->|Armed| Redirect[DM redirect back to Proof Channel]
    end
```

---

## 1. Wave Logistics Bot (`-z`) — Proof Automation & Archival

The Logistics Bot owns the local machine learning models and file storage for proof.

### A. Proof Automation (`Tasks/proof_automation_tasks.py`)
This is the YOLO-based classification and stolen-prevention engine.

*   **Watch Channels**:
    *   **Server 1 (Wave Drop Maps - `988564962802810961`)**: Watches channel ID `1210798761329295440`.
    *   **Server 2 (Loot Routes - `971731167621574666`)**: Watches channel ID `1188088624345002035`.
*   **Pipeline Flow**:
    1.  **Stolen/Duplicate Check (Cross-Guild)**:
        *   **Exact SHA-256**: Generates a file hash. If already uploaded by another user $\rightarrow$ flags stolen, auto-responds to user with a warning, logs to `#copy-proof-logs` (`1512346144448188486`), and stops processing.
        *   **Attachment ID**: Detects direct Discord CDN link reuse. Flags stolen and stops.
        *   **Perceptual Hash (pHash)**: Computes a 256-bit hash (including a mirrored hash check to close left-right flip evasion). If distance $\le 20$, it logs a "look-alike" review embed side-by-side with the original in `#staff-review` (`1512090290922586272`) but does *not* warn the user.
        *   **OCR User Check**: EasyOCR-based check (currently disabled to save CPU/RAM).
    2.  **YOLO Classification (`weights/proof_best.pt`)**:
        If unique, the image is passed to the YOLO classifier.
        *   **Class 0 ("Following Only")**: [Server 1 only] User only followed but did not like pinned tweet. Replies with twitter guidelines.
        *   **Class 3 ("Need to press search")**: [Both servers] Code typed but not submitted. Replies to press Search.
        *   **Class 5 ("Zoom Out")**: [Both servers] Crop is too tight. Replies asking to zoom out.
        *   **Class 4 ("Using creator code correctly")**: [Both servers] **Access-granting class.** Grants roles and rotates through 5 distinct "unlocked" message templates (maintained via `creator_code_index` in SQLite).
        *   **Class 6/7 ("Other / Scam")**: Class 7 triggers a staff scam alert.
    3.  **Low-Confidence (Heads-Up)**:
        If a prediction falls in `[0.40, class_threshold)`, the bot forwards the image to `#staff-review` for manual oversight.

### B. Proof Archival (`Tasks/proof.py`)
A background archival tool that triggers on manual staff approval.

*   **Trigger**: When a member with the **Role Giver** role replies to any message in the watch channel.
*   **Behavior**:
    *   Downloads all attachments on the replied-to message.
    *   Saves them locally to `proof_assets/<guild_id>/<YYYY-MM-DD>/<author_id>/<msg_id>_<idx>_<filename>`.
    *   **Security Whitelist**: Pre-download size cap (25MB) and extension match. Post-download magic-byte check (`_IMAGE_SIGNATURES` verified for PNG, JPEG, GIF, BMP, TIFF, WEBP) to ensure renamed executables/malware are deleted instantly.

### C. Logistics Bot Database Tables (`Database/roles.db`)
*   **`proof_submissions`**: Tracks all processed proofs.
    *   `id`, `guild_id`, `user_id`, `phash`, `twitter_username`, `message_id`, `submitted_at`, `sha256`, `attachment_id`, `filename`.
*   **`proof_automation_state`**: Tracks toggle state and success-message index.
    *   `guild_id`, `creator_code_index`, `enabled`.
*   **`stolen_flags`**: Tracks user strike histories for stolen uploads.
    *   `id`, `guild_id`, `user_id`, `kind`, `match_type`, `message_id`, `flagged_at`.
*   **`proof_config`**: Configuration for manual archival.
    *   `guild_id`, `channel_id`, `enabled`, `total_saved`, `last_saved_at`.
*   **`proof_saved_messages`**: Tracks archived messages to avoid duplicate saves.
    *   `guild_id`, `message_id`, `saved_at`, `file_count`.

---

## 2. Wave Management Bot (`>`) — DM Reply & Cleanup System

The Management Bot handles communication feedback loops, logs, and channel purges.

### A. Outbound Reply Cog (`tasks/reply_dm_outbound.py`)
*   **Trigger**: Watches the watch channel (`reply_dm_channel_id` in `config.json`). When a staff member (listed in `reply_dm_staff_role_ids`) or an automated reviewer bot replies to a user's message:
    *   Queues a DM to the user containing the staff reply content via the shared `dm_queue` database (`_source="reply_dm_duty"`).
    *   Arms a **sticky note** (`reply_dm_note`) mapping the user ID to the guild/bot.
*   **Auto-Delete & Expiries**:
    *   **5-Minute Purge**: Deletes the member's message and the staff's reply 5 minutes after DM confirmation, along with all prior messages from that member in the channel.
    *   **12-Hour Expiry**: Automatically deletes unreplied member submissions older than 12 hours.
    *   **10-Minute Ping Purge**: Automatically deletes messages consisting of just a staff role mention.

### B. Inbound DM Cog (`tasks/reply_dm_inbound.py`)
*   **Trigger**: When a user DMs the bot.
*   **Behavior**:
    *   Checks if the user has an armed note in the database (`reply_dm_note`).
    *   If the note was armed by **this bot instance** (to prevent double replies from both bots), the bot DMs the user a redirect warning:
        > *"Hi! If you're sending proof, please post it in #proof-channel on the server instead..."*
    *   Disarms the note upon sending. If no note is armed, it stays silent.

### C. Management Bot Database Tables (`bot_database.db`)
*   **`reply_dm_note`**: Tracks armed sticky notes.
    *   `user_id`, `guild_id`, `bot_id`, `armed_at`.
*   **`reply_dm_pending_deletes`**: Persists active 5-minute message deletion tasks.
    *   `message_id`, `channel_id`, `staff_reply_id`, `delete_at`, `member_id`.
*   **`reply_dm_pending_staff_mention_deletes`**: Persists active 10-minute ping deletion tasks.
    *   `message_id`, `channel_id`, `delete_at`.
