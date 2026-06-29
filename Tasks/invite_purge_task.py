"""
Invite Auto-Purge Background Task for Wave Logistics Bot.

This task runs periodically to delete invites based on configured rules.
"""

import discord
from discord.ext import commands, tasks
import Database.database_improved as database
from datetime import datetime, timezone, timedelta
import logging

logger = logging.getLogger('discord')

class InvitePurgeTask(commands.Cog):
    """Background task for auto-purging invites."""

    def __init__(self, bot):
        self.bot = bot
        self.purge_invites.start()

    def cog_unload(self):
        """Stop the task when cog is unloaded."""
        self.purge_invites.cancel()

    @tasks.loop(hours=24)
    async def purge_invites(self):
        """
        Run invite purge check every 24 hours for all guilds with the feature enabled.

        Rules:
        1. Delete invites with 2 days or less remaining AND 1 or fewer uses
        2. Delete invites with 4 days or less remaining AND 0 uses
        3. Delete infinite invites older than 2 weeks with 0 uses
           (exclude invites created by bots)
        """
        try:
            # Iterate through all connected guilds
            for guild in self.bot.guilds:
                try:
                    # Check if invite purge is enabled for this guild
                    enabled = await database.get_invite_rules_enabled(guild.id)
                    if not enabled:
                        continue

                    logger.debug(f"[INVITE PURGE] Checking guild: {guild.name} ({guild.id})")

                    # Get all invites for the guild
                    try:
                        invites = await guild.invites()
                    except discord.Forbidden:
                        logger.warning(f"[INVITE PURGE] No permission to fetch invites for {guild.name}")
                        continue

                    deleted_count = 0
                    now = datetime.now(timezone.utc)

                    for invite in invites:
                        try:
                            should_delete = False
                            reason = None

                            # Skip invites without created_at or max_age info
                            if not invite.created_at:
                                continue

                            # Calculate remaining time if invite has an expiration
                            remaining_seconds = None
                            if invite.max_age:
                                created_at_utc = invite.created_at.replace(tzinfo=timezone.utc)
                                age_seconds = (now - created_at_utc).total_seconds()
                                remaining_seconds = invite.max_age - age_seconds

                            # Rule 1: 2 days or less remaining with 1 or fewer uses
                            if remaining_seconds is not None and remaining_seconds <= 172800:  # 2 days in seconds
                                if invite.uses is not None and invite.uses <= 1:
                                    should_delete = True
                                    reason = f"≤2 days remaining with ≤1 uses (uses: {invite.uses})"

                            # Rule 2: 4 days or less remaining with 0 uses
                            elif remaining_seconds is not None and remaining_seconds <= 345600:  # 4 days in seconds
                                if invite.uses == 0:
                                    should_delete = True
                                    reason = "≤4 days remaining with 0 uses"

                            # Rule 3: Infinite invites (no expiration) older than 2 weeks with 0 uses
                            # Exclude bot-created invites
                            elif invite.max_age is None:
                                if invite.uses == 0:
                                    created_at_utc = invite.created_at.replace(tzinfo=timezone.utc)
                                    age = now - created_at_utc
                                    if age > timedelta(days=14):
                                        # Check if creator is a bot
                                        if invite.inviter and invite.inviter.bot:
                                            reason = "infinite invite older than 2 weeks with 0 uses (bot-created, excluded)"
                                        else:
                                            should_delete = True
                                            reason = "infinite invite older than 2 weeks with 0 uses"

                            # Delete if rule matched
                            if should_delete:
                                try:
                                    await invite.delete()
                                    deleted_count += 1
                                    logger.info(
                                        f"[INVITE PURGE] Deleted invite {invite.code} in {guild.name}: {reason}"
                                    )
                                except discord.Forbidden:
                                    logger.warning(
                                        f"[INVITE PURGE] No permission to delete invite {invite.code} in {guild.name}"
                                    )
                                except discord.NotFound:
                                    logger.debug(
                                        f"[INVITE PURGE] Invite {invite.code} already deleted in {guild.name}"
                                    )
                                except Exception as e:
                                    logger.error(
                                        f"[INVITE PURGE] Error deleting invite {invite.code} in {guild.name}: {e}"
                                    )

                        except Exception as e:
                            logger.error(f"[INVITE PURGE] Error processing invite in {guild.name}: {e}")
                            continue

                    if deleted_count > 0:
                        logger.info(f"[INVITE PURGE] Purged {deleted_count} invites from {guild.name}")

                except Exception as e:
                    logger.error(f"[INVITE PURGE] Error purging invites for guild {guild.id}: {e}")
                    continue

        except Exception as e:
            logger.error(f"[INVITE PURGE] Critical error in purge_invites task: {e}")

    @purge_invites.before_loop
    async def before_purge_invites(self):
        """Wait for bot to be ready before running the task."""
        await self.bot.wait_until_ready()
        logger.info("[INVITE PURGE] Task initialized, will run on startup and every 24 hours after")


async def setup(bot):
    """Load the cog."""
    await bot.add_cog(InvitePurgeTask(bot))
