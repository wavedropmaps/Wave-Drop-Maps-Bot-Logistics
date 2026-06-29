"""
Invite Auto-Purge Commands for Wave Logistics Bot.

Commands:
- -z invitedelete - Toggle invite auto-purge on/off (admin only)
"""

import discord
from discord.ext import commands
import Database.database_improved as database
import logging

logger = logging.getLogger('discord')

class InviteDeleting(commands.Cog):
    """Commands for managing invite auto-purge rules."""

    def __init__(self, bot):
        self.bot = bot

    def is_administrator():
        async def predicate(ctx):
            return ctx.author.guild_permissions.administrator
        return commands.check(predicate)

    @commands.command(name='invitedelete')
    @is_administrator()
    async def invite_delete_toggle(self, ctx):
        """
        Toggle invite auto-purge for this server (admin only).

        **Auto-Purge Rules:**
        • Invites with 2-day expiration AND 1 or fewer uses
        • Invites with 4-day expiration AND 0 uses
        • Infinite invites older than 1 week with 0 uses (bot-created invites excluded)

        Usage: -z invitedelete
        """
        try:
            # Get current status
            currently_enabled = await database.get_invite_rules_enabled(ctx.guild.id)
            new_status = not currently_enabled

            # Toggle in database
            await database.toggle_invite_rules(ctx.guild.id, new_status)

            status_text = "✅ **ENABLED**" if new_status else "❌ **DISABLED**"
            status_color = discord.Color.green() if new_status else discord.Color.red()

            embed = discord.Embed(
                title="Invite Auto-Purge",
                description=f"Invite auto-purge has been {status_text}",
                color=status_color
            )

            embed.add_field(
                name="Auto-Delete Rules",
                value=(
                    "🗑️ **2 days or less remaining** with **1 or fewer uses**\n"
                    "🗑️ **4 days or less remaining** with **0 uses**\n"
                    "🗑️ **Infinite invites** older than **2 weeks** with **0 uses**\n"
                    "*(Bot-created invites are excluded)*"
                ),
                inline=False
            )

            embed.add_field(
                name="Status",
                value=f"Currently: {status_text}",
                inline=False
            )

            logger.info(f"[INVITE DELETE] Guild {ctx.guild.id} ({ctx.guild.name}): Invite auto-purge toggled to {new_status}")

            await ctx.send(embed=embed)

        except Exception as e:
            logger.error(f"[INVITE DELETE] Error toggling invite rules for guild {ctx.guild.id}: {e}")
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to toggle invite auto-purge settings. Please try again.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

async def setup(bot):
    """Load the cog."""
    await bot.add_cog(InviteDeleting(bot))
