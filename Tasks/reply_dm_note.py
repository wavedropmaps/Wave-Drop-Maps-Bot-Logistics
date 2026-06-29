"""
Reply-DM Sticky Note Helpers
============================
Three primitives that read/write the `reply_dm_note` table in the shared DB.

- arm_note(user_id, guild_id, source_bot_id)
    Place / overwrite a sticky note saying "this user has a pending staff reply
    from <guild_id>". Called by reply_dm_duty after each successful DM.

- wipe_note(user_id)
    Remove the note. Called when any non-reply_dm_duty bot DM goes out to
    the user, or after the auto-reply fires successfully.

- get_active_note(user_id)
    Returns (guild_id, source_bot_id) if a non-expired note exists for the user, else None.
    Notes expire 48h after armed_at.
    Both values are needed so auto_reply.py can verify it should fire on THIS bot.

Helpers are self-managing: they create the table on first call so they
work regardless of which bot is up first.
"""

import logging
import time

import aiosqlite

logger = logging.getLogger('discord')

SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"
NOTE_TTL_SECONDS = 48 * 3600

async def _ensure_schema(db: aiosqlite.Connection) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS — safe to call on every connection."""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reply_dm_note (
            user_id        INTEGER PRIMARY KEY,
            guild_id       INTEGER NOT NULL,
            source_bot_id  INTEGER,
            armed_at       REAL NOT NULL
        )
    """)
    await db.execute("""
        CREATE INDEX IF NOT EXISTS idx_reply_dm_note_armed_at
        ON reply_dm_note(armed_at)
    """)


async def arm_note(user_id: int, guild_id: int, source_bot_id: int) -> None:
    """Insert or replace the sticky note for user_id with a fresh armed_at."""
    try:
        async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await _ensure_schema(db)
            await db.execute(
                """
                INSERT OR REPLACE INTO reply_dm_note
                (user_id, guild_id, source_bot_id, armed_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, guild_id, source_bot_id, time.time()),
            )
            await db.commit()
        logger.info(f"[ReplyDMNote] ARMED user={user_id} guild={guild_id} src_bot={source_bot_id}")
    except Exception as e:
        logger.error(f"[ReplyDMNote] arm_note failed for user={user_id}: {e}", exc_info=True)


async def wipe_note(user_id: int) -> None:
    """Delete the sticky note for user_id (no-op if no row exists)."""
    try:
        async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await _ensure_schema(db)
            cursor = await db.execute(
                "DELETE FROM reply_dm_note WHERE user_id = ?",
                (user_id,),
            )
            await db.commit()
            if cursor.rowcount:
                logger.info(f"[ReplyDMNote] WIPED user={user_id}")
    except Exception as e:
        logger.error(f"[ReplyDMNote] wipe_note failed for user={user_id}: {e}", exc_info=True)


async def get_active_note(user_id: int) -> tuple[int, int] | None:
    """Return (guild_id, source_bot_id) of a non-expired note for user_id, or None.

    Both values are needed so auto_reply.py can verify it should fire on THIS bot,
    not just any bot that sees the incoming DM.
    """
    cutoff = time.time() - NOTE_TTL_SECONDS
    try:
        async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await _ensure_schema(db)
            async with db.execute(
                "SELECT guild_id, source_bot_id FROM reply_dm_note WHERE user_id = ? AND armed_at > ?",
                (user_id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
        return (int(row[0]), int(row[1])) if row else None
    except Exception as e:
        logger.error(f"[ReplyDMNote] get_active_note failed for user={user_id}: {e}", exc_info=True)
        return None


# No-op setup() so Main.py's cog loader doesn't log an error if it tries
# to load this. This module is a helper, not a cog.
async def setup(bot):
    pass
