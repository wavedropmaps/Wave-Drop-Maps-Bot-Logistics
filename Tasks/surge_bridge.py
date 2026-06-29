"""
Tasks/surge_bridge.py — Surge cross-bot dispatch (Wave Logistics → Wave Management).

When a SURGE-route request is in the queue (guild 971731167621574666) and hasn't been
dispatched yet, this sweep posts the map into the Management bot's surge-maps channel
(staff hub, 1416770574042140804) formatted like the loot-route maps-not-taken posts:

    Game Mode: <mode>            (only when the queue entry has one)
    Description: <description>
    <image attachment>

The queue linkage (code + customer priority) is hidden in the attachment FILENAME
(`surge-q<code>-p<n>-<original>.png`) so staff never see raw marker text. Only the
URL-only fallback (image couldn't be downloaded) still carries a marker line, rendered
as Discord subtext: `-# [surge-bridge] queue:<code> priority:<n>`.

The Management bot's surge watcher (which accepts bot-forwarded image+text) then assigns
a surge maker. `dispatched_at` is stamped so an entry is never double-posted ("on add +
reconciliation check" — the 60s sweep is the reconciliation). On completion, the Management
bot fires `-z removequeue <code>` back to drop the entry.

Self-contained: a single background loop + DB reads. Does not touch the existing addmap flow.
"""

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

SURGE_QUEUE_GUILD_ID = 971731167621574666          # where the surge queue lives
SURGE_MAPS_CHANNEL_ID = 1416770574042140804        # Management staff-hub surge-maps channel


class SurgeBridgeCog(commands.Cog):
    """Dispatches undispatched surge queue entries to the Management surge-maps channel."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not self.surge_dispatch_sweep.is_running():
            self.surge_dispatch_sweep.start()

    def cog_unload(self):
        if self.surge_dispatch_sweep.is_running():
            self.surge_dispatch_sweep.cancel()

    async def _download(self, url: str, filename: str = None):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        name = filename or url.split('?')[0].split('/')[-1] or "surge_map.png"
                        import io
                        return discord.File(io.BytesIO(await resp.read()), filename=name)
        except Exception as e:
            logger.warning(f"[surge_bridge] image download failed ({url}): {e}")
        return None

    @tasks.loop(seconds=60)
    async def surge_dispatch_sweep(self):
        try:
            reqs = await database.get_undispatched_surge_requests(SURGE_QUEUE_GUILD_ID)
            if not reqs:
                return
            channel = self.bot.get_channel(SURGE_MAPS_CHANNEL_ID)
            if not channel:
                logger.warning("[surge_bridge] surge-maps channel not reachable — is the bot in the staff hub guild?")
                return
            src_guild = self.bot.get_guild(SURGE_QUEUE_GUILD_ID)

            for req in reqs:
                code = req['queue_number']
                try:
                    # Customer priority (lower = higher priority); preserved for the maker-side hold pool.
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
                        lines.append("Surge route request")
                    content = "\n".join(lines)

                    file = None
                    img = req.get('image_url')
                    if img and str(img).startswith('http'):
                        orig_name = img.split('?')[0].split('/')[-1] or "surge_map.png"
                        safe_code = re.sub(r'[^A-Za-z0-9]', '', str(code))
                        marker_name = f"surge-q{safe_code}-p{prio}-{orig_name}" if safe_code else orig_name
                        file = await self._download(img, filename=marker_name)
                    if file:
                        await channel.send(content=content, file=file)
                    else:
                        # No downloadable file → include the CDN url in text so the watcher still
                        # detects an image, and carry the queue linkage in a subtext marker line
                        # (no filename available to hide it in).
                        if img:
                            content = f"{content}\n{img}"
                        content = f"{content}\n-# [surge-bridge] queue:{code} priority:{prio}"
                        await channel.send(content=content)

                    await database.mark_surge_dispatched(SURGE_QUEUE_GUILD_ID, code)
                    logger.info(f"[surge_bridge] dispatched surge queue '{code}' → Management surge channel")
                except Exception as e:
                    logger.error(f"[surge_bridge] dispatch failed for queue '{code}': {e}")
        except Exception as e:
            logger.error(f"[surge_bridge] sweep error: {e}")

    @surge_dispatch_sweep.before_loop
    async def _before_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SurgeBridgeCog(bot))
    logger.info("✅ SurgeBridgeCog loaded (surge cross-bot dispatch)")
