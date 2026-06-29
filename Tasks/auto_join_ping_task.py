"""
Auto Join Ghost-Ping Task
==========================
On member join, batches a ghost-ping (mention + immediate delete) in
configured channels. Uses shared SQLite DB so both bots can run this same
cog simultaneously without producing duplicate pings — whichever bot wins
the per-member claim handles the ping, the other exits silently.

Tables (in dm_shared_queue.db):
  tippy_join_config   — per-guild settings (written by WLB commands)
  join_ping_claims    — cross-bot dedup + rejoin cooldown
"""

import asyncio
import aiosqlite
import json
import logging
import time
from collections import defaultdict
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"

CLAIM_RECORD_TTL_SECONDS = 600        # claims auto-purged after 10 minutes
CLAIM_CLEANUP_INTERVAL_SECONDS = 60.0

DEFAULT_DELETE_DELAY_MS = 1000
DEFAULT_BATCH_WINDOW_MS = 800
DEFAULT_REJOIN_COOLDOWN_SECONDS = 60

LOG_FLUSH_DELAY_SECONDS = 2.5


class AutoJoinPingTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # channel_id -> list[member_id] pending mentions
        self._pending: dict[int, list[int]] = defaultdict(list)
        # channel_id -> active batch worker Task
        self._workers: dict[int, asyncio.Task] = {}
        # channel_id -> lock guarding batch processing
        self._channel_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        # guild_id -> buffered log entries awaiting flush
        self._log_buffer: dict[int, list[dict]] = defaultdict(list)
        # guild_id -> active flush task (single consolidated log per join event)
        self._log_flush_tasks: dict[int, asyncio.Task] = {}
        self._cleanup_task: Optional[asyncio.Task] = None

    async def cog_load(self):
        await self._init_db()
        logger.info("[AutoJoinPing] DB ready")

    @commands.Cog.listener()
    async def on_ready(self):
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._claim_cleanup_loop())
            logger.info("[AutoJoinPing] Claim cleanup loop started")

    def cog_unload(self):
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        for worker in list(self._workers.values()):
            if not worker.done():
                worker.cancel()
        for flush in list(self._log_flush_tasks.values()):
            if not flush.done():
                flush.cancel()

    # ── DB schema ──────────────────────────────────────────────────────────

    async def _init_db(self):
        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")

                await db.execute("""
                    CREATE TABLE IF NOT EXISTS tippy_join_config (
                        guild_id                INTEGER PRIMARY KEY,
                        enabled                 INTEGER DEFAULT 0,
                        channel_ids             TEXT DEFAULT '[]',
                        log_channel_id          INTEGER,
                        delete_delay_ms         INTEGER DEFAULT 1000,
                        batch_window_ms         INTEGER DEFAULT 800,
                        rejoin_cooldown_seconds INTEGER DEFAULT 60,
                        total_pings             INTEGER DEFAULT 0,
                        total_batches           INTEGER DEFAULT 0,
                        last_join_at            REAL
                    )
                """)

                await db.execute("""
                    CREATE TABLE IF NOT EXISTS join_ping_claims (
                        member_id         INTEGER NOT NULL,
                        guild_id          INTEGER NOT NULL,
                        claimed_by_bot_id INTEGER NOT NULL,
                        claimed_at        REAL NOT NULL,
                        PRIMARY KEY (member_id, guild_id)
                    )
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_claims_age
                    ON join_ping_claims(claimed_at)
                """)

                await db.commit()
        except Exception as e:
            logger.error(f"[AutoJoinPing] DB init error: {e}", exc_info=True)

    # ── Config helpers ─────────────────────────────────────────────────────

    async def _get_config(self, guild_id: int) -> Optional[dict]:
        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM tippy_join_config WHERE guild_id=?",
                    (guild_id,)
                ) as cursor:
                    row = await cursor.fetchone()
            if not row:
                return None
            try:
                channel_ids = json.loads(row['channel_ids'] or '[]')
            except json.JSONDecodeError:
                channel_ids = []
            return {
                'enabled': bool(row['enabled']),
                'channel_ids': channel_ids,
                'log_channel_id': row['log_channel_id'],
                'delete_delay_ms': row['delete_delay_ms'] or DEFAULT_DELETE_DELAY_MS,
                'batch_window_ms': row['batch_window_ms'] or DEFAULT_BATCH_WINDOW_MS,
                'rejoin_cooldown_seconds': (
                    row['rejoin_cooldown_seconds'] or DEFAULT_REJOIN_COOLDOWN_SECONDS
                ),
            }
        except Exception as e:
            logger.error(f"[AutoJoinPing] Config read error: {e}", exc_info=True)
            return None

    # ── Claim (dedup + rejoin cooldown) ────────────────────────────────────

    async def _try_claim(
        self, member_id: int, guild_id: int, cooldown_seconds: int
    ) -> bool:
        """Atomically determine if this bot should ghost-ping this member.

        Strategy: try INSERT (fresh joins). If row already exists and is
        within cooldown → skip. If expired → CAS update with our bot_id.
        Returns True iff this bot won.
        """
        now = time.time()
        cooldown_cutoff = now - cooldown_seconds
        bot_id = self.bot.user.id if self.bot.user else 0

        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")

                cursor = await db.execute("""
                    INSERT OR IGNORE INTO join_ping_claims
                    (member_id, guild_id, claimed_by_bot_id, claimed_at)
                    VALUES (?, ?, ?, ?)
                """, (member_id, guild_id, bot_id, now))
                await db.commit()

                if cursor.rowcount > 0:
                    return True  # Fresh claim won

                # Row exists. CAS-update only if older than cooldown.
                cursor = await db.execute("""
                    UPDATE join_ping_claims
                    SET claimed_by_bot_id = ?, claimed_at = ?
                    WHERE member_id = ? AND guild_id = ?
                      AND claimed_at < ?
                """, (bot_id, now, member_id, guild_id, cooldown_cutoff))
                await db.commit()
                return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"[AutoJoinPing] Claim error for member {member_id}: {e}", exc_info=True)
            return False

    # ── Event listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        cfg = await self._get_config(guild.id)
        if not cfg or not cfg['enabled'] or not cfg['channel_ids']:
            return

        won = await self._try_claim(
            member.id, guild.id, cfg['rejoin_cooldown_seconds']
        )
        if not won:
            logger.debug(
                f"[AutoJoinPing] Skipped member {member.id} in {guild.name} "
                f"(other bot claimed or within cooldown)"
            )
            return

        logger.info(
            f"[AutoJoinPing] Claimed member {member.id} ({member}) in {guild.name}"
        )

        for channel_id in cfg['channel_ids']:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                logger.warning(
                    f"[AutoJoinPing] Channel {channel_id} not found in {guild.name}"
                )
                continue
            if not isinstance(channel, discord.TextChannel):
                continue

            self._pending[channel_id].append(member.id)
            existing = self._workers.get(channel_id)
            if existing is None or existing.done():
                task = asyncio.create_task(self._batch_worker(channel_id, cfg))
                self._workers[channel_id] = task
                # Drop our own slot once finished — but only if a newer worker
                # hasn't already replaced us. Keeps the dict to live channels.
                task.add_done_callback(
                    lambda t, cid=channel_id: self._workers.pop(cid, None)
                    if self._workers.get(cid) is t else None
                )

    # ── Batch worker ───────────────────────────────────────────────────────

    async def _batch_worker(self, channel_id: int, cfg: dict):
        """Wait batch_window_ms, then send batched ghost-ping for pending members."""
        async with self._channel_locks[channel_id]:
            try:
                await asyncio.sleep(cfg['batch_window_ms'] / 1000.0)

                pending = self._pending.get(channel_id, [])
                if not pending:
                    return

                # Deduplicate while preserving order
                seen = set()
                unique: list[int] = []
                for mid in pending:
                    if mid not in seen:
                        seen.add(mid)
                        unique.append(mid)
                self._pending[channel_id] = []

                channel = self.bot.get_channel(channel_id)
                if channel is None:
                    logger.warning(f"[AutoJoinPing] Channel {channel_id} disappeared mid-batch")
                    return

                mentions = " ".join(f"<@{mid}>" for mid in unique)

                try:
                    msg = await channel.send(
                        content=mentions,
                        allowed_mentions=discord.AllowedMentions(
                            users=True, everyone=False, roles=False, replied_user=False
                        )
                    )
                except discord.Forbidden:
                    logger.warning(
                        f"[AutoJoinPing] Missing Send Messages in #{channel} "
                        f"({channel.guild.name})"
                    )
                    self._queue_log(
                        channel.guild, channel, unique,
                        status='failed', error='missing Send Messages'
                    )
                    return
                except discord.HTTPException as e:
                    logger.exception(f"[AutoJoinPing] Send failed in #{channel}: {e}")
                    return

                await asyncio.sleep(cfg['delete_delay_ms'] / 1000.0)

                deleted = await self._delete_with_retry(msg, channel)
                if not deleted:
                    return

                await self._update_stats(channel.guild.id, members_pinged=len(unique))

                logger.info(
                    f"[AutoJoinPing] Ghost-pinged {len(unique)} member(s) in "
                    f"#{channel} ({channel.guild.name})"
                )
                self._queue_log(
                    channel.guild, channel, unique, status='success'
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception(
                    f"[AutoJoinPing] Batch worker crashed for channel {channel_id}: {e}"
                )

        # Members who joined while this worker was already past its drain
        # (sending/deleting takes ~2s) land in _pending with no live worker —
        # on_member_join saw a not-done task and skipped spawning. Without
        # this respawn they'd only get pinged when the NEXT member joins.
        if self._pending.get(channel_id):
            task = asyncio.create_task(self._batch_worker(channel_id, cfg))
            self._workers[channel_id] = task
            task.add_done_callback(
                lambda t, cid=channel_id: self._workers.pop(cid, None)
                if self._workers.get(cid) is t else None
            )

    async def _delete_with_retry(
        self, msg: discord.Message, channel: discord.TextChannel
    ) -> bool:
        """Delete with one retry. Returns False if message remains visible."""
        for attempt in (1, 2):
            try:
                await msg.delete()
                return True
            except discord.NotFound:
                return True  # already gone
            except discord.Forbidden:
                logger.warning(
                    f"[AutoJoinPing] CRITICAL: cannot delete ghost ping in "
                    f"#{channel} ({channel.guild.name}) — missing Manage Messages"
                )
                await self._send_critical_log(
                    channel.guild,
                    f"⚠️ **GHOST PING LEAKED** in {channel.mention} — missing "
                    f"Manage Messages, message is now VISIBLE (msg_id=`{msg.id}`)"
                )
                return False
            except discord.HTTPException as e:
                logger.warning(
                    f"[AutoJoinPing] Delete attempt {attempt} failed in #{channel}: {e}"
                )
                if attempt == 1:
                    await asyncio.sleep(1.0)
                    continue
                await self._send_critical_log(
                    channel.guild,
                    f"⚠️ Failed to delete ghost ping in {channel.mention} "
                    f"after retry (msg_id=`{msg.id}`) — please remove manually"
                )
                return False
        return False

    # ── Stats ──────────────────────────────────────────────────────────────

    async def _update_stats(self, guild_id: int, members_pinged: int):
        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                await db.execute("""
                    UPDATE tippy_join_config
                    SET total_pings = total_pings + ?,
                        total_batches = total_batches + 1,
                        last_join_at = ?
                    WHERE guild_id = ?
                """, (members_pinged, time.time(), guild_id))
                await db.commit()
        except Exception as e:
            logger.error(f"[AutoJoinPing] Stats update error: {e}", exc_info=True)

    # ── Logging ────────────────────────────────────────────────────────────

    async def _send_critical_log(self, guild: discord.Guild, text: str):
        """Post a critical/error log immediately, bypassing the success buffer."""
        cfg = await self._get_config(guild.id)
        if not cfg:
            return
        log_channel_id = cfg.get('log_channel_id')
        if not log_channel_id:
            return
        channel = self.bot.get_channel(log_channel_id)
        if channel is None:
            return
        try:
            await channel.send(
                text, allowed_mentions=discord.AllowedMentions.none()
            )
        except discord.HTTPException:
            pass

    def _queue_log(
        self, guild: discord.Guild, channel: discord.TextChannel,
        member_ids: list[int], status: str, error: Optional[str] = None
    ):
        """Buffer a log entry; flush task posts a single consolidated message."""
        self._log_buffer[guild.id].append({
            'channel': channel,
            'member_ids': list(member_ids),
            'status': status,
            'error': error,
        })
        existing = self._log_flush_tasks.get(guild.id)
        if existing is None or existing.done():
            task = asyncio.create_task(self._flush_log(guild.id))
            self._log_flush_tasks[guild.id] = task
            # Same self-cleanup: drop our slot when done unless replaced.
            task.add_done_callback(
                lambda t, gid=guild.id: self._log_flush_tasks.pop(gid, None)
                if self._log_flush_tasks.get(gid) is t else None
            )

    async def _flush_log(self, guild_id: int):
        """Wait briefly, then post one consolidated log message per guild."""
        try:
            await asyncio.sleep(LOG_FLUSH_DELAY_SECONDS)

            entries = self._log_buffer.pop(guild_id, [])
            if not entries:
                return

            cfg = await self._get_config(guild_id)
            if not cfg:
                return
            log_channel_id = cfg.get('log_channel_id')
            if not log_channel_id:
                return
            log_channel = self.bot.get_channel(log_channel_id)
            if log_channel is None:
                return

            all_members: list[int] = []
            seen = set()
            for e in entries:
                for mid in e['member_ids']:
                    if mid not in seen:
                        seen.add(mid)
                        all_members.append(mid)

            successes = [e for e in entries if e['status'] == 'success']
            failures = [e for e in entries if e['status'] == 'failed']

            bot_name = self.bot.user.name if self.bot.user else "?"
            member_preview = ", ".join(f"<@{m}>" for m in all_members[:5])
            if len(all_members) > 5:
                member_preview += f" + {len(all_members) - 5} more"

            lines = [
                f"🧹 Ghost-pinged **{len(all_members)}** member(s) "
                f"via `{bot_name}` — {member_preview}"
            ]
            if successes:
                success_channels = ", ".join(e['channel'].mention for e in successes)
                lines.append(
                    f"✅ **{len(successes)} channel(s):** {success_channels}"
                )
            for f in failures:
                lines.append(f"❌ {f['channel'].mention} — {f['error']}")

            try:
                await log_channel.send(
                    "\n".join(lines),
                    allowed_mentions=discord.AllowedMentions.none()
                )
            except discord.HTTPException as e:
                logger.warning(f"[AutoJoinPing] Log send failed: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"[AutoJoinPing] Log flush error for guild {guild_id}: {e}")

    # ── Cleanup loop ───────────────────────────────────────────────────────

    async def _claim_cleanup_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await asyncio.sleep(CLAIM_CLEANUP_INTERVAL_SECONDS)
                cutoff = time.time() - CLAIM_RECORD_TTL_SECONDS
                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    result = await db.execute(
                        "DELETE FROM join_ping_claims WHERE claimed_at < ?",
                        (cutoff,)
                    )
                    await db.commit()
                    if result.rowcount > 0:
                        logger.debug(
                            f"[AutoJoinPing] Cleaned {result.rowcount} expired claim(s)"
                        )
            except asyncio.CancelledError:
                logger.info("[AutoJoinPing] Claim cleanup loop cancelled")
                break
            except Exception as e:
                logger.error(f"[AutoJoinPing] Cleanup error: {e}", exc_info=True)


async def setup(bot):
    await bot.add_cog(AutoJoinPingTask(bot))
    logger.info("✅ AutoJoinPingTask cog loaded")
