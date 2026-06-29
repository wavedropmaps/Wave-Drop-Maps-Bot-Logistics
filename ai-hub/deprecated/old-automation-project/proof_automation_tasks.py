"""
Proof Automation — YOLO classifier + duplicate/stolen detection
===============================================================
Watches configured channels across multiple guilds.
For every image posted (all images in a message are checked):
  1. Stolen-proof detection (cross-guild, strongest signal first):
       a. SHA-256 exact file match     — byte-identical re-upload
       b. Discord attachment reuse     — same CDN attachment id
       c. pHash near-duplicate         — survives re-encode/resize; the
          horizontally-MIRRORED image is also checked (flip evasion)
       d. OCR username match           — one account farming proof
  2. YOLO classification     — replies with the appropriate message
  3. Stores submission       — pHash + sha256 + attachment_id + filename in DB

The fingerprint collector ALWAYS runs — even when per-guild automation is
toggled off (-z prooftoggle) or when a thief is warned and processing stops —
so every image posted in a watch channel stays matchable later.

Every stolen/copied flag is also recorded per user (stolen_flags table) and the
review embed shows how many times that user was flagged before. Count only —
no extra action is ever taken from the count.

Stolen matches are reported to the staff review channel with the match type,
original submitter/server (cross-server flagged), a time-window suspicion
rating, and any EXIF metadata.

Actionable YOLO classes (proof_best.pt):
    0  Following Only                     (Server 1 only)
    3  Need to press search on code proof (both servers — reply, no role)
    4  Using the creator code correctly   (both servers — cycles through 5 messages + role)
    5  Zoom Out                           (both servers)

Stolen proof  → posted to staff review channel (no auto-warn to user)
Low confidence / wrong class → heads-up post to staff channel with image

No commands — all config is hardcoded below.
"""

import asyncio
import io
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone

import aiosqlite
import discord
from discord.ext import commands

logger = logging.getLogger('discord')

MODEL_PATH             = os.path.join("weights", "proof_best.pt")
LOCAL_DB               = "Database/roles.db"
HEADS_UP_MIN_CONF      = 0.40   # ≥ this but < class threshold → heads-up log

# Perceptual-hash size + match threshold. We use a 256-bit pHash (hash_size=16).
# Validated on 51 real proofs (tests/eval_harness.py): re-encode/resize copies
# land at 0–2, the closest genuinely-different user at 46 → a 20 cutoff catches
# copies with a huge (~26-bit) safety margin and zero false positives on the set.
# (Crops are NOT caught — they exceed the different-user distance; accepted gap.)
PHASH_HASH_SIZE        = 16
PHASH_DUPE_THRESHOLD   = 20

# ── Stolen-proof detection switches ──────────────────────────────────────────
# Master switch. When False: no detection at all (submissions still stored).
STOLEN_CHECKS_ENABLED = True

# EXACT signals (SHA-256 + attachment id): a literal re-upload, zero false
# positives.
EXACT_STOLEN_ENABLED = True

# EXACT matches (SHA-256/attachment) are zero-false-positive, so they ENFORCE:
# warn the user (STOLEN_MSG) + stop processing. Perceptual (pHash) is separate
# (see below) and is ALWAYS log-only regardless of this flag.
STOLEN_WARN_USER = True

# PERCEPTUAL signal (256-bit pHash): catches re-encoded/resized copies. Validated
# safe on real data, but to be cautious it is STAFF-HEADS-UP ONLY — it posts a
# review for staff and NEVER warns the user or blocks them. Catches re-encode/
# resize theft; does NOT catch crops.
PERCEPTUAL_STOLEN_ENABLED = True

# OCR username signal: DISABLED. Useless on the creator-code screen (the code is
# the same for everyone, no per-user @handle) and EasyOCR is the big RAM hog.
# Off ⇒ EasyOCR never loads. (Worth re-enabling only for Server 1 Twitter proofs.)
OCR_USERNAME_ENABLED = False

# Also compare the horizontally-MIRRORED incoming image against stored hashes —
# closes the "flip the stolen image" evasion. Costs one extra pHash (~ms) per
# image at query time only; nothing extra is stored. Log-only like all
# perceptual signals.
PHASH_CHECK_MIRROR = True

# Render a single labeled side-by-side image (OLD original left, NEW submission
# right) inside the review embed, so staff compare at a glance instead of
# scrolling two stacked attachments. Full-size files stay attached either way.
COMPARISON_IMAGE_ENABLED = True

# Filenames too generic to mean anything when two uploads share them (Discord
# paste defaults etc.). A filename match is only ever shown as a SUPPORTING
# line inside an embed that something else already triggered — never a signal
# on its own, and never for these names.
GENERIC_FILENAMES = {
    "image.png", "image.jpg", "image.jpeg", "image.webp",
    "image0.png", "image1.png", "image2.png", "image3.png",
    "image0.jpg", "image1.jpg", "image2.jpg", "image3.jpg",
    "unknown.png", "unknown.jpg", "untitled.png", "untitled.jpg",
    "screenshot.png", "screenshot.jpg", "img.png", "img.jpg",
}

# YOLO low-confidence heads-up channel.
STAFF_REVIEW_CHANNEL_ID = 1512090290922586272
# EXACT (CONFIRMED) copy/stolen-proof logs — the live detection channel.
COPY_PROOF_CHANNEL_ID = 1512346144448188486
# PERCEPTUAL (pHash) matches go here — separate channel for TESTING/observation
# while we gather more data before trusting perceptual matches.
PERCEPTUAL_LOG_CHANNEL_ID = 1512090290922586272

# Reply sent to a user when their submission is flagged as possible stolen proof.
STOLEN_MSG = (
    "🚨 This proof has already been submitted by someone else. Submitting "
    "**stolen or copied proof is not allowed and can get you banned.** "
    "Please only submit your own original proof."
)

CLASS_NAMES = {
    0: "Following Only",
    1: "Liking Only",
    2: "Liking and Following",
    3: "Need to press search on code proof",
    4: "Using the creator code correctly",
    5: "Zoom Out",
    # Classes 6 + 7 only exist in the NEXT model (retrained with negative
    # classes so scams/memes/random images stop being forced into a proof
    # class). Safe to list now — all probs indexing is bounds-checked, so the
    # current 6-class weights keep working and the 8-class weights are drop-in.
    6: "Other / Not a proof",
    7: "Scam",
}

# Per-class confidence required to ACT (from the precision analysis).
# A value of None disables the class entirely — never detected/acted on
# in any server (no reply, no role, no heads-up).
CLASS_CONF_THRESHOLD = {
    0: 0.90,   # Following Only
    1: None,   # Liking Only            — never (disabled)
    2: None,   # Liking and Following   — never (disabled)
    3: 0.90,   # Need to press search on code proof
    4: 0.99,   # Using the creator code correctly
    5: 0.99,   # Zoom Out
    6: None,   # Other / Not a proof    — never act (absorbs random non-proof images)
    7: None,   # Scam                   — handled by the dedicated scam path below,
               #                          not the generic reply/grant flow
}

# ── Scam class (7) handling — phased in like everything else ─────────────────
# Only fires on models that actually have class 7 (bounds-checked). Alerts go
# to the testing channel; deletion ships OFF until the class is proven.
SCAM_CLASS            = 7
SCAM_CONF_THRESHOLD   = 0.90
SCAM_ALERT_ENABLED    = True    # post a staff alert when a scam is detected
SCAM_DELETE_ENABLED   = False   # observe-only for now; flip to auto-delete later
SCAM_ALERT_CHANNEL_ID = STAFF_REVIEW_CHANNEL_ID

# The access-granting class. When an image shows this at/above its threshold,
# it overrides any other top-1 prediction (granting access takes priority).
CREATOR_CODE_CLASS = 4

# Classes suppressed when a single message contains MORE THAN ONE image.
# Following Only (0) is a "you still need to do X" nag — but a multi-image
# message is almost always someone submitting several proofs at once, so
# nagging "you only followed" is usually wrong. Skip it in that case.
MULTI_IMAGE_SUPPRESSED_CLASSES = {0}

# Training-data logging: for every class with a numeric threshold, log a
# "near-miss" line whenever that class's probability lands in
# [TRAINING_LOG_MIN_CONF, its action threshold) — i.e. the bot saw it but the
# confidence was too low to act. Grep `[ProofAuto][TRAIN]` to collect cases the
# model is *almost* getting right, for retraining. Logging only — no behaviour.
TRAINING_LOG_MIN_CONF = 0.30

# ── Per-server config ─────────────────────────────────────────────────────────
GUILD_CONFIG = {

    # ── Server 1 ──────────────────────────────────────────────────────────────
    988564962802810961: {
        "name": "Server 1 (Wave Drop Maps)",
        "watch_channel_id": 1210798761329295440,
        "active_classes": (0, 3, 4, 5),
        # When several classes are actionable, pick by this hierarchy instead of
        # highest confidence: creator code (4) > press search (3) > zoom out (5)
        # > following only (0). Class 0 sits at the bottom, so any other class
        # wins over it when both fire. (4 is normally handled by the grant
        # override; this mainly decides 3 vs 5 vs 0.)
        "class_priority": (4, 3, 5, 0),
        "creator_code_role_ids": (1055713830988157039, 1305277560086593546),
        "following_only_msg": (
            "Please show **__proof__** of __**liking**__ the** pinned tweet **"
            "https://x.com/Wavedropmaps/status/1896931137722982898 and following "
            "https://x.com/Wavedropmaps, send proof in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "press_search_msg": (
            "You need to **press __search__** on the code "
            "`WAVEDROPMAPS` in the **support a creator panel** so it shows as "
            "applied, then send **proof** in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "zoom_out_msg": (
            "Please show **proof** of using **code** `WAVEDROPMAPS` in the "
            "**support a creator panel** while the image is **__ZOOMED OUT__**  "
            "and send **proof** in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "creator_code_messages": [
            (
                "🔓 Full access unlocked\n"
                "**- Request custom drop maps for ANY spot with first-priority, "
                "skipping the queue → <#1364454494665969664> | <#1210810131634454638> **"
            ),
            (
                "Full access ✅\n"
                "**- Custom drop maps 👀 skip the drop map queue + first priority\n"
                "- <#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "You're in 🔥\n"
                "- **Get your spot mapped first, skip the wait → "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "🔓 Full access unlocked — **request custom drop maps for any spot "
                "with first-priority, skipping the drop map queue in "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "🔓 Full access\n"
                "- **Custom drop maps first-priority → "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
        ],
    },

    # ── Server 2 ──────────────────────────────────────────────────────────────
    971731167621574666: {
        "name": "Server 2 (Loot Routes)",
        "watch_channel_id": 1188088624345002035,
        "active_classes": (3, 4, 5),
        # When several classes are actionable, pick by this hierarchy instead of
        # highest confidence: creator code (4) > press search (3) > zoom out (5).
        # (4 is normally handled by the grant override; this mainly decides 3 vs 5.)
        "class_priority": (4, 3, 5),
        "creator_code_role_ids": (1105873031450075227,),
        "following_only_msg": None,
        "press_search_msg": (
            "You need to **press __search__** on the code "
            "`WAVEDROPMAPS` in the **support a creator pane**l so it shows as "
            "applied, then send **proof** in "
            "https://discord.com/channels/971731167621574666/1188088624345002035"
        ),
        "zoom_out_msg": (
            "Please show **proof** of using **code** `WAVEDROPMAPS` in the "
            "**support a creator pane**l while the image is **__ZOOMED OUT__**  "
            "and send **proof** in "
            "https://discord.com/channels/971731167621574666/1188088624345002035"
        ),
        "creator_code_messages": [
            (
                "🔓 Full access unlocked\n"
                "- **Get Pro Loot Routes + skip the loot route queue → "
                "<#1364463385709903893> | ⁠<#1131551886563082314>**"
            ),
            (
                "Full access ✅\n"
                "- **Pro Loot Routes 👀 skip the queue**\n"
                "- ⁠<#1364463385709903893> | ⁠<#1131551886563082314>"
            ),
            (
                "You're in 🔥\n"
                "**Pro Loot Routes + skip the queue → "
                "⁠<#1364463385709903893> | ⁠<#1131551886563082314>**"
            ),
            (
                "🔓 Full access\n"
                "**Pro Loot Routes + skip queue → "
                "<#1364463385709903893> | <#1131551886563082314>**"
            ),
            (
                "🔓 Full access unlocked —** grab Pro Loot Routes & skip the loot "
                "route queue in <#1364463385709903893> | <#1131551886563082314>**"
            ),
        ],
    },
}

# ── Lazy-loaded models ────────────────────────────────────────────────────────
_yolo_model = None
_ocr_reader = None


def _get_yolo():
    global _yolo_model
    if _yolo_model is None:
        import torch
        from ultralytics import YOLO
        _yolo_model = YOLO(MODEL_PATH)
        cuda = torch.cuda.is_available()
        try:
            imgsz = _yolo_model.model.args.get("imgsz", "?")
        except Exception:
            imgsz = "?"
        logger.info(
            f"[ProofAuto] YOLO model loaded ({MODEL_PATH}) — "
            f"device={'cuda' if cuda else 'cpu'}, cuda_available={cuda}, "
            f"torch_threads={torch.get_num_threads()}, train_imgsz={imgsz}"
        )
        if not cuda:
            logger.info("[ProofAuto] Running YOLO on CPU (no usable CUDA GPU detected)")
    return _yolo_model


def _get_ocr():
    global _ocr_reader
    if _ocr_reader is None:
        import torch
        import easyocr
        # Auto-detect: use the GPU only if PyTorch sees a CUDA device, else CPU.
        # NOTE: PyTorch GPU = CUDA (NVIDIA) only. On AMD/integrated GPUs this
        # stays False and EasyOCR runs on CPU.
        use_gpu = torch.cuda.is_available()
        _ocr_reader = easyocr.Reader(['en'], gpu=use_gpu, verbose=False)
        logger.info(
            f"[ProofAuto] EasyOCR reader loaded — "
            f"device={'cuda' if use_gpu else 'cpu'}, gpu={use_gpu}"
        )
    return _ocr_reader


# ── DB helpers ────────────────────────────────────────────────────────────────
async def _ensure_schema(db):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_submissions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         INTEGER NOT NULL,
            user_id          INTEGER NOT NULL,
            phash            TEXT    NOT NULL,
            twitter_username TEXT,
            message_id       INTEGER NOT NULL,
            submitted_at     REAL    NOT NULL,
            sha256           TEXT,
            attachment_id    INTEGER
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_automation_state (
            guild_id             INTEGER PRIMARY KEY,
            creator_code_index   INTEGER DEFAULT 0,
            enabled              INTEGER DEFAULT 1
        )
    """)
    # Per-user record of every stolen/copied flag. COUNT ONLY — shown in the
    # review embed so staff can spot repeat offenders; never drives any action.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS stolen_flags (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            kind       TEXT    NOT NULL,   -- 'exact' (confirmed) | 'perceptual' (look-alike)
            match_type TEXT,
            message_id INTEGER,
            flagged_at REAL    NOT NULL
        )
    """)
    # Migrate: add columns if upgrading from an older schema
    for table, col, ddl in (
        ("proof_automation_state", "enabled",       "INTEGER DEFAULT 1"),
        ("proof_submissions",      "sha256",        "TEXT"),
        ("proof_submissions",      "attachment_id", "INTEGER"),
        ("proof_submissions",      "filename",      "TEXT"),
    ):
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except Exception:
            pass  # Column already exists
    # Indexes for fast exact-match lookups
    await db.execute("CREATE INDEX IF NOT EXISTS idx_proof_sha256 ON proof_submissions(sha256)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_proof_attach ON proof_submissions(attachment_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_stolen_flags_user ON stolen_flags(user_id)")


async def _is_enabled(guild_id: int) -> bool:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT enabled FROM proof_automation_state WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return bool(row[0]) if row else True  # default enabled if no row yet


async def _get_next_creator_msg(guild_id: int) -> str:
    messages = GUILD_CONFIG[guild_id]["creator_code_messages"]
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            "INSERT OR IGNORE INTO proof_automation_state (guild_id, creator_code_index) VALUES (?, 0)",
            (guild_id,)
        )
        await db.commit()
        async with db.execute(
            "SELECT creator_code_index FROM proof_automation_state WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        idx = row[0] if row else 0
        next_idx = (idx + 1) % len(messages)
        await db.execute(
            "UPDATE proof_automation_state SET creator_code_index=? WHERE guild_id=?",
            (next_idx, guild_id)
        )
        await db.commit()
    return messages[idx]


async def _find_exact_stolen(analysis: dict, user_id: int) -> dict | None:
    """Instant exact-match stolen checks (SHA-256 + Discord attachment reuse).

    These are indexed lookups with zero false positives, so they run BEFORE the
    expensive YOLO/OCR pass — an exact hit lets us skip all model inference.
    Cross-guild on purpose. Returns a match dict or None.
    """
    sha    = analysis.get("sha256")
    att_id = analysis.get("attachment_id")
    if not sha and not att_id:
        return None

    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)

        # 1. Exact file (SHA-256) — byte-for-byte identical, zero false positives
        if sha:
            async with db.execute(
                """SELECT user_id, guild_id, submitted_at, message_id, filename FROM proof_submissions
                   WHERE sha256=? AND user_id!=? ORDER BY submitted_at LIMIT 1""",
                (sha, user_id)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return {
                    "match_type": "Exact file match (SHA-256)",
                    "detail": "Byte-for-byte identical file",
                    "original_user_id": row[0], "original_guild_id": row[1],
                    "original_submitted_at": row[2], "original_message_id": row[3],
                    "original_filename": row[4],
                }

        # 2. Discord attachment reuse — same CDN attachment linked again
        if att_id:
            async with db.execute(
                """SELECT user_id, guild_id, submitted_at, message_id, filename FROM proof_submissions
                   WHERE attachment_id=? AND user_id!=? ORDER BY submitted_at LIMIT 1""",
                (att_id, user_id)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return {
                    "match_type": "Discord attachment reused",
                    "detail": f"Same Discord CDN attachment id `{att_id}`",
                    "original_user_id": row[0], "original_guild_id": row[1],
                    "original_submitted_at": row[2], "original_message_id": row[3],
                    "original_filename": row[4],
                }

    return None


async def _find_fuzzy_stolen(analysis: dict, user_id: int) -> dict | None:
    """Fuzzy stolen checks that need the expensive signals (pHash + OCR
    username). Run only after the exact checks miss. Cross-guild.
    """
    phash_str = analysis.get("phash")
    username  = analysis.get("username")
    if not phash_str and not username:
        return None

    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)

        # 3. pHash near-duplicate (cross-guild) — survives re-encode / resize.
        # The MIRRORED incoming hash is checked too: flipping the stolen image
        # left↔right is a trivial evasion that moves the straight distance to
        # ~130/256, but mirror(flipped copy) ≈ original, so this closes it.
        if phash_str:
            import imagehash
            incoming = [(imagehash.hex_to_hash(phash_str), False)]
            if PHASH_CHECK_MIRROR and analysis.get("phash_flip"):
                try:
                    incoming.append((imagehash.hex_to_hash(analysis["phash_flip"]), True))
                except Exception:
                    pass
            async with db.execute(
                """SELECT phash, user_id, guild_id, submitted_at, message_id, filename
                   FROM proof_submissions WHERE user_id!=? AND phash!=''""",
                (user_id,)
            ) as cur:
                rows = await cur.fetchall()
            best_dist, best, best_flip = 999, None, False
            for ph, uid, gid, ts, mid, fname in rows:
                try:
                    stored = imagehash.hex_to_hash(ph)
                except Exception:
                    continue
                for inc, flipped in incoming:
                    try:
                        d = inc - stored
                    except Exception:
                        continue
                    if d < best_dist:
                        best_dist, best, best_flip = d, (uid, gid, ts, mid, fname), flipped
            if best and best_dist <= PHASH_DUPE_THRESHOLD:
                detail = f"Hamming distance {best_dist} (threshold {PHASH_DUPE_THRESHOLD})"
                if best_flip:
                    detail += " — matched the MIRRORED image (flip evasion)"
                return {
                    "match_type": "pHash image match (mirrored)" if best_flip else "pHash image match",
                    "detail": detail,
                    "original_user_id": best[0], "original_guild_id": best[1],
                    "original_submitted_at": best[2], "original_message_id": best[3],
                    "original_filename": best[4],
                }

        # 4. Same account/username (cross-guild) — one account farming proof
        if username:
            async with db.execute(
                """SELECT user_id, guild_id, submitted_at, message_id FROM proof_submissions
                   WHERE twitter_username=? AND user_id!=? ORDER BY submitted_at LIMIT 1""",
                (username.lower(), user_id)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return {
                    "match_type": "Same account (OCR username)",
                    "detail": f"Detected username: @{username}",
                    "original_user_id": row[0], "original_guild_id": row[1],
                    "original_submitted_at": row[2], "original_message_id": row[3],
                }

    return None


async def _store_submission(guild_id: int, user_id: int, phash_str: str | None,
                            twitter_username: str | None, message_id: int,
                            sha256: str | None = None,
                            attachment_id: int | None = None,
                            filename: str | None = None):
    # phash may be missing (PIL-unopenable format, compute error) — store the
    # row anyway so the SHA-256/attachment exact layer still protects the image.
    # Empty string keeps the NOT NULL schema; fuzzy lookups filter phash!=''.
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            """INSERT INTO proof_submissions
               (guild_id, user_id, phash, twitter_username, message_id,
                submitted_at, sha256, attachment_id, filename)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, phash_str or "", twitter_username, message_id,
             time.time(), sha256, attachment_id, filename)
        )
        await db.commit()


async def _record_flag_and_count(guild_id: int, user_id: int, kind: str,
                                 match_type: str | None, message_id: int) -> tuple[int, int]:
    """Record one stolen/copied flag and return the user's PRIOR flag counts
    (exact, perceptual) across all guilds. Count only — never drives action."""
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT kind, COUNT(*) FROM stolen_flags WHERE user_id=? GROUP BY kind",
            (user_id,)
        ) as cur:
            counts = dict(await cur.fetchall())
        await db.execute(
            """INSERT INTO stolen_flags (guild_id, user_id, kind, match_type, message_id, flagged_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, kind, match_type, message_id, time.time())
        )
        await db.commit()
    return counts.get("exact", 0), counts.get("perceptual", 0)


# ── Inference helpers (run in thread pool) ───────────────────────────────────
def _compute_phash(path: str) -> str:
    import imagehash
    from PIL import Image
    # 256-bit pHash (hash_size=16) — far better copy/different-user separation
    # than the 64-bit default on standardized proof screens.
    return str(imagehash.phash(Image.open(path), hash_size=PHASH_HASH_SIZE))


def _compute_phash_flip(path: str) -> str:
    """pHash of the horizontally-mirrored image — compared at query time only
    (never stored) to catch flipped copies."""
    import imagehash
    from PIL import Image, ImageOps
    return str(imagehash.phash(ImageOps.mirror(Image.open(path).convert("RGB")),
                               hash_size=PHASH_HASH_SIZE))


def _build_comparison_jpg(old_bytes: bytes, new_path: str) -> bytes:
    """Compose OLD (left) and NEW (right) into one labeled side-by-side JPEG
    for the review embed. Runs in a thread executor."""
    import io as _io
    from PIL import Image, ImageDraw, ImageFont

    old = Image.open(_io.BytesIO(old_bytes)).convert("RGB")
    new = Image.open(new_path).convert("RGB")
    H = 512

    def scaled(im):
        w = max(1, round(im.width * H / im.height))
        return im.resize((w, H))

    old_s, new_s = scaled(old), scaled(new)
    gap, label_h = 14, 38
    canvas = Image.new("RGB", (old_s.width + gap + new_s.width, H + label_h), (30, 31, 34))
    canvas.paste(old_s, (0, label_h))
    canvas.paste(new_s, (old_s.width + gap, label_h))

    d = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.load_default(size=22)
    except TypeError:  # older Pillow: no size arg
        font = ImageFont.load_default()
    # Plain ASCII labels — Pillow's default font has no em-dash glyph.
    d.text((6, 8), "OLD - original proof", fill=(90, 210, 130), font=font)
    d.text((old_s.width + gap + 6, 8), "NEW - just submitted", fill=(245, 90, 90), font=font)

    buf = _io.BytesIO()
    canvas.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _compute_sha256(path: str) -> str:
    """Exact file fingerprint — catches re-uploads of the identical file."""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


_EXIF_FIELDS = ("DateTimeOriginal", "DateTime", "Make", "Model", "Software")


def _extract_exif(path: str) -> str | None:
    """Pull a few useful EXIF fields if present (screenshots usually strip these)."""
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        exif = getattr(Image.open(path), "_getexif", lambda: None)()
        if not exif:
            return None
        found = {TAGS.get(t, t): str(v) for t, v in exif.items()
                 if TAGS.get(t, t) in _EXIF_FIELDS}
        return ", ".join(f"{k}: {v}" for k, v in found.items()) or None
    except Exception:
        return None


def _humanize_seconds(secs: float) -> str:
    secs = int(secs)
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _run_yolo(path: str) -> list[float] | None:
    """Return the full per-class probability list, or None on failure.

    We keep the whole distribution (not just top-1) so the access-granting
    class can override the top prediction when its probability is high enough.
    """
    model = _get_yolo()
    results = model(path, verbose=False)
    if not results:
        return None
    probs = results[0].probs
    if probs is None:
        return None
    return [float(p) for p in probs.data.tolist()]


def _run_yolo_batch(paths: list[str]) -> list[list[float] | None]:
    """Classify several images in a SINGLE forward pass. Returns a list aligned
    to `paths`; each entry is a per-class probability list (or None on failure).
    """
    if not paths:
        return []
    model = _get_yolo()
    results = model(paths, verbose=False)
    out: list[list[float] | None] = []
    for r in results:
        probs = getattr(r, "probs", None)
        out.append([float(p) for p in probs.data.tolist()] if probs is not None else None)
    # Guard against any length mismatch (shouldn't happen, but stay aligned)
    while len(out) < len(paths):
        out.append(None)
    return out[:len(paths)]


_TWITTER_USERNAME_RE = re.compile(r'@([A-Za-z0-9_]{1,50})')


def _extract_twitter_username(path: str) -> str | None:
    try:
        reader = _get_ocr()
        results = reader.readtext(path, detail=0)
        for text in results:
            m = _TWITTER_USERNAME_RE.search(text)
            if m:
                return m.group(1).lower()
    except Exception as e:
        logger.warning(f"[ProofAuto] OCR error: {e}")
    return None


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}


def _is_image(att: discord.Attachment) -> bool:
    ext = os.path.splitext(att.filename)[1].lower()
    return ext in IMAGE_EXTENSIONS or (att.content_type or "").startswith("image/")


def _safe_unlink(path: str):
    """Best-effort temp cleanup. On Windows a delete can fail if a handle is
    still open, so swallow OSError rather than crashing the listener."""
    try:
        os.unlink(path)
    except OSError:
        pass


# ── Cog ───────────────────────────────────────────────────────────────────────
class ProofAutomationTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── Staff channel helpers ─────────────────────────────────────────────────
    def _get_staff_channel(self) -> discord.TextChannel | None:
        return self.bot.get_channel(STAFF_REVIEW_CHANNEL_ID)

    def _get_copy_proof_channel(self) -> discord.TextChannel | None:
        return self.bot.get_channel(COPY_PROOF_CHANNEL_ID)

    async def _fetch_original_image_bytes(self, match: dict) -> tuple[bytes, str] | None:
        """Re-download the ORIGINAL submission's image so staff can compare it
        side-by-side with the detected one. All proofs are posted in their
        guild's watch channel, so we look it up there. Returns (bytes, filename)
        or None if the original can't be fetched (deleted / no access)."""
        gid = match.get("original_guild_id")
        mid = match.get("original_message_id")
        cfg = GUILD_CONFIG.get(gid) if gid else None
        if not cfg or not mid:
            return None
        ch = self.bot.get_channel(cfg["watch_channel_id"])
        if ch is None:
            return None
        try:
            orig_msg = await ch.fetch_message(mid)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        imgs = [a for a in orig_msg.attachments if _is_image(a)]
        if not imgs:
            return None
        try:
            data = await imgs[0].read()
        except (discord.HTTPException, discord.NotFound):
            return None
        return data, os.path.basename(imgs[0].filename)

    async def _post_stolen_review(self, message: discord.Message, cfg: dict,
                                   analysis: dict, match: dict,
                                   auto_actioned: bool = True):
        """Post a detailed copy-proof diagnostic (old vs new image + what caused
        the match) to the copy-proof channel.

        auto_actioned=True  → EXACT match: user was already warned (confirmed).
        auto_actioned=False → PERCEPTUAL match: staff review only, user untouched.
        """
        # EXACT (confirmed) → live copy-proof channel; PERCEPTUAL (testing) →
        # separate testing channel.
        chan_id = COPY_PROOF_CHANNEL_ID if auto_actioned else PERCEPTUAL_LOG_CHANNEL_ID
        ch = self.bot.get_channel(chan_id)
        if ch is None:
            logger.warning(f"[ProofAuto] log channel {chan_id} not found")
            return

        original_user_id = match.get("original_user_id")
        original_mention = f"<@{original_user_id}>" if original_user_id else "unknown"
        submitted_at = discord.utils.format_dt(message.created_at, style='R')

        # Strike record: remember this flag and fetch how many came before.
        # COUNT ONLY — displayed for staff, never drives any action.
        prior_exact, prior_perc = 0, 0
        try:
            prior_exact, prior_perc = await _record_flag_and_count(
                message.guild.id, message.author.id,
                "exact" if auto_actioned else "perceptual",
                match.get("match_type"), message.id
            )
        except Exception as e:
            logger.warning(f"[ProofAuto] failed to record stolen flag: {e}")

        # Time-window suspicion: a re-post minutes after the original is far
        # more suspicious than one months later.
        orig_ts = match.get("original_submitted_at")
        delta_field = "unknown"
        if orig_ts:
            delta = abs(time.time() - orig_ts)
            ago = _humanize_seconds(delta)
            if delta <= 600:
                delta_field = f"🔴 {ago} after original — HIGH suspicion"
            elif delta <= 3600:
                delta_field = f"🟠 {ago} after original — elevated"
            else:
                delta_field = f"🟡 {ago} after original"

        if auto_actioned:
            title = "🚨 Stolen Proof — CONFIRMED (exact match)"
            color = discord.Color.red()
            footer = ("User auto-warned. Exact re-upload — zero false-positive risk."
                      if STOLEN_WARN_USER else
                      "LOG ONLY (observation) — user NOT warned. Exact re-upload.")
        else:
            title = "👀 Possible Copied Proof — REVIEW (look-alike)"
            color = discord.Color.orange()
            footer = "LOG ONLY — perceptual match, staff review. User NOT warned/blocked."

        embed = discord.Embed(title=title, color=color, timestamp=message.created_at)
        embed.add_field(name="Server", value=cfg["name"], inline=True)
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
        embed.add_field(name="Submitted by", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Original submitter", value=f"{original_mention}", inline=True)
        embed.add_field(name="Match type", value=match.get("match_type", "?"), inline=True)
        if prior_exact or prior_perc:
            parts = []
            if prior_exact:
                parts.append(f"{prior_exact}× confirmed")
            if prior_perc:
                parts.append(f"{prior_perc}× look-alike")
            embed.add_field(
                name="⚠️ Prior flags",
                value=f"This user was flagged **{' + '.join(parts)}** before (count only — no action taken)",
                inline=False,
            )
        else:
            embed.add_field(name="Prior flags", value="First flag for this user", inline=False)

        # ── "What caused it" — detailed diagnostics for review ────────────────
        diag = [f"**Why flagged:** {match.get('detail', '—')}"]
        # Filename match — SUPPORTING clue only (shown only when something else
        # already triggered this embed); generic paste-names are ignored.
        orig_fn = match.get("original_filename")
        new_fn  = analysis.get("filename")
        if (orig_fn and new_fn and orig_fn.lower() == new_fn.lower()
                and orig_fn.lower() not in GENERIC_FILENAMES):
            diag.append(f"**Filename match:** both uploads named `{orig_fn}` (supporting clue only)")
        # pHash is only computed for perceptual matches; exact matches short-circuit
        # before it runs, so only show it when we actually have it.
        if analysis.get("phash"):
            diag.append(f"**Detected pHash:** `{analysis['phash']}`")
        diag.append(f"**Detected SHA-256:** `{(analysis.get('sha256') or '')[:24]}…`")
        # detected image dimensions + size
        try:
            from PIL import Image as _Img
            with _Img.open(analysis["tmp_path"]) as im:
                w, h = im.size
            sz = os.path.getsize(analysis["tmp_path"])
            diag.append(f"**Detected image:** {w}×{h}px, {sz//1024} KB, `{analysis.get('filename')}`")
        except Exception:
            pass
        if analysis.get("phash"):
            diag.append(f"**pHash threshold:** ≤{PHASH_DUPE_THRESHOLD} (256-bit) flags a match")
        embed.add_field(name="🔍 Diagnosis", value="\n".join(diag)[:1024], inline=False)

        # Flag when the original came from a different server
        orig_gid = match.get("original_guild_id")
        if orig_gid and orig_gid != message.guild.id:
            orig_name = GUILD_CONFIG.get(orig_gid, {}).get("name", str(orig_gid))
            embed.add_field(name="⚠️ Cross-server", value=f"Original posted in **{orig_name}**", inline=False)

        embed.add_field(name="Time vs original", value=delta_field, inline=True)
        if analysis.get("exif"):
            embed.add_field(name="EXIF", value=analysis["exif"][:1024], inline=False)
        embed.add_field(name="Submitted", value=submitted_at, inline=True)
        embed.add_field(name="Jump to message", value=f"[Click here]({message.jump_url})", inline=True)

        # Old vs new image, side by side. The labeled composite (OLD left, NEW
        # right) renders inside the embed; full-size originals stay attached
        # so staff can still download/inspect them (and save NEW_/OLD_ pairs).
        orig = await self._fetch_original_image_bytes(match)
        original_file = None
        compare_file = None
        if orig is not None:
            original_file = discord.File(io.BytesIO(orig[0]), filename=f"OLD_{orig[1]}")
            if COMPARISON_IMAGE_ENABLED:
                try:
                    loop = asyncio.get_running_loop()
                    jpg = await loop.run_in_executor(
                        None, _build_comparison_jpg, orig[0], analysis["tmp_path"]
                    )
                    compare_file = discord.File(io.BytesIO(jpg), filename="COMPARE_old_vs_new.jpg")
                    embed.set_image(url="attachment://COMPARE_old_vs_new.jpg")
                except Exception as e:
                    logger.warning(f"[ProofAuto] comparison image failed: {e}")
        if orig is not None:
            embed.add_field(
                name="🖼️ Compare images",
                value=("📌 OLD (left) = the earlier proof  •  🆕 NEW (right) = just submitted\n"
                       "Full-size `OLD_…` / `NEW_…` files attached."
                       if compare_file else
                       "🆕 `NEW_…` = just submitted  •  📌 `OLD_…` = the earlier proof it matched"),
                inline=False,
            )
        else:
            embed.add_field(
                name="🖼️ Compare images",
                value="🆕 `NEW_…` attached. ⚠️ OLD image unavailable (deleted / not accessible).",
                inline=False,
            )
        embed.set_footer(text=footer)

        try:
            with open(analysis["tmp_path"], "rb") as f:
                detected = discord.File(
                    io.BytesIO(f.read()),
                    filename=f"NEW_{os.path.basename(analysis['tmp_path'])}"
                )
            files = [detected] + ([original_file] if original_file else []) \
                               + ([compare_file] if compare_file else [])
            await ch.send(embed=embed, files=files)
        except (discord.Forbidden, discord.HTTPException, OSError) as e:
            logger.warning(f"[ProofAuto] failed to post copy-proof log: {e}")

    async def _post_scam_alert(self, message: discord.Message, cfg: dict,
                               analysis: dict, confidence: float, deleted: bool):
        ch = self.bot.get_channel(SCAM_ALERT_CHANNEL_ID)
        if ch is None:
            logger.warning(f"[ProofAuto] scam alert channel {SCAM_ALERT_CHANNEL_ID} not found")
            return

        embed = discord.Embed(
            title="🚨 Scam Post Detected",
            color=discord.Color.red(),
            timestamp=message.created_at,
        )
        embed.add_field(name="Server", value=cfg["name"], inline=True)
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
        embed.add_field(name="Posted by", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Confidence", value=f"{confidence * 100:.1f}%", inline=True)
        if deleted:
            embed.add_field(name="Action", value="🗑️ Message auto-deleted", inline=True)
        else:
            embed.add_field(name="Action", value="👀 Observe only — message NOT touched", inline=True)
            embed.add_field(name="Jump to message", value=f"[Click here]({message.jump_url})", inline=True)
        embed.set_footer(text=f"YOLO class {SCAM_CLASS} (Scam) ≥ {SCAM_CONF_THRESHOLD:.0%} • "
                              f"delete={'on' if SCAM_DELETE_ENABLED else 'off'}")

        try:
            with open(analysis["tmp_path"], "rb") as f:
                file = discord.File(io.BytesIO(f.read()),
                                    filename=f"SCAM_{os.path.basename(analysis['tmp_path'])}")
            await ch.send(embed=embed, file=file)
        except (discord.Forbidden, discord.HTTPException, OSError) as e:
            logger.warning(f"[ProofAuto] failed to post scam alert: {e}")

    async def _post_heads_up(self, message: discord.Message, cfg: dict,
                              tmp_path: str, class_id: int, confidence: float,
                              reason: str):
        ch = self._get_staff_channel()
        if ch is None:
            return

        class_name = CLASS_NAMES.get(class_id, f"Class {class_id}")
        conf_pct = f"{confidence * 100:.1f}%"

        embed = discord.Embed(
            title="👀 Low Confidence Detection",
            color=discord.Color.orange(),
            timestamp=message.created_at,
        )
        embed.add_field(name="Server", value=cfg["name"], inline=True)
        embed.add_field(name="Channel", value=f"<#{message.channel.id}>", inline=True)
        embed.add_field(name="Submitted by", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Detected class", value=f"`{class_id}` — {class_name}", inline=True)
        embed.add_field(name="Confidence", value=conf_pct, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Jump to message", value=f"[Click here]({message.jump_url})", inline=True)
        embed.set_footer(text="No action taken — informational only")

        try:
            with open(tmp_path, "rb") as f:
                file = discord.File(f, filename=os.path.basename(tmp_path))
                await ch.send(embed=embed, file=file)
        except (discord.Forbidden, discord.HTTPException, OSError) as e:
            logger.warning(f"[ProofAuto] failed to post heads-up: {e}")

    # ── Listener ──────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if not message.guild:
            return

        cfg = GUILD_CONFIG.get(message.guild.id)
        if cfg is None:
            return
        if message.channel.id != cfg["watch_channel_id"]:
            return

        images = [a for a in message.attachments if _is_image(a)]
        if not images:
            return

        if not await _is_enabled(message.guild.id):
            # Toggled off = no replies/roles/detection, but the fingerprint
            # collector still runs — otherwise images posted while disabled
            # would be blind spots that can be stolen forever.
            await self._collect_fingerprints(message, images)
            return

        # Every image is classified. If ANY image is a valid creator-code proof,
        # access is granted (see _process_images).
        await self._process_images(message, images, cfg)

    async def _store_fingerprints_only(self, guild_id: int, user_id: int,
                                       message_id: int, analyses: list[dict]):
        """Store fingerprints for all downloaded images, computing pHash for any
        that haven't had it yet (cheap — no YOLO/OCR)."""
        loop = asyncio.get_running_loop()
        for a in analyses:
            if a["phash"] is None:
                try:
                    a["phash"] = await loop.run_in_executor(None, _compute_phash, a["tmp_path"])
                except Exception as e:
                    logger.warning(f"[ProofAuto] pHash failed for {a['filename']}: {e}")
            await _store_submission(
                guild_id, user_id, a["phash"], a.get("username"), message_id,
                sha256=a.get("sha256"), attachment_id=a.get("attachment_id"),
                filename=a.get("filename")
            )

    async def _collect_fingerprints(self, message: discord.Message,
                                    attachments: list[discord.Attachment]):
        """Collector-only path (automation toggled off): download + store
        fingerprints, no detection, no classification, no replies."""
        analyses = await asyncio.gather(*(self._download_and_fingerprint(a) for a in attachments))
        analyses = [a for a in analyses if a is not None]
        if not analyses:
            return
        try:
            await self._store_fingerprints_only(
                message.guild.id, message.author.id, message.id, analyses
            )
        finally:
            for a in analyses:
                _safe_unlink(a["tmp_path"])

    async def _download_and_fingerprint(self, att: discord.Attachment) -> dict | None:
        """Phase 1 — download + CHEAP fingerprints only (sha256, EXIF, attachment
        id). No model inference here, so an exact stolen match can short-circuit
        before we pay for YOLO/OCR.

        Returns {filename, tmp_path, attachment_id, sha256, exif, phash, probs,
        username} (heavy fields start None) or None if the download failed.
        """
        suffix = os.path.splitext(att.filename)[1].lower() or ".jpg"
        # Close the temp handle before att.save reopens it — required on Windows.
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await att.save(tmp_path, use_cached=False)
        except (discord.HTTPException, discord.NotFound, OSError) as e:
            logger.warning(f"[ProofAuto] failed to download {att.filename}: {e}")
            _safe_unlink(tmp_path)
            return None

        loop = asyncio.get_running_loop()
        sha256, exif = await asyncio.gather(
            loop.run_in_executor(None, _compute_sha256, tmp_path),
            loop.run_in_executor(None, _extract_exif, tmp_path),
            return_exceptions=True
        )
        if isinstance(sha256, Exception):
            logger.warning(f"[ProofAuto] sha256 failed: {sha256}")
            sha256 = None
        if isinstance(exif, Exception):
            exif = None

        return {
            "filename": att.filename,
            "tmp_path": tmp_path,
            "attachment_id": att.id,
            "sha256": sha256,
            "exif": exif,
            "phash": None,
            "phash_flip": None,
            "probs": None,
            "username": None,
        }

    async def _run_heavy_analysis(self, items: list[dict]):
        """Phase 3 — the expensive signals: ONE batched YOLO forward pass for all
        images, plus pHash + OCR per image in parallel. Mutates `items` in place.
        """
        loop  = asyncio.get_running_loop()
        paths = [it["tmp_path"] for it in items]

        # Single batched YOLO call instead of one per image. A YOLO crash must
        # not abort the handler — fingerprints still need to be stored after.
        try:
            batch_probs = await loop.run_in_executor(None, _run_yolo_batch, paths)
        except Exception as e:
            logger.warning(f"[ProofAuto] batched YOLO failed: {e}")
            batch_probs = [None] * len(paths)

        # pHash is cheap and still used as the dedup storage key, so always run.
        phashes = await asyncio.gather(
            *(loop.run_in_executor(None, _compute_phash, p) for p in paths),
            return_exceptions=True
        )
        # Mirrored pHash — query-time flip-evasion check (never stored).
        if PHASH_CHECK_MIRROR:
            flip_hashes = await asyncio.gather(
                *(loop.run_in_executor(None, _compute_phash_flip, p) for p in paths),
                return_exceptions=True
            )
        else:
            flip_hashes = [None] * len(paths)
        # OCR (EasyOCR) is expensive and ONLY feeds the username stolen signal.
        # With OCR disabled, skip it entirely — saves CPU + RAM (EasyOCR never loads).
        if OCR_USERNAME_ENABLED:
            usernames = await asyncio.gather(
                *(loop.run_in_executor(None, _extract_twitter_username, p) for p in paths),
                return_exceptions=True
            )
        else:
            usernames = [None] * len(paths)

        for it, ph, fph, probs, un in zip(items, phashes, flip_hashes, batch_probs, usernames):
            it["phash"]      = None if isinstance(ph, Exception) else ph
            it["phash_flip"] = None if isinstance(fph, Exception) else fph
            it["probs"]      = probs
            it["username"]   = None if isinstance(un, Exception) else un

    async def _process_images(self, message: discord.Message,
                              attachments: list[discord.Attachment], cfg: dict):
        guild_id = message.guild.id
        user_id  = message.author.id

        # Phase 1: download + cheap fingerprints (no model inference yet).
        analyses = await asyncio.gather(*(self._download_and_fingerprint(a) for a in attachments))
        analyses = [a for a in analyses if a is not None]
        if not analyses:
            return

        try:
            stolen_logged = False  # avoid double-logging exact + perceptual

            # Phase 2: EXACT stolen check (SHA-256 / attachment) — zero false positives.
            if STOLEN_CHECKS_ENABLED and EXACT_STOLEN_ENABLED:
                for a in analyses:
                    match = await _find_exact_stolen(a, user_id)
                    if match:
                        logger.info(
                            f"[ProofAuto] EXACT stolen ({match['match_type']}) "
                            f"user={user_id} guild={guild_id} "
                            f"orig_user={match['original_user_id']} "
                            f"orig_guild={match['original_guild_id']}"
                        )
                        await self._post_stolen_review(message, cfg, a, match, auto_actioned=True)
                        stolen_logged = True
                        if STOLEN_WARN_USER:
                            # Enforcement mode: warn the user and stop — but
                            # still fingerprint-store everything in this message
                            # first, so the thief's copy (and any other images
                            # posted with it) stay matchable later.
                            await self._store_fingerprints_only(guild_id, user_id, message.id, analyses)
                            await self._safe_reply(message, STOLEN_MSG)
                            return
                        break  # log-only: logged it, fall through to normal grading

            # Phase 3: heavy analysis — batched YOLO + pHash (+ OCR if enabled).
            # A failure here must not abort the handler: the stolen checks below
            # degrade gracefully and the fingerprints still get stored.
            try:
                await self._run_heavy_analysis(analyses)
            except Exception as e:
                logger.warning(f"[ProofAuto] heavy analysis failed: {e}")

            # Phase 4: PERCEPTUAL stolen check (256-bit pHash) — LOG ONLY, never
            # warns/blocks the user. Skip if we already logged an exact match.
            if STOLEN_CHECKS_ENABLED and PERCEPTUAL_STOLEN_ENABLED and not stolen_logged:
                for a in analyses:
                    match = await _find_fuzzy_stolen(a, user_id)
                    if match:
                        logger.info(
                            f"[ProofAuto] PERCEPTUAL match ({match['match_type']}) "
                            f"user={user_id} guild={guild_id} "
                            f"orig_user={match['original_user_id']} "
                            f"orig_guild={match['original_guild_id']} → log only"
                        )
                        await self._post_stolen_review(message, cfg, a, match, auto_actioned=False)
                        break  # one log per message; never block the user

            # ── Store every submission (for dedup) ────────────────────────────
            # Stored even when pHash failed (e.g. PIL-unopenable format) — the
            # SHA-256/attachment exact layer must still protect those images.
            for a in analyses:
                await _store_submission(
                    guild_id, user_id, a["phash"], a["username"], message.id,
                    sha256=a.get("sha256"), attachment_id=a.get("attachment_id"),
                    filename=a.get("filename")
                )

            # ── Score every image (top class + full distribution) ─────────────
            scored = []  # list of (class_id, confidence, probs, analysis)
            for a in analyses:
                probs = a["probs"]
                if not probs:
                    continue
                cid = max(range(len(probs)), key=lambda i: probs[i])
                conf = probs[cid]
                scored.append((cid, conf, probs, a))
                logger.info(
                    f"[ProofAuto] {a['filename']} → class {cid} "
                    f"({CLASS_NAMES.get(cid, '?')}) conf={conf:.3f} "
                    f"user={user_id} guild={guild_id}"
                )

                # Training-data near-misses: any class whose probability is in
                # [TRAINING_LOG_MIN_CONF, its threshold) — what the bot almost
                # caught. Logged per class off the full distribution.
                for c, t in CLASS_CONF_THRESHOLD.items():
                    if t is None or c >= len(probs):
                        continue
                    pc = probs[c]
                    if TRAINING_LOG_MIN_CONF <= pc < t:
                        logger.info(
                            f"[ProofAuto][TRAIN] near-miss class {c} "
                            f"({CLASS_NAMES.get(c, '?')}) conf={pc:.3f} "
                            f"thresh={t} active={c in cfg['active_classes']} "
                            f"user={user_id} guild={guild_id} "
                            f"file={a['filename']} msg={message.jump_url}"
                        )
            if not scored:
                return

            # ── Scam check (class 7) — runs BEFORE anything can grant/reply ───
            # Only on models that have the class (len-guarded). Observe mode
            # (delete off): alert staff, then continue normal processing so
            # behaviour is unchanged while the class earns trust.
            if SCAM_ALERT_ENABLED or SCAM_DELETE_ENABLED:
                scam_best, scam_img = 0.0, None
                for _cid, _conf, probs, a in scored:
                    if len(probs) > SCAM_CLASS and probs[SCAM_CLASS] > scam_best:
                        scam_best, scam_img = probs[SCAM_CLASS], a
                if scam_img is not None and scam_best >= SCAM_CONF_THRESHOLD:
                    logger.info(
                        f"[ProofAuto] SCAM detected (p={scam_best:.3f}) "
                        f"user={user_id} guild={guild_id} file={scam_img['filename']}"
                    )
                    deleted = False
                    if SCAM_DELETE_ENABLED:
                        try:
                            await message.delete()
                            deleted = True
                        except (discord.Forbidden, discord.HTTPException) as e:
                            logger.warning(f"[ProofAuto] failed to delete scam message: {e}")
                    if SCAM_ALERT_ENABLED:
                        await self._post_scam_alert(message, cfg, scam_img, scam_best, deleted)
                    if deleted:
                        return  # message gone — nothing left to grade

            # Best creator-code probability across all images
            cc_thresh = CLASS_CONF_THRESHOLD.get(CREATOR_CODE_CLASS)
            cc_best_conf, cc_best_img = 0.0, None
            for _cid, _conf, probs, a in scored:
                if probs[CREATOR_CODE_CLASS] > cc_best_conf:
                    cc_best_conf, cc_best_img = probs[CREATOR_CODE_CLASS], a

            # ── Access-grant override ─────────────────────────────────────────
            # Granting access is the highest-priority outcome, so if ANY image
            # shows the creator code correctly at/above its threshold, grant —
            # even if a "zoom out" / "press search" class ranked #1 on that image.
            if (CREATOR_CODE_CLASS in cfg["active_classes"]
                    and cc_thresh is not None
                    and cc_best_conf >= cc_thresh):
                logger.info(
                    f"[ProofAuto] Creator-code grant (p={cc_best_conf:.3f}) "
                    f"user={user_id} guild={guild_id}"
                )
                msg = await _get_next_creator_msg(guild_id)
                await asyncio.gather(
                    self._safe_reply(message, msg),
                    self._assign_creator_roles(message, cfg),
                    return_exceptions=True
                )
                return

            # In a multi-image message, suppress "you still need to do X" nags
            # (e.g. Following Only) — the user is likely submitting several proofs.
            multi_image = len(analyses) > 1

            # ── Otherwise act on the highest-confidence actionable image ──────
            actionable = []
            for cid, conf, _probs, a in scored:
                t = CLASS_CONF_THRESHOLD.get(cid)
                if t is None:                      # disabled class
                    continue
                if cid not in cfg["active_classes"]:
                    continue
                if multi_image and cid in MULTI_IMAGE_SUPPRESSED_CLASSES:
                    continue
                if conf >= t:
                    actionable.append((cid, conf, a))
            if actionable:
                priority = cfg.get("class_priority")
                if priority:
                    # Pick by configured hierarchy (lower rank = higher priority);
                    # tie-break and unlisted classes fall back to confidence.
                    rank = {c: i for i, c in enumerate(priority)}
                    cid, conf, a = min(actionable, key=lambda x: (rank.get(x[0], 999), -x[1]))
                else:
                    cid, conf, a = max(actionable, key=lambda x: x[1])
                if cid == 0:
                    await self._safe_reply(message, cfg["following_only_msg"])
                elif cid == 3:
                    await self._safe_reply(message, cfg["press_search_msg"])
                elif cid == 5:
                    await self._safe_reply(message, cfg["zoom_out_msg"])
                # cid == 4 is always handled by the override above
                return

            # ── Heads-up: best near-miss active class ─────────────────────────
            candidates = []  # (class_id, conf, threshold, analysis)
            for cid, conf, _probs, a in scored:
                t = CLASS_CONF_THRESHOLD.get(cid)
                if t is None or cid not in cfg["active_classes"]:
                    continue
                if multi_image and cid in MULTI_IMAGE_SUPPRESSED_CLASSES:
                    continue
                if HEADS_UP_MIN_CONF <= conf < t:
                    candidates.append((cid, conf, t, a))
            # Surface a near-miss creator-code grant too (even if not top-1)
            if (CREATOR_CODE_CLASS in cfg["active_classes"] and cc_thresh is not None
                    and HEADS_UP_MIN_CONF <= cc_best_conf < cc_thresh):
                candidates.append((CREATOR_CODE_CLASS, cc_best_conf, cc_thresh, cc_best_img))
            if candidates:
                cid, conf, t, a = max(candidates, key=lambda x: x[1])
                reason = (
                    f"Confidence {conf*100:.1f}% is below the {t*100:.0f}% action "
                    f"threshold for `{cid}` ({CLASS_NAMES.get(cid, '?')})"
                )
                await self._post_heads_up(message, cfg, a["tmp_path"], cid, conf, reason)

        finally:
            for a in analyses:
                _safe_unlink(a["tmp_path"])

    async def _assign_creator_roles(self, message: discord.Message, cfg: dict):
        member = message.author
        if not isinstance(member, discord.Member):
            return
        roles_to_add = [
            message.guild.get_role(rid)
            for rid in cfg["creator_code_role_ids"]
            if message.guild.get_role(rid) is not None
            and message.guild.get_role(rid) not in member.roles
        ]
        if not roles_to_add:
            return
        try:
            await member.add_roles(*roles_to_add, reason="Creator code proof verified")
            logger.info(
                f"[ProofAuto] Assigned roles {[r.id for r in roles_to_add]} "
                f"to {member.id} in guild {message.guild.id}"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"[ProofAuto] failed to assign roles to {member.id}: {e}")

    async def _safe_reply(self, message: discord.Message, content: str):
        try:
            await message.reply(content, mention_author=False)
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"[ProofAuto] failed to reply: {e}")


async def setup(bot):
    await bot.add_cog(ProofAutomationTask(bot))
    # NOTE: models are loaded lazily on the first proof image (not at startup),
    # so the bot's idle RAM stays low until then. Once loaded they stay resident.
    logger.info("✅ ProofAutomationTask cog loaded")
