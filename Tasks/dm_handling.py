"""
DM Handling Task — Wave Logistics Bot
======================================
Logs all incoming DMs to a dedicated Discord channel.

  📥 Received DMs  →  channel 1488725432726716446

Sending is no longer monkey-patched here. Main.py owns the User.send /
Member.send patch and routes every outbound DM through Tasks.dm_queue
(shared SQLite queue with both bots). The dm_queue worker handles its
own send-logging to the DM_SEND_LOG_CHANNEL.
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import aiosqlite
import discord
from discord.ext import commands

logger = logging.getLogger('discord')

# ── Hardcoded log channel IDs ─────────────────────────────────────────────────
RECEIVE_LOG_CHANNEL = 1488725432726716446   # 📥 DMs received from users
SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"


async def _get_last_sent_dm(user_id: int):
    """Return (summary, sent_at) of the most recent outbound DM to user_id, or (None, None)."""
    try:
        async with aiosqlite.connect(SHARED_DB, timeout=5.0) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT content, kwargs_json, sent_at FROM (
                    SELECT content, kwargs_json, sent_at FROM dm_queue
                    WHERE user_id=? AND status='sent' AND sent_at IS NOT NULL
                    UNION ALL
                    SELECT content, kwargs_json, sent_at FROM dm_sent_archive
                    WHERE user_id=?
                )
                ORDER BY sent_at DESC
                LIMIT 1
            """, (user_id, user_id)) as cursor:
                row = await cursor.fetchone()
        if not row:
            return None, None
        text = row['content']
        if not text:
            try:
                kw = json.loads(row['kwargs_json'] or '{}')
                emb = kw.get('embed') or (kw.get('embeds') or [None])[0]
                if emb:
                    parts = []
                    if emb.get('title'): parts.append(emb['title'])
                    if emb.get('description'): parts.append(emb['description'][:200])
                    text = " — ".join(parts) if parts else "*embed (no text)*"
                else:
                    text = "*no text content*"
            except Exception:
                text = "*unknown*"
        return text[:400], row['sent_at']
    except Exception as e:
        logger.debug(f"[DM Handling] could not fetch last sent DM: {e}")
        return None, None


class DMHandling(commands.Cog):
    """Logs incoming DMs received from users to the configured log channel."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── Listener: incoming DMs ────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Log any DM received from a real user."""
        if message.guild is not None:
            return   # not a DM
        if message.author.bot:
            return   # ignore bots / ourselves

        asyncio.create_task(self._log_received_dm(message))

    # ── Internal log helper ───────────────────────────────────────────────────

    async def _log_received_dm(self, message: discord.Message):
        """Post a 📥 embed to RECEIVE_LOG_CHANNEL."""
        try:
            channel = self.bot.get_channel(RECEIVE_LOG_CHANNEL)
            if not channel:
                logger.warning(f"[DM Handling] Receive log channel {RECEIVE_LOG_CHANNEL} not found")
                return

            embed = discord.Embed(
                title="📥 DM Received",
                color=discord.Color.blue(),
                timestamp=message.created_at
            )
            embed.add_field(
                name="From",
                value=f"{message.author.mention} (`{message.author}`)\nID: `{message.author.id}`",
                inline=False
            )

            content = message.content or "*No text content*"
            if len(content) > 1024:
                content = content[:1021] + "..."
            embed.add_field(name="Message", value=content, inline=False)

            if message.attachments:
                att_lines = "\n".join(
                    f"[{a.filename}]({a.url})" for a in message.attachments
                )
                embed.add_field(name="Attachments", value=att_lines[:1024], inline=False)

            # Show the last thing the bot said to this user for context
            last_text, last_sent_at = await _get_last_sent_dm(message.author.id)
            if last_text:
                age_s = time.time() - last_sent_at
                if age_s < 3600:
                    age_str = f"{int(age_s // 60)}m ago"
                elif age_s < 86400:
                    age_str = f"{age_s / 3600:.1f}h ago"
                else:
                    age_str = f"{age_s / 86400:.1f}d ago"
                snippet = last_text if len(last_text) <= 300 else last_text[:297] + "..."
                embed.add_field(name=f"↩️ Last bot msg ({age_str})", value=snippet, inline=False)

            if message.author.avatar:
                embed.set_thumbnail(url=message.author.avatar.url)

            embed.set_footer(text=f"User ID: {message.author.id}")
            await channel.send(embed=embed)

        except Exception as e:
            logger.error(f"[DM Handling] Failed to log received DM: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(DMHandling(bot))
    logger.info("✅ DMHandling cog loaded (receive-only — sends are handled by dm_queue)")
