"""
Centralized Database-Driven DM Queue with Round-Robin Load Balancing
====================================================================
Single source of truth: shared SQLite database (dm_queue table).
Bots INSERT jobs. Database/Coordinator assigns to ONE bot. Worker sends.
No duplicates. Perfect round-robin when both bots have capacity.
"""

import asyncio
import aiosqlite
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

# ── Configuration constants ────────────────────────────────────────────────────
SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"
COORDINATOR_INTERVAL = 1.0
WORKER_INTERVAL = 1.0
HEARTBEAT_INTERVAL = 5.0
DM_PER_SECOND_GAP = 1.0
DM_WINDOW_LIMIT = 5
DM_WINDOW_SECONDS = 300.0
BOT_OFFLINE_THRESHOLD = 30.0
LOG_CHANNEL_ID = 1503714231566991441  # Queue manager dashboard (shared with Management Bot)
DM_SEND_LOG_CHANNEL = 1488725444357263390  # Individual DMs sent
DM_RECEIVE_LOG_CHANNEL = 1488725432726716446  # Individual DMs received

_dm_queue_instance: Optional["DMQueueCog"] = None


def _serialize_kwargs(content: Optional[str], kwargs: dict) -> str:
    """Convert discord objects to JSON for shared DB storage.

    Only content/embed/embeds survive the queue round-trip. Anything else
    (file, files, view, delete_after, allowed_mentions, ...) cannot be
    serialized to the shared DB and is dropped — loudly, so a future caller
    passing an attachment finds out from the logs instead of silently losing it.
    """
    safe = {}
    if content is not None:
        safe['content'] = content
    dropped = []
    for k, v in kwargs.items():
        if k == 'embed' and isinstance(v, discord.Embed):
            safe['embed'] = v.to_dict()
        elif k == 'embeds' and isinstance(v, list):
            safe['embeds'] = [e.to_dict() for e in v if isinstance(e, discord.Embed)]
        elif k == 'content':
            if v is not None:
                safe['content'] = v
        else:
            dropped.append(k)
    if dropped:
        logger.warning(
            f"[DMQueue] DM kwargs {dropped} are not serializable through the "
            f"shared queue and were DROPPED — the DM goes out without them."
        )
    return json.dumps(safe)


def _deserialize_kwargs(kwargs_json: str) -> Tuple[Optional[str], dict]:
    """Returns (content, kwargs) reconstructed from shared DB JSON."""
    raw = json.loads(kwargs_json or '{}')
    content = raw.pop('content', None)
    result = {}
    if 'embed' in raw:
        result['embed'] = discord.Embed.from_dict(raw['embed'])
    if 'embeds' in raw:
        result['embeds'] = [discord.Embed.from_dict(d) for d in raw['embeds']]
    return content, result


class DMQueueCog(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self._bot_id = None
        self._log_channel = None
        self._coordinator_task = None
        self._worker_task = None
        self._heartbeat_task = None
        self._recovery_task = None
        self._archive_cleanup_task = None
        # Strong refs to in-flight enqueue tasks — the event loop only keeps
        # weak refs, so an un-referenced insert task can be GC'd mid-flight
        # and silently drop a DM.
        self._insert_tasks: set = set()

    async def cog_load(self):
        """Initialize database tables on load."""
        await self._init_db()
        logger.info("[DMQueue] DB ready")

    @commands.Cog.listener()
    async def on_ready(self):
        """Resolve log channel and start background tasks when bot is ready."""
        if self._bot_id:
            return  # Already started

        self._bot_id = self.bot.user.id
        logger.info(f"[DMQueue] Bot ID: {self._bot_id}")

        # Resolve log channel
        self._log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        if self._log_channel:
            logger.info(f"[DMQueue] PRIMARY log channel resolved: {LOG_CHANNEL_ID}")
        else:
            logger.warning(f"[DMQueue] Log channel {LOG_CHANNEL_ID} not found")

        # Upsert bot into registry
        async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=30000")
            await db.execute("""
                INSERT INTO dm_bot_registry (bot_id, last_seen, last_send, sends_window)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET last_seen=excluded.last_seen
            """, (self._bot_id, time.time(), 0.0, json.dumps([])))
            await db.commit()

        # Start background tasks
        self._coordinator_task = asyncio.create_task(self._coordinator_loop())
        self._worker_task = asyncio.create_task(self._worker_loop())
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._recovery_task = asyncio.create_task(self._recovery_loop())
        self._archive_cleanup_task = asyncio.create_task(self._archive_cleanup_loop())
        logger.info("[DMQueue] Cog loaded and started")

    def cog_unload(self):
        """Cancel all background tasks on unload."""
        for task in [self._coordinator_task, self._worker_task, self._heartbeat_task, self._recovery_task, self._archive_cleanup_task]:
            if task and not task.done():
                task.cancel()
        logger.info("[DMQueue] Cog unloaded")

    def enqueue(self, user, content=None, **kwargs):
        """Called synchronously by Main.py monkey-patch. Returns immediately.

        Pop reserved kwargs (batch_id, _source) before serializing the rest as
        DM kwargs. `_source` is used by the sticky-note system to tag DMs from
        reply_dm_duty (preserve note) vs everything else (wipe note on send).
        """
        batch_id = kwargs.pop('batch_id', None)
        source = kwargs.pop('_source', None)
        task = asyncio.create_task(self._insert_job(user, content, kwargs, batch_id, source))
        self._insert_tasks.add(task)
        task.add_done_callback(self._insert_tasks.discard)

    async def _insert_job(self, user, content, kwargs, batch_id=None, source=None):
        """Insert DM job into shared database."""
        try:
            # Startup window: enqueue() can fire after the cog loads but before
            # on_ready sets _bot_id. source_bot_id is NOT NULL, so inserting
            # None would IntegrityError and silently drop the DM. Use a LOCAL
            # resolved id — do NOT set self._bot_id here, on_ready uses it as
            # its "already started" guard for the background loops.
            bot_id = self._bot_id
            if bot_id is None:
                await self.bot.wait_until_ready()
                bot_id = self.bot.user.id

            now = time.time()
            kwargs_json = _serialize_kwargs(content, kwargs)
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                await db.execute("""
                    INSERT INTO dm_queue
                    (source_bot_id, user_id, content, kwargs_json, status, created_at, batch_id, source)
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """, (bot_id, user.id, content, kwargs_json, now, batch_id, source))
                await db.commit()
            logger.info(f"[DMQueue] ENQUEUED: user_id={user.id} source_bot={bot_id} batch={batch_id} source={source}")

            # Wave-Logging dashboard event (dm_queue tab)
            try:
                from utils.global_logger import log_event as _wl_event
                await _wl_event(
                    category="dm_queue",
                    action="dm_enqueued",
                    target=user,
                    details={
                        "source_bot_id": str(self._bot_id) if self._bot_id else None,
                        "batch_id": batch_id,
                        "source": source,
                        "content_preview": (content or "")[:200] if content else None,
                    },
                )
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[DMQueue] Error inserting job: {e}", exc_info=True)

    # ── Background tasks ───────────────────────────────────────────────────────

    async def _coordinator_loop(self):
        """Assign pending DMs to capable bots (round-robin by last_send)."""
        while True:
            try:
                await asyncio.sleep(COORDINATOR_INTERVAL)
                now = time.time()

                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    db.row_factory = aiosqlite.Row

                    # Load online bots and their capacity
                    async with db.execute("""
                        SELECT bot_id, last_send, sends_window
                        FROM dm_bot_registry
                        WHERE last_seen > ?
                        ORDER BY last_send ASC
                    """, (now - BOT_OFFLINE_THRESHOLD,)) as cursor:
                        bots = await cursor.fetchall()

                    logger.debug(f"[DMQueue:COORD] Found {len(bots)} online bot(s)")

                    capable_bots = []
                    for bot_row in bots:
                        bot_id = bot_row['bot_id']
                        last_send = float(bot_row['last_send']) if bot_row['last_send'] is not None else 0.0
                        sends_window = json.loads(bot_row['sends_window'])

                        # Evict old timestamps
                        sends_window = [t for t in sends_window if t > now - DM_WINDOW_SECONDS]

                        # Check capacity
                        gap_ok = (now - last_send) >= DM_PER_SECOND_GAP
                        window_ok = len(sends_window) < DM_WINDOW_LIMIT

                        logger.debug(f"[DMQueue:COORD] Bot {bot_id}: gap_ok={gap_ok} window_ok={window_ok} sends_in_window={len(sends_window)}")

                        if gap_ok and window_ok:
                            capable_bots.append(bot_id)

                    logger.debug(f"[DMQueue:COORD] Capable bots: {capable_bots}")

                    if not capable_bots:
                        logger.debug(f"[DMQueue:COORD] No capable bots, skipping assignment")
                        continue

                    # Load pending DMs
                    async with db.execute("""
                        SELECT id, user_id, source_bot_id, source FROM dm_queue
                        WHERE status='pending'
                        ORDER BY created_at ASC
                        LIMIT 10
                    """) as cursor:
                        pending = await cursor.fetchall()

                    logger.debug(f"[DMQueue:COORD] Found {len(pending)} pending job(s)")

                    # Assign round-robin via CAS UPDATE
                    assigned_count = 0
                    for i, row in enumerate(pending):
                        # Capture all columns up-front — row_factory state can drift
                        # after a write happens on the same connection mid-loop.
                        job_id = row['id']
                        user_id = row['user_id']
                        source_bot_id = row['source_bot_id']
                        source = row['source']

                        # Auto-reply DMs must stay with the source bot that armed the sticky note
                        # Otherwise the wrong bot sends the auto-reply (consistency issue)
                        if source == 'auto_reply':
                            # Prefer source bot for sticky-note consistency
                            if source_bot_id in capable_bots:
                                bot_id = source_bot_id
                                logger.debug(f"[DMQueue:COORD] Auto-reply: forcing bot {bot_id} (source bot)")
                            elif (now - row['created_at']) > 300:
                                # Source bot offline >5min — fall back to prevent permanent loss
                                bot_id = capable_bots[i % len(capable_bots)]
                                logger.warning(f"[DMQueue:COORD] Auto-reply job {job_id}: source bot {source_bot_id} "
                                               f"offline >5m, falling back to bot {bot_id}")
                            else:
                                logger.debug(f"[DMQueue:COORD] Auto-reply job {job_id}: source bot {source_bot_id} not capable, deferring")
                                continue  # Still within grace period
                        else:
                            # Standard round-robin for other DM types
                            bot_id = capable_bots[i % len(capable_bots)]

                        result = await db.execute("""
                            UPDATE dm_queue
                            SET status='assigned', assigned_bot_id=?, assigned_at=?
                            WHERE id=? AND status='pending'
                        """, (bot_id, now, job_id))

                        if result.rowcount > 0:
                            logger.info(f"[DMQueue:COORD] ASSIGNED: job_id={job_id} -> bot_id={bot_id}")
                            assigned_count += 1
                            asyncio.create_task(self._log_event_assigned(job_id, user_id, bot_id))

                    if assigned_count > 0:
                        await db.commit()
                        logger.info(f"[DMQueue:COORD] Assigned {assigned_count} job(s) this cycle")

            except asyncio.CancelledError:
                logger.info("[DMQueue] Coordinator loop cancelled")
                break
            except Exception as e:
                logger.error(f"[DMQueue:COORD] ERROR: {e}", exc_info=True)

    async def _recovery_loop(self):
        """Recover stuck 'sending' jobs if assigned bot goes offline."""
        MAX_RETRIES = 3
        SENDING_TIMEOUT = 60.0  # Seconds

        while True:
            try:
                await asyncio.sleep(10.0)  # Check every 10 seconds
                now = time.time()

                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    db.row_factory = aiosqlite.Row

                    # Find stuck 'sending' OR long-stuck 'assigned' jobs
                    async with db.execute("""
                        SELECT id, assigned_bot_id, assigned_at, attempt_count, status
                        FROM dm_queue
                        WHERE status='sending'
                           OR (status='assigned' AND assigned_at < ?)
                    """, (now - SENDING_TIMEOUT,)) as cursor:
                        stuck_jobs = await cursor.fetchall()

                    for job in stuck_jobs:
                        job_id = job['id']
                        assigned_at = float(job['assigned_at']) if job['assigned_at'] else now
                        attempt_count = job['attempt_count']
                        sending_duration = now - assigned_at

                        # Recover regardless of whether bot is online — a hung send won't go offline
                        if sending_duration > SENDING_TIMEOUT:
                            if attempt_count < MAX_RETRIES:
                                await db.execute("""
                                    UPDATE dm_queue
                                    SET status='pending', attempt_count=attempt_count+1,
                                        assigned_bot_id=NULL, error_msg=NULL
                                    WHERE id=?
                                """, (job_id,))
                                logger.warning(f"[DMQueue:RECOVERY] RECOVERED: job_id={job_id} "
                                             f"(stuck >{SENDING_TIMEOUT}s, retry {attempt_count + 1}/{MAX_RETRIES})")
                            else:
                                logger.error(f"[DMQueue:RECOVERY] ARCHIVING: job_id={job_id} (max retries exceeded)")
                                async with db.execute("SELECT * FROM dm_queue WHERE id=?", (job_id,)) as cursor:
                                    row = await cursor.fetchone()
                                    if row:
                                        now_arch = time.time()
                                        await db.execute("""
                                            INSERT INTO dm_failed_archive
                                            (original_id, user_id, source_bot_id, assigned_bot_id, content, fail_reason,
                                             error_msg, attempt_count, created_at, failed_at, archived_at, batch_id)
                                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                        """, (
                                            job_id, row['user_id'], row['source_bot_id'], row['assigned_bot_id'],
                                            row['content'], "Max retries", f"Max retries ({MAX_RETRIES}) exceeded",
                                            attempt_count, row['created_at'], now_arch, now_arch, row['batch_id']
                                        ))
                                        await db.execute("DELETE FROM dm_queue WHERE id=?", (job_id,))

                    if stuck_jobs:
                        await db.commit()

                    # Retry transient 'failed' jobs — NEVER retry permanent user-side failures
                    async with db.execute("""
                        SELECT id, attempt_count FROM dm_queue
                        WHERE status='failed'
                          AND (failed_at IS NULL OR failed_at < ?)
                          AND error_category IS NOT 'user_error'
                          AND (error_msg IS NULL
                               OR (error_msg NOT LIKE '%Forbidden%'
                                   AND error_msg NOT LIKE '%NotFound%'))
                    """, (now - 30.0,)) as cursor:
                        failed_jobs = await cursor.fetchall()

                    for job in failed_jobs:
                        job_id = job['id']
                        attempt_count = job['attempt_count']
                        if attempt_count < MAX_RETRIES:
                            await db.execute("""
                                UPDATE dm_queue
                                SET status='pending', assigned_bot_id=NULL, error_msg=NULL
                                WHERE id=? AND status='failed'
                            """, (job_id,))
                            logger.warning(f"[DMQueue:RECOVERY] RETRYING failed job_id={job_id} "
                                           f"(attempt {attempt_count + 1}/{MAX_RETRIES})")
                        else:
                            async with db.execute("SELECT * FROM dm_queue WHERE id=?", (job_id,)) as cursor:
                                row = await cursor.fetchone()
                            if row:
                                now_arch = time.time()
                                await db.execute("""
                                    INSERT INTO dm_failed_archive
                                    (original_id, user_id, source_bot_id, assigned_bot_id, content, fail_reason,
                                     error_msg, attempt_count, created_at, failed_at, archived_at, batch_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """, (
                                    job_id, row['user_id'], row['source_bot_id'], row['assigned_bot_id'],
                                    row['content'], "Max retries (transient)",
                                    row['error_msg'], attempt_count,
                                    row['created_at'], now_arch, now_arch, row['batch_id']
                                ))
                                await db.execute("DELETE FROM dm_queue WHERE id=?", (job_id,))
                                logger.error(f"[DMQueue:RECOVERY] ARCHIVED failed job_id={job_id} (max retries exceeded)")

                    if failed_jobs:
                        await db.commit()

            except asyncio.CancelledError:
                logger.info("[DMQueue] Recovery loop cancelled")
                break
            except Exception as e:
                logger.error(f"[DMQueue:RECOVERY] ERROR: {e}", exc_info=True)

    async def _archive_cleanup_loop(self):
        """Move successfully sent DMs older than 24h to archive."""
        ARCHIVE_AFTER_SECONDS = 86400  # 24 hours

        while True:
            try:
                await asyncio.sleep(600.0)  # Check every 10 minutes
                now = time.time()
                cutoff = now - ARCHIVE_AFTER_SECONDS

                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    db.row_factory = aiosqlite.Row

                    # Find all sent DMs older than 24h
                    async with db.execute("""
                        SELECT id, user_id, source_bot_id, assigned_bot_id, content, kwargs_json,
                               attempt_count, created_at, sent_at, batch_id, source
                        FROM dm_queue
                        WHERE status='sent' AND sent_at < ?
                    """, (cutoff,)) as cursor:
                        old_sent = await cursor.fetchall()

                    # Move to archive (include kwargs_json + source for log viewer)
                    for row in old_sent:
                        await db.execute("""
                            INSERT INTO dm_sent_archive
                            (original_id, user_id, source_bot_id, assigned_bot_id, content, kwargs_json,
                             attempt_count, created_at, sent_at, archived_at, batch_id, source)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            row['id'], row['user_id'], row['source_bot_id'], row['assigned_bot_id'],
                            row['content'], row['kwargs_json'] if 'kwargs_json' in row.keys() else '{}',
                            row['attempt_count'], row['created_at'], row['sent_at'], now,
                            row['batch_id'] if 'batch_id' in row.keys() else None,
                            row['source'] if 'source' in row.keys() else None,
                        ))
                        await db.execute("DELETE FROM dm_queue WHERE id=?", (row['id'],))

                    if old_sent:
                        await db.commit()
                        logger.info(f"[DMQueue:ARCHIVE] Archived {len(old_sent)} sent DMs to dm_sent_archive")

                    # Prune archive entries older than 30 days
                    archive_cutoff = now - (30 * 86400)
                    async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db2:
                        await db2.execute("PRAGMA journal_mode=WAL")
                        await db2.execute("PRAGMA busy_timeout=30000")
                        result_sent = await db2.execute(
                            "DELETE FROM dm_sent_archive WHERE archived_at < ?", (archive_cutoff,)
                        )
                        result_fail = await db2.execute(
                            "DELETE FROM dm_failed_archive WHERE archived_at < ?", (archive_cutoff,)
                        )
                        await db2.commit()
                        pruned = (result_sent.rowcount or 0) + (result_fail.rowcount or 0)
                        if pruned:
                            logger.info(f"[DMQueue:ARCHIVE] Pruned {pruned} archive entries older than 30 days")

            except asyncio.CancelledError:
                logger.info("[DMQueue] Archive cleanup loop cancelled")
                break
            except Exception as e:
                logger.error(f"[DMQueue:ARCHIVE] ERROR: {e}", exc_info=True)

    async def _worker_loop(self):
        """Claim assigned DMs and send them."""
        while True:
            try:
                await asyncio.sleep(WORKER_INTERVAL)

                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    db.row_factory = aiosqlite.Row

                    # Claim one assigned job
                    async with db.execute("""
                        SELECT id, user_id, content, kwargs_json, source
                        FROM dm_queue
                        WHERE assigned_bot_id=? AND status='assigned'
                        LIMIT 1
                    """, (self._bot_id,)) as cursor:
                        row = await cursor.fetchone()

                    if not row:
                        logger.debug(f"[DMQueue:WORKER] No assigned jobs for bot {self._bot_id}")
                        continue

                    job_id = row['id']
                    user_id = row['user_id']
                    logger.debug(f"[DMQueue:WORKER] Found assigned job: id={job_id} user={user_id}")

                    # Claim it
                    result = await db.execute("""
                        UPDATE dm_queue
                        SET status='sending'
                        WHERE id=? AND status='assigned'
                    """, (job_id,))

                    if result.rowcount == 0:
                        logger.warning(f"[DMQueue:WORKER] Failed to claim job {job_id} (race condition)")
                        continue  # Another bot claimed it

                    await db.commit()
                    logger.info(f"[DMQueue:WORKER] CLAIMED: job_id={job_id} user_id={user_id}")

                    # Send the DM
                    await self._send_job(row)

            except asyncio.CancelledError:
                logger.info("[DMQueue] Worker loop cancelled")
                break
            except Exception as e:
                logger.error(f"[DMQueue:WORKER] ERROR: {e}", exc_info=True)

    async def _send_job(self, row):
        """Send a single DM job."""
        import Main as _main
        from Tasks.reply_dm_note import wipe_note

        job_id = row['id']
        user_id = row['user_id']
        content = row['content']
        kwargs_json = row['kwargs_json']
        source = row['source'] if 'source' in row.keys() else None

        logger.debug(f"[DMQueue:SEND] Starting send for job_id={job_id} user_id={user_id} source={source}")

        try:
            user = await self.bot.fetch_user(user_id)
            content, kwargs = _deserialize_kwargs(kwargs_json)
            logger.debug(f"[DMQueue:SEND] Fetched user {user_id}: {user}")

            original_send = (
                _main._original_member_send
                if isinstance(user, discord.Member)
                else _main._original_user_send
            )

            msg = await original_send(user, content, **kwargs)
            logger.debug(f"[DMQueue:SEND] Message sent to {user_id}, msg={msg}")

            # Sticky-note wipe: only reply_dm_duty DMs preserve the note
            # (that send IS the arming). Everything else — random welcome DMs,
            # queue-complete DMs, AND successful auto_reply sends — wipes it.
            # Failed sends raise above and never reach this line, keeping the
            # note armed for a future retry.
            if source != 'reply_dm_duty':
                await wipe_note(user_id)

            # Update registry
            now = time.time()
            await self._update_registry_sent(now)
            logger.debug(f"[DMQueue:SEND] Updated registry with send time")

            # Mark as sent
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                await db.execute("""
                    UPDATE dm_queue
                    SET status='sent', sent_at=?
                    WHERE id=?
                """, (now, job_id))
                await db.commit()

            logger.info(f"[DMQueue:SEND] SUCCESS: job_id={job_id} user_id={user_id}")

            # Log events
            asyncio.create_task(self._log_event_sent(job_id, user_id))
            await self._log_sent(user, content, kwargs)

        except discord.Forbidden:
            logger.warning(f"[DMQueue:SEND] FORBIDDEN: job_id={job_id} user_id={user_id} (DMs disabled)")
            await self._mark_failed(job_id, "Forbidden: DMs disabled or blocked by user", permanent=True)
            asyncio.create_task(self._log_event_failed(job_id, user_id, "DMs disabled"))
        except discord.NotFound:
            logger.error(f"[DMQueue:SEND] NOT_FOUND: job_id={job_id} user_id={user_id} (user deleted or doesn't exist)")
            await self._mark_failed(job_id, "NotFound: User doesn't exist or was deleted", permanent=True)
            asyncio.create_task(self._log_event_failed(job_id, user_id, "User not found"))
        # NOTE: discord.InvalidArgument was removed in discord.py 2.x — naming it
        # in an except clause raises AttributeError and masks the real error.
        # Bad-argument failures now fall through to the generic handler below
        # (transient → retried up to MAX_RETRIES, then archived).
        except Exception as e:
            logger.error(f"[DMQueue:SEND] FAILED: job_id={job_id} user_id={user_id} error={e}", exc_info=True)
            await self._mark_failed(job_id, str(e), permanent=False)
            asyncio.create_task(self._log_event_failed(job_id, user_id, str(e)))

    async def _update_registry_sent(self, send_time: float):
        """Update last_send and sends_window in registry."""
        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                db.row_factory = aiosqlite.Row

                # Fetch current window
                async with db.execute("""
                    SELECT sends_window FROM dm_bot_registry
                    WHERE bot_id=?
                """, (self._bot_id,)) as cursor:
                    row = await cursor.fetchone()

                if row:
                    sends_window = json.loads(row['sends_window'])
                else:
                    sends_window = []

                # Evict old timestamps
                sends_window = [t for t in sends_window if t > send_time - DM_WINDOW_SECONDS]
                sends_window.append(send_time)

                await db.execute("""
                    INSERT INTO dm_bot_registry (bot_id, last_send, sends_window)
                    VALUES (?, ?, ?)
                    ON CONFLICT(bot_id) DO UPDATE SET
                        last_send=excluded.last_send,
                        sends_window=excluded.sends_window
                """, (self._bot_id, send_time, json.dumps(sends_window)))

                await db.commit()
        except Exception as e:
            logger.error(f"[DMQueue] Error updating registry: {e}", exc_info=True)

    async def _heartbeat_loop(self):
        """Update last_seen every 5 seconds to indicate bot is alive."""
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                now = time.time()

                async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA busy_timeout=30000")
                    await db.execute("""
                        INSERT INTO dm_bot_registry (bot_id, last_seen)
                        VALUES (?, ?)
                        ON CONFLICT(bot_id) DO UPDATE SET last_seen=excluded.last_seen
                    """, (self._bot_id, now))
                    await db.commit()

                logger.debug(f"[DMQueue:HB] Heartbeat sent for bot {self._bot_id}")

            except asyncio.CancelledError:
                logger.info("[DMQueue] Heartbeat loop cancelled")
                break
            except Exception as e:
                logger.error(f"[DMQueue:HB] ERROR: {e}", exc_info=True)

    # ── Database initialization ────────────────────────────────────────────────

    async def _init_db(self):
        """Create tables if they don't exist, then run migrations, then create indexes."""
        try:
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")

                # ── Phase 1: Create tables (with batch_id for fresh installs) ──
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS dm_queue (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_bot_id   INTEGER NOT NULL,
                        user_id         INTEGER NOT NULL,
                        content         TEXT,
                        kwargs_json     TEXT DEFAULT '{}',
                        status          TEXT DEFAULT 'pending',
                        assigned_bot_id INTEGER,
                        attempt_count   INTEGER DEFAULT 0,
                        created_at      REAL NOT NULL,
                        assigned_at     REAL,
                        sent_at         REAL,
                        error_msg       TEXT,
                        batch_id        TEXT
                    )
                """)

                await db.execute("""
                    CREATE TABLE IF NOT EXISTS dm_bot_registry (
                        bot_id       INTEGER PRIMARY KEY,
                        last_seen    REAL DEFAULT 0,
                        last_send    REAL DEFAULT 0,
                        sends_window TEXT DEFAULT '[]'
                    )
                """)

                await db.execute("""
                    CREATE TABLE IF NOT EXISTS dm_sent_archive (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_id     INTEGER NOT NULL,
                        user_id         INTEGER NOT NULL,
                        source_bot_id   INTEGER,
                        assigned_bot_id INTEGER,
                        content         TEXT,
                        attempt_count   INTEGER DEFAULT 0,
                        created_at      REAL,
                        sent_at         REAL,
                        archived_at     REAL NOT NULL,
                        batch_id        TEXT
                    )
                """)

                await db.execute("""
                    CREATE TABLE IF NOT EXISTS dm_failed_archive (
                        id              INTEGER PRIMARY KEY AUTOINCREMENT,
                        original_id     INTEGER NOT NULL,
                        user_id         INTEGER NOT NULL,
                        source_bot_id   INTEGER,
                        assigned_bot_id INTEGER,
                        content         TEXT,
                        fail_reason     TEXT NOT NULL,
                        error_msg       TEXT,
                        attempt_count   INTEGER DEFAULT 0,
                        created_at      REAL,
                        failed_at       REAL,
                        archived_at     REAL NOT NULL,
                        batch_id        TEXT
                    )
                """)

                # ── Reply-DM Sticky Note ──────────────────────────────────
                # Armed by reply_dm_duty (Management bot) after each staff-reply
                # forward DM. Wiped by any other outbound bot DM (this is also
                # done by Logistics — see Tasks/dm_handling.py monkey-patch).
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS reply_dm_note (
                        user_id        INTEGER PRIMARY KEY,
                        guild_id       INTEGER NOT NULL,
                        source_bot_id  INTEGER,
                        armed_at       REAL NOT NULL
                    )
                """)

                # Drop the legacy auto_reply_queue table (UNIQUE(user_id) made
                # it incompatible with re-armable per-guild notes).
                await db.execute("DROP TABLE IF EXISTS auto_reply_queue")

                # ── Phase 2: Migrations (add missing columns to existing tables) ──
                # These MUST run before index creation so the columns exist.
                try:
                    await db.execute("ALTER TABLE dm_queue ADD COLUMN failed_at REAL")
                except Exception as e:
                    logger.debug(f"[DMQueue] Migration add column 'failed_at' failed (likely already exists): {e}")

                try:
                    await db.execute("ALTER TABLE dm_queue ADD COLUMN source TEXT")
                except Exception as e:
                    logger.debug(f"[DMQueue] Migration add column 'source' failed (likely already exists): {e}")

                for tbl in ("dm_queue", "dm_sent_archive", "dm_failed_archive"):
                    try:
                        await db.execute(f"ALTER TABLE {tbl} ADD COLUMN batch_id TEXT")
                    except Exception as e:
                        logger.debug(f"[DMQueue] Migration add column 'batch_id' to {tbl} failed (likely already exists): {e}")

                for tbl in ("dm_queue", "dm_failed_archive"):
                    try:
                        await db.execute(f"ALTER TABLE {tbl} ADD COLUMN error_category TEXT")
                    except Exception:
                        pass

                for tbl in ("dm_sent_archive", "dm_failed_archive"):
                    try:
                        await db.execute(f"ALTER TABLE {tbl} ADD COLUMN kwargs_json TEXT DEFAULT '{{}}'")
                    except Exception:
                        pass
                    try:
                        await db.execute(f"ALTER TABLE {tbl} ADD COLUMN source TEXT")
                    except Exception:
                        pass

                try:
                    await db.execute("""
                        CREATE INDEX IF NOT EXISTS idx_dm_queue_status_created
                        ON dm_queue(status, created_at)
                    """)
                except Exception:
                    pass

                # ── Phase 3: Create indexes (columns now guaranteed to exist) ──
                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reply_dm_note_armed_at
                    ON reply_dm_note(armed_at)
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dm_queue_status
                    ON dm_queue(status)
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dm_queue_batch_id
                    ON dm_queue(batch_id)
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dm_sent_archive_batch_id
                    ON dm_sent_archive(batch_id)
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dm_failed_archive_reason
                    ON dm_failed_archive(fail_reason)
                """)

                await db.execute("""
                    CREATE INDEX IF NOT EXISTS idx_dm_failed_archive_batch_id
                    ON dm_failed_archive(batch_id)
                """)

                await db.commit()

                # ── Phase 4: Log schema for debugging ──
                try:
                    async with db.execute("PRAGMA table_info(dm_queue)") as cur:
                        cols = await cur.fetchall()
                        # PRAGMA returns plain tuples: (cid, name, type, notnull, dflt_value, pk)
                        col_names = [c[1] for c in cols]
                        logger.info(f"[DMQueue] dm_queue columns: {col_names}")
                except Exception as e:
                    logger.error(f"[DMQueue] Failed to fetch dm_queue schema: {e}", exc_info=True)
                logger.info("[DMQueue] Database initialized")
        except Exception as e:
            logger.error(f"[DMQueue] DB init error: {e}", exc_info=True)

    async def _mark_failed(self, job_id: int, error_msg: str, permanent: bool = False, error_category: str = 'other'):
        """Mark a job as failed. If permanent, move to archive and remove from queue."""
        try:
            now = time.time()
            async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
                await db.execute("PRAGMA journal_mode=WAL")
                await db.execute("PRAGMA busy_timeout=30000")
                db.row_factory = aiosqlite.Row

                if permanent:
                    async with db.execute("SELECT * FROM dm_queue WHERE id=?", (job_id,)) as cursor:
                        row = await cursor.fetchone()

                    if row:
                        fail_reason = "Unknown"
                        if "Forbidden" in error_msg:
                            fail_reason = "DMs disabled"
                        elif "NotFound" in error_msg:
                            fail_reason = "User not found"
                        elif "InvalidArgument" in error_msg:
                            fail_reason = "Invalid"
                        elif "Max retries" in error_msg:
                            fail_reason = "Max retries"

                        await db.execute("""
                            INSERT INTO dm_failed_archive
                            (original_id, user_id, source_bot_id, assigned_bot_id, content, kwargs_json,
                             fail_reason, error_msg, error_category, attempt_count, created_at,
                             failed_at, archived_at, batch_id, source)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            job_id, row['user_id'], row['source_bot_id'], row['assigned_bot_id'],
                            row['content'], row['kwargs_json'] if 'kwargs_json' in row.keys() else '{}',
                            fail_reason, error_msg, error_category, row['attempt_count'],
                            row['created_at'], now, now,
                            row['batch_id'] if 'batch_id' in row.keys() else None,
                            row['source'] if 'source' in row.keys() else None,
                        ))
                        await db.execute("DELETE FROM dm_queue WHERE id=?", (job_id,))
                        logger.info(f"[DMQueue] ARCHIVED: job_id={job_id} reason={fail_reason}")
                else:
                    await db.execute("""
                        UPDATE dm_queue
                        SET status='failed', error_msg=?, error_category=?, failed_at=?
                        WHERE id=?
                    """, (error_msg, error_category, now, job_id))

                await db.commit()
        except Exception as e:
            logger.error(f"[DMQueue] Error marking failed: {e}", exc_info=True)

    async def _log_sent(self, user, content: Optional[str], kwargs: dict):
        """Log sent DM to the DM send log channel (not queue dashboard)."""
        try:
            channel = self.bot.get_channel(DM_SEND_LOG_CHANNEL)
            if not channel:
                logger.debug(f"[DMQueue:SEND] Channel {DM_SEND_LOG_CHANNEL} not cached, fetching...")
                try:
                    channel = await self.bot.fetch_channel(DM_SEND_LOG_CHANNEL)
                    logger.debug(f"[DMQueue:SEND] Successfully fetched channel {DM_SEND_LOG_CHANNEL}")
                except Exception as fetch_err:
                    logger.error(f"[DMQueue:SEND] Failed to fetch DM send log channel {DM_SEND_LOG_CHANNEL}: {fetch_err}")
                    return

            embed = discord.Embed(
                title="📬 DM Sent",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(
                name="To",
                value=f"{user.mention} ({user})\nID: `{user.id}`",
                inline=False
            )

            # Plain text
            if content:
                text = content if len(content) <= 1024 else content[:1021] + "..."
                embed.add_field(name="Content", value=text, inline=False)

            # Single embed
            sent_embed = kwargs.get('embed')
            if sent_embed:
                parts = []
                if sent_embed.title:
                    parts.append(f"**Title:** {sent_embed.title}")
                if sent_embed.description:
                    parts.append(f"**Desc:** {sent_embed.description[:200]}")
                if parts:
                    embed.add_field(name="Embed", value="\n".join(parts), inline=False)

            # Multiple embeds
            for i, e in enumerate(kwargs.get('embeds', []), 1):
                parts = []
                if e.title:
                    parts.append(f"**Title:** {e.title}")
                if e.description:
                    parts.append(f"**Desc:** {e.description[:200]}")
                if parts:
                    embed.add_field(name=f"Embed {i}", value="\n".join(parts), inline=False)

            # Files
            all_files = ([kwargs['file']] if 'file' in kwargs else []) + list(kwargs.get('files', []))
            if all_files:
                file_text = "\n".join(f"📎 {f.filename}" for f in all_files if hasattr(f, 'filename'))
                embed.add_field(name="Files", value=file_text[:1024], inline=False)

            if hasattr(user, 'avatar') and user.avatar:
                embed.set_thumbnail(url=user.avatar.url)

            await channel.send(embed=embed)
            logger.info(f"[DMQueue:SEND] Logged DM to user {user.id} in send log channel {DM_SEND_LOG_CHANNEL}")
        except Exception as e:
            logger.error(f"[DMQueue:SEND] Error logging DM: {e}", exc_info=True)

    async def _log_event(self, title: str, description: str, color=discord.Color.greyple()):
        """Log an event to the dashboard channel."""
        if not self._log_channel:
            try:
                self._log_channel = await self.bot.fetch_channel(LOG_CHANNEL_ID)
            except Exception:
                return

        try:
            embed = discord.Embed(
                title=title,
                description=description,
                color=color,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text=f"Bot {self._bot_id}")
            await self._log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"[DMQueue:EVENT] Error logging event: {e}")

    async def _log_event_assigned(self, job_id: int, user_id: int, assigned_bot_id: int):
        """Log when a DM is assigned."""
        await self._log_event(
            title="📌 DM Assigned",
            description=f"Job {job_id} → User {user_id} assigned to Bot {assigned_bot_id}",
            color=discord.Color.blue()
        )

    async def _log_event_sent(self, job_id: int, user_id: int):
        """Log when a DM is sent successfully."""
        await self._log_event(
            title="✅ DM Sent",
            description=f"Job {job_id} → User {user_id}",
            color=discord.Color.green()
        )

    async def _log_event_failed(self, job_id: int, user_id: int, error: str):
        """Log when a DM fails."""
        await self._log_event(
            title="❌ DM Failed",
            description=f"Job {job_id} → User {user_id}\nError: {error[:200]}",
            color=discord.Color.red()
        )

    async def _log_event_rate_limited(self, bot_id: int):
        """Log when a bot hits rate limit."""
        await self._log_event(
            title="⏱️ Rate Limited",
            description=f"Bot {bot_id} at capacity, queuing DMs",
            color=discord.Color.orange()
        )


async def setup(bot):
    global _dm_queue_instance
    cog = DMQueueCog(bot)
    _dm_queue_instance = cog
    await bot.add_cog(cog)
    logger.info("[DMQueue] Cog registered")
