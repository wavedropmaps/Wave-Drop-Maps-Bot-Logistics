"""
Vouch DM Task — Wave Logistics Bot
==================================
Monitors a specific channel and DMs users without required roles.

  📧 Users without vouch role → get sent to DM
"""

import asyncio
import logging

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

# ── Hardcoded configuration ───────────────────────────────────────────────────
GUILD_ID = 988564962802810961
MONITOR_CHANNEL_ID = 1210814682357698621
REQUIRED_ROLES = [1055713830988157039, 993395068826296361]

DM_MESSAGE = """Hey {user_mention}, TYSM for vouching for our server! 💙

Did you know you can get **__200+ free drop maps for landmark+ reload + OG__** locations by just by following this channel!
👉 https://discord.com/channels/988564962802810961/1210768729395437568"""


class VouchDMing(commands.Cog):
    """Monitors vouch channel and DMs users without required roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Check if user has required role, DM them if not."""
        # Only monitor the specific channel
        if message.channel.id != MONITOR_CHANNEL_ID:
            return

        # Only monitor the specific guild
        if message.guild.id != GUILD_ID:
            return

        # Ignore bot messages
        if message.author.bot:
            return

        # Check if user has at least one required role
        member = message.author
        has_role = any(member.get_role(role_id) for role_id in REQUIRED_ROLES)

        # If they don't have the role, send them a DM
        if not has_role:
            asyncio.create_task(self._send_vouch_dm(member))

    async def _send_vouch_dm(self, member: discord.Member):
        """Send the vouch DM to the member."""
        try:
            dm_content = DM_MESSAGE.format(user_mention=member.mention)
            await member.send(dm_content)
            logger.info(f"[VouchDMing] Sent DM to {member} ({member.id})")
        except discord.Forbidden:
            logger.warning(f"[VouchDMing] Cannot DM {member} ({member.id}) — DMs disabled")
        except Exception as e:
            logger.error(f"[VouchDMing] Failed to DM {member} ({member.id}): {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(VouchDMing(bot))
    logger.info("✅ VouchDMing cog loaded")
