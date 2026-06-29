"""
Auto-Reply to Incoming DMs (sticky-note model) — Logistics Bot
==============================================================
Mirrors Wave Management Bot's tasks/auto_reply.py.

Listens for incoming DMs. If the user has an armed sticky note
(placed by Management bot's reply_dm_duty after forwarding a staff reply),
fire the guild-specific auto-reply text and disarm the note.

If no armed note exists → stay silent.

Notes are stored in the shared DB `reply_dm_note` table. Either bot can
fire the auto-reply because the note lives in the shared DB.
"""

import logging

import discord
from discord.ext import commands

from Tasks.reply_dm_note import get_active_note

logger = logging.getLogger('discord')

# Keep in sync with Wave Management Bot/tasks/auto_reply.py.
AUTO_REPLY_BY_GUILD = {
    988564962802810961: (
        "Hi {user_mention}! 👋\n\n"
        "Thanks for reaching out! If you're sending proof, please post it in "
        "https://discord.com/channels/988564962802810961/1210798761329295440 "
        "on the server instead — a staff member will review it and give you the role. "
        "That way nothing gets lost in DMs!\n\n"
        "Thank you for taking the time to DM me — we appreciate it! Have a blessed day! 💙"
    ),
    971731167621574666: (
        "Hi {user_mention}! 👋\n\n"
        "Thanks for reaching out! If you're sending proof, please post it in "
        "https://discord.com/channels/971731167621574666/1188088624345002035 "
        "on the server instead — a staff member will review it and give you the role. "
        "That way nothing gets lost in DMs!\n\n"
        "Thank you for taking the time to DM me — we appreciate it! Have a blessed day! 💙"
    ),
}


class AutoReplyCog(commands.Cog):
    """Fires a guild-specific auto-reply when a member with an armed note DMs the bot."""

    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("[AutoReply] Cog ready — listening for incoming DMs")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is not None or message.author.bot:
            return

        if message.content.startswith(self.bot.command_prefix):
            return

        try:
            note = await get_active_note(message.author.id)
            if note is None:
                return

            guild_id, source_bot_id = note

            # Only fire auto-reply if THIS bot sent the original reply_dm_duty message.
            # Without this check, both bots would see the incoming DM and both would
            # try to send the auto-reply.
            if source_bot_id != self.bot.user.id:
                logger.debug(
                    f"[AutoReply] Skipping DM from {message.author.id} — "
                    f"note was armed by bot {source_bot_id}, not this bot {self.bot.user.id}"
                )
                return

            template = AUTO_REPLY_BY_GUILD.get(guild_id)
            if template is None:
                logger.warning(
                    f"[AutoReply] No template configured for guild {guild_id} "
                    f"(user {message.author.id}). Skipping."
                )
                return

            reply_text = template.format(user_mention=message.author.mention)

            # The dm_handling patch wipes the note after successful send.
            # On Forbidden, the exception propagates and the wipe never runs,
            # leaving the note armed for retry.
            try:
                await message.author.send(reply_text, _source="auto_reply")
                logger.info(
                    f"[AutoReply] SENT to user={message.author.id} guild={guild_id}"
                )
            except discord.Forbidden:
                logger.warning(
                    f"[AutoReply] FORBIDDEN for user={message.author.id} "
                    f"(DMs blocked); note left armed for retry"
                )

        except Exception as e:
            logger.error(f"[AutoReply] Error handling DM from {message.author.id}: {e}", exc_info=True)


async def setup(bot):
    await bot.add_cog(AutoReplyCog(bot))
    logger.info("✅ AutoReplyCog loaded")
