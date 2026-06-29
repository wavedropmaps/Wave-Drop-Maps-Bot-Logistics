"""
Tasks/loot_bridge.py — Loot-route cross-bot dispatch (Wave Logistics → Wave Management).

When a loot_route request is in the queue (guild 971731167621574666) and hasn't been
dispatched yet, this sweep posts the map into the Management bot's maps-not-taken channel
(1205406903463710750) formatted identically to a normal member submission:

    Game Mode: <mode>            (only when the queue entry has one)
    Description: <description>
    <image attachment>

The queue linkage (code + customer priority) is hidden in the attachment FILENAME
(`loot-q<code>-p<n>-<original>.png`) so staff never see raw marker text. Only the
URL-only fallback (image couldn't be downloaded) still carries a marker line, rendered
as Discord subtext: `-# [loot-bridge] queue:<code> priority:<n>`.

The Management bot's on_loot_route_message watcher (loot_routes.py) extracts the queue
code from the filename and stores it with the assignment, enabling `-z removequeue <code>`
on completion. `dispatched_at` is stamped so an entry is never double-posted.

Architecture note: this replaces the indirect path where queue display embeds leaked into
the maps-not-taken channel via the MapRequestForwarder. The MapRequestForwarder now
filters out loot route queue display embeds (title contains "LOOT ROUTE REQUEST").
"""

import io
import logging
import re

import aiohttp
import discord
from discord.ext import commands, tasks

import Database.database_improved as database

try:
    from utils.queue_priority import calculate_request_priority
except Exception:  # pragma: no cover
    calculate_request_priority = None

logger = logging.getLogger("discord")

LOOT_QUEUE_GUILD_ID = 971731167621574666        # where the loot queue lives
LOOT_MAPS_CHANNEL_ID = 1205406903463710750      # Management staff-hub maps-not-taken channel


class LootBridgeCog(commands.Cog):
    """Dispatches undispatched loot queue entries to the Management maps-not-taken channel."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        # One-time cleanup: mark all old undispatched loot routes as dispatched
        # to prevent backlog flood on startup. Only runs once per bot session.
        await self._mark_old_backlog_as_dispatched()
        if not self.loot_dispatch_sweep.is_running():
            self.loot_dispatch_sweep.start()

    async def _mark_old_backlog_as_dispatched(self):
        """Mark all existing undispatched loot routes as dispatched to prevent backlog flood."""
        try:
            # Get ALL undispatched loot routes (no time filter for this cleanup)
            db = await database.get_db()
            async with db.execute('''
                SELECT queue_number FROM map_requests
                WHERE guild_id = ? AND route_type = 'loot_route'
                AND (dispatched_at IS NULL OR dispatched_at = '')
            ''', (LOOT_QUEUE_GUILD_ID,)) as cursor:
                rows = await cursor.fetchall()

            if not rows:
                return

            backfill_count = len(rows)
            logger.warning(f"[loot_bridge] BACKLOG CLEANUP: Found {backfill_count} old undispatched loot routes")

            # Mark them all as dispatched with a sentinel timestamp
            await db.execute('''
                UPDATE map_requests
                SET dispatched_at = '2025-01-01T00:00:00+00:00'
                WHERE guild_id = ? AND route_type = 'loot_route'
                AND (dispatched_at IS NULL OR dispatched_at = '')
            ''', (LOOT_QUEUE_GUILD_ID,))
            await db.commit()

            logger.warning(f"[loot_bridge] BACKLOG CLEANUP: Marked {backfill_count} old routes as dispatched")
            logger.info("[loot_bridge] New loot routes will dispatch normally with 1-hour window")
        except Exception as e:
            logger.error(f"[loot_bridge] Backlog cleanup failed: {e}")

    def cog_unload(self):
        if self.loot_dispatch_sweep.is_running():
            self.loot_dispatch_sweep.cancel()

    async def _download(self, url: str, filename: str = None):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        name = filename or url.split('?')[0].split('/')[-1] or "loot_map.png"
                        return discord.File(io.BytesIO(await resp.read()), filename=name)
        except Exception as e:
            logger.warning(f"[loot_bridge] image download failed ({url}): {e}")
        return None

    @tasks.loop(seconds=60)
    async def loot_dispatch_sweep(self):
        try:
            # Only get loot requests from the last 1 hour, max 5 per sweep
            reqs = await database.get_undispatched_loot_requests(LOOT_QUEUE_GUILD_ID, hours_lookback=1)
            if not reqs:
                return
            # Limit batch size to prevent flooding on startup/backlog
            MAX_DISPATCH_PER_SWEEP = 5
            if len(reqs) > MAX_DISPATCH_PER_SWEEP:
                logger.warning(f"[loot_bridge] backlog detected: {len(reqs)} items, dispatching max {MAX_DISPATCH_PER_SWEEP}")
                reqs = reqs[:MAX_DISPATCH_PER_SWEEP]
            channel = self.bot.get_channel(LOOT_MAPS_CHANNEL_ID)
            if not channel:
                logger.warning("[loot_bridge] maps-not-taken channel not reachable — is the bot in the Management guild?")
                return
            src_guild = self.bot.get_guild(LOOT_QUEUE_GUILD_ID)

            for req in reqs:
                code = req['queue_number']
                try:
                    prio = 999
                    if calculate_request_priority and src_guild and req.get('user_ids'):
                        try:
                            lvl, _, _ = await calculate_request_priority(src_guild, 'loot_route', req['user_ids'])
                            if lvl:
                                prio = lvl
                        except Exception:
                            pass

                    desc = (req.get('description') or "").strip()
                    game_mode = (req.get('map_type') or "").strip()
                    lines = []
                    if game_mode:
                        lines.append(f"Game Mode: {game_mode}")
                    if desc:
                        lines.append(f"Description: {desc}")
                    if not lines:
                        # The watcher ignores bot posts without text — always say something.
                        lines.append("Loot route request")
                    content = "\n".join(lines)

                    file = None
                    img = req.get('image_url')
                    if img and str(img).startswith('http'):
                        orig_name = img.split('?')[0].split('/')[-1] or "loot_map.png"
                        safe_code = re.sub(r'[^A-Za-z0-9]', '', str(code))
                        marker_name = f"loot-q{safe_code}-p{prio}-{orig_name}" if safe_code else orig_name
                        file = await self._download(img, filename=marker_name)
                    if file:
                        await channel.send(content=content, file=file)
                    else:
                        # No downloadable file → include the CDN url in text so the watcher
                        # still detects an image, and carry the queue linkage in a subtext line.
                        if img:
                            content = f"{content}\n{img}"
                        content = f"{content}\n-# [loot-bridge] queue:{code} priority:{prio}"
                        await channel.send(content=content)

                    await database.mark_loot_dispatched(LOOT_QUEUE_GUILD_ID, code)
                    logger.info(f"[loot_bridge] dispatched loot queue '{code}' → Management maps-not-taken channel")
                except Exception as e:
                    logger.error(f"[loot_bridge] dispatch failed for queue '{code}': {e}")
        except Exception as e:
            logger.error(f"[loot_bridge] sweep error: {e}")

    @loot_dispatch_sweep.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(LootBridgeCog(bot))
    logger.info("✅ LootBridgeCog loaded (loot cross-bot dispatch)")
