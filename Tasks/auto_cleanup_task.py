"""
Auto Join Ghost-Ping Cleanup Task
==================================
Detects and deletes leaked auto-join ghost-ping messages (user mentions with no text).
Runs on BOTH bots:
- Management Bot (primary cleanup)
- Logistics Bot (fallback/double-check)

Config stored in shared DB (dm_shared_queue.db) under auto_cleanup_config table.
Both bots see the same watched channels and gracefully handle already-deleted messages.
"""

import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import asyncio
import aiosqlite
import logging

logger = logging.getLogger('discord')

SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"
GRACE_PERIOD_SECONDS = 5.0
SWEEP_INTERVAL_HOURS = 24
SWEEP_LOOKBACK_HOURS = 48
SWEEP_STARTUP_DELAY_SECONDS = 60
BULK_DELETE_THRESHOLD_DAYS = 14
SINGLE_DELETE_SPACING_SECONDS = 0.3


async def _get_config(guild_id: int) -> dict:
    """Read cleanup config from tippy_join_config (auto-join ping channels)."""
    async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT channel_ids, enabled, log_channel_id FROM tippy_join_config WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return {'enabled': False, 'watched_channel_ids': [], 'log_channel_id': None}

    import json
    try:
        watched = json.loads(row['channel_ids'] or '[]')
    except (json.JSONDecodeError, TypeError):
        watched = []

    return {
        'enabled': bool(row['enabled']),
        'watched_channel_ids': watched,
        'log_channel_id': row['log_channel_id'],
    }


def is_ghost_ping(message: discord.Message) -> bool:
    """Detect auto-join ghost pings: pure mentions (no other text) from bot."""
    if message.author is None or not message.author.bot:
        return False

    content = message.content.strip()
    if not content:
        return False

    # Ghost ping format: "<@id1> <@id2> ..." with optional whitespace
    # Must be ONLY mentions, no other text
    parts = content.split()
    if not parts:
        return False

    # Check if every part is a user mention (format: <@id> or <@!id>)
    for part in parts:
        if not (part.startswith('<@') and part.endswith('>')):
            return False
        # Extract ID and verify it's numeric
        inner = part[2:-1]
        if inner.startswith('!'):
            inner = inner[1:]
        if not inner.isdigit():
            return False

    return True


class AutoCleanupTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._live_deleted = 0
        self._last_sweep_at = None
        self._last_sweep_count = 0
        self.daily_sweep.start()

    def cog_unload(self):
        self.daily_sweep.cancel()

    async def _post_log(self, guild: discord.Guild, text: str):
        """Post cleanup event to log channel if configured."""
        cfg = await _get_config(guild.id)
        log_channel_id = cfg.get('log_channel_id')
        if not log_channel_id:
            return
        channel = self.bot.get_channel(log_channel_id)
        if channel is None:
            return
        try:
            await channel.send(text)
        except discord.HTTPException:
            pass

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Live cleanup: catch and delete ghost pings shortly after they arrive."""
        if message.guild is None:
            return

        cfg = await _get_config(message.guild.id)
        if not cfg.get('enabled'):
            return

        watched = cfg.get('watched_channel_ids', [])
        if message.channel.id not in watched:
            return

        if not is_ghost_ping(message):
            return

        logger.info(
            f"[AutoCleanup] Ghost ping detected in #{message.channel} ({message.guild.name}), msg_id={message.id}"
        )

        await asyncio.sleep(GRACE_PERIOD_SECONDS)

        try:
            still_there = await message.channel.fetch_message(message.id)
        except discord.NotFound:
            logger.info(f"[AutoCleanup] Ghost ping self-cleaned msg_id={message.id} (skip)")
            return
        except discord.Forbidden:
            logger.warning(
                f"[AutoCleanup] No Read Message History in #{message.channel}; cannot verify cleanup"
            )
            return
        except discord.HTTPException:
            logger.exception("[AutoCleanup] Failed to refetch potential ghost ping")
            return

        try:
            await still_there.delete()
            self._live_deleted += 1
            logger.info(
                f"[AutoCleanup] Deleted leaked ghost ping msg_id={message.id} in #{message.channel}"
            )
        except discord.NotFound:
            pass  # Already gone
        except discord.Forbidden:
            logger.warning(
                f"[AutoCleanup] Missing Manage Messages in #{message.channel}; cannot delete ghost ping"
            )
        except discord.HTTPException:
            logger.exception("[AutoCleanup] Live cleanup delete failed")

    @tasks.loop(hours=SWEEP_INTERVAL_HOURS)
    async def daily_sweep(self):
        """Daily sweep: scan history for any ghost pings that leaked through live cleanup."""
        total_deleted = 0
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=SWEEP_LOOKBACK_HOURS)
        min_age = timedelta(seconds=GRACE_PERIOD_SECONDS * 4)

        for guild in list(self.bot.guilds):
            try:
                cfg = await _get_config(guild.id)
                if not cfg.get('enabled'):
                    continue
                watched_ids = cfg.get('watched_channel_ids', [])
                if not watched_ids:
                    continue

                for channel_id in watched_ids:
                    channel = guild.get_channel(channel_id)
                    if channel is None:
                        logger.warning(
                            f"[AutoCleanup] Watched channel {channel_id} not found in {guild.name}"
                        )
                        continue

                    candidates = []
                    try:
                        async for msg in channel.history(after=cutoff, limit=None):
                            if not is_ghost_ping(msg):
                                continue
                            if (now - msg.created_at) < min_age:
                                continue
                            candidates.append(msg)
                    except discord.Forbidden:
                        logger.warning(
                            f"[AutoCleanup] No history access in #{channel} ({guild.name})"
                        )
                        continue
                    except discord.HTTPException:
                        logger.exception(
                            f"[AutoCleanup] Failed to fetch history in #{channel} ({guild.name})"
                        )
                        continue

                    recent = [
                        m for m in candidates
                        if (now - m.created_at).days < BULK_DELETE_THRESHOLD_DAYS
                    ]
                    old = [
                        m for m in candidates
                        if (now - m.created_at).days >= BULK_DELETE_THRESHOLD_DAYS
                    ]
                    deleted = 0

                    # Bulk delete recent messages (<14 days)
                    for i in range(0, len(recent), 100):
                        chunk = recent[i:i + 100]
                        try:
                            if len(chunk) == 1:
                                await chunk[0].delete()
                                deleted += 1
                            else:
                                await channel.delete_messages(chunk)
                                deleted += len(chunk)
                        except discord.NotFound:
                            pass  # Already deleted by the other bot
                        except discord.HTTPException:
                            logger.exception(f"[AutoCleanup] Bulk delete failed in #{channel}")

                    # Single delete old messages (>= 14 days) with spacing
                    for m in old:
                        try:
                            await m.delete()
                            deleted += 1
                            await asyncio.sleep(SINGLE_DELETE_SPACING_SECONDS)
                        except discord.NotFound:
                            pass  # Already deleted
                        except discord.Forbidden:
                            logger.warning(
                                f"[AutoCleanup] Missing Manage Messages in #{channel}; stopping sweep"
                            )
                            break
                        except discord.HTTPException:
                            logger.exception(f"[AutoCleanup] Single delete failed in #{channel}")

                    total_deleted += deleted
                    if deleted:
                        logger.info(
                            f"[AutoCleanup] Swept #{channel} ({guild.name}): scanned {len(candidates)}, deleted {deleted}"
                        )

            except Exception:
                logger.exception(
                    f"[AutoCleanup] Sweep failed for guild {guild.id} ({guild.name})"
                )

        self._last_sweep_at = datetime.now(timezone.utc)
        self._last_sweep_count = total_deleted
        logger.info(f"[AutoCleanup] Daily sweep complete. Total deleted: {total_deleted}")

    @daily_sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(SWEEP_STARTUP_DELAY_SECONDS)


async def setup(bot):
    await bot.add_cog(AutoCleanupTask(bot))
    logger.info("✅ AutoCleanupTask cog loaded")
