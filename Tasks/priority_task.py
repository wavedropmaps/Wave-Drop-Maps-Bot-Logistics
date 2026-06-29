import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta
import json
import os
import logging
import asyncio

import Database.database_improved as database

logger = logging.getLogger('discord')
CONFIG_PATH = "server_config.json"

GENERIC_MESSAGE = (
    "Thank you for supporting Wave Free Drop Maps through your purchase! "
    "Your support is greatly appreciated and goes a long way!"
)

GUILD_DM_CONTENT = {
    988564962802810961: {
        "priority": (
            "🗺️ **Request your drop map:** https://discord.com/channels/988564962802810961/1364832845423575040\n"
            "💬 **Your exclusive paid chat:** https://discord.com/channels/988564962802810961/1441214889497591941"
        )
    },
    971731167621574666: {
        "priority": None
    }
}

# Custom renewal text shown in both the 3-day warning and removal DMs, per guild
GUILD_RENEW_TEXT = {
    988564962802810961: {
        "priority": (
            "Please contact a staff member and state your purpose is to purchase the Paid Priority again.\n"
            "🎟️ https://discord.com/channels/988564962802810961/1210815399113785385"
        ),
    },
    971731167621574666: {
        "priority": (
            "Please contact a staff member and state your purpose is to purchase the Paid Priority again."
        ),
    },
}

async def send_assignment_dm(member, guild, role_type):
    guild_content = GUILD_DM_CONTENT.get(guild.id, {})
    extra = guild_content.get(role_type)

    embed = discord.Embed(
        title="🎉 Thank You for Your Support!",
        description=GENERIC_MESSAGE,
        color=discord.Color.blue()
    )
    embed.add_field(
        name="⚡ Priority Status",
        value=(
            "Your request will be placed **first in the queue**, meaning your drop map "
            "will be prioritised at the very start of the creation process."
        ),
        inline=False
    )
    embed.add_field(
        name="⏳ Role Duration",
        value="Your **Priority** role lasts **30 days**. You will receive a reminder 3 days before it expires.",
        inline=False
    )
    if extra:
        embed.add_field(
            name="📌 Useful Links",
            value=extra,
            inline=False
        )
    embed.set_footer(text=f"Wave Free Drop Maps | {guild.name}")

    try:
        await member.send(embed=embed)
        logger.info(f"Sent assignment DM to {member} for priority role in {guild.name}")
    except discord.Forbidden:
        logger.warning(f"Could not DM {member} for priority role assignment")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)

async def send_dms_batched(tasks: list, batch_size: int = 10, delay: float = 1.0):
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        if i + batch_size < len(tasks):
            await asyncio.sleep(delay)

class PriorityTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_priority_roles.start()

    def cog_unload(self):
        self.check_priority_roles.cancel()

    @tasks.loop(hours=1)
    async def check_priority_roles(self):
        config = load_config()
        now = datetime.now(timezone.utc)
        records = await database.get_all_tracked_roles()

        warning_dms = []
        removal_dms = []
        removal_records = []

        for record in records:
            if record['role_type'] != 'priority':
                continue

            guild_id = str(record['guild_id'])
            if guild_id not in config:
                continue

            guild = self.bot.get_guild(record['guild_id'])
            if not guild:
                continue

            member = guild.get_member(record['user_id'])
            assigned_at = datetime.fromisoformat(record['assigned_at'])
            days_elapsed = (now - assigned_at).days

            priority_role_id = config[guild_id].get('priority_role_id')
            priority_role = guild.get_role(priority_role_id) if priority_role_id else None
            user_mention = member.mention if member else f"<@{record['user_id']}>"

            # Get guild-specific renewal text, fallback to generic
            renew_text = (
                GUILD_RENEW_TEXT
                .get(record['guild_id'], {})
                .get('priority', "Please contact a staff member to renew it before it is removed.")
            )
            removal_renew_text = (
                GUILD_RENEW_TEXT
                .get(record['guild_id'], {})
                .get('priority', "Please contact a staff member to purchase it again.")
            )

            # Day 27 warning (3 days before day 30)
            if days_elapsed >= 27 and not record['warned']:
                if member:
                    embed = discord.Embed(
                        title="⚠️ Priority Role Expiring Soon",
                        description=(
                            f"Your **30-day Priority** subscription in **{guild.name}** expires in **3 days**.\n\n"
                            f"{renew_text}"
                        ),
                        color=discord.Color.orange()
                    )
                    warning_dms.append(member.send(embed=embed))

                await database.set_warned(record['id'])

            # Day 30 removal
            if days_elapsed >= 30:
                # Cache miss ≠ member left — confirm via the API before
                # deciding, or a transient cache gap deletes the tracking
                # record while the user silently keeps the paid role.
                if member is None:
                    try:
                        member = await guild.fetch_member(record['user_id'])
                    except discord.NotFound:
                        member = None  # genuinely left the server
                    except discord.HTTPException:
                        continue  # transient API error — retry next hour

                if member and priority_role and priority_role in member.roles:
                    try:
                        await member.remove_roles(priority_role, reason="Priority role expired after 30 days")
                        logger.info(f"Removed priority role from {member} in {guild.name}")
                    except discord.HTTPException:
                        # Keep the tracking record so next hour's run retries —
                        # untracking here would leave the user the expired paid
                        # role forever. (Forbidden is an HTTPException too.)
                        logger.warning(f"Could not remove priority role from {member} — keeping record, will retry")
                        continue

                    # DM only after the role is actually removed; a kept record
                    # retrying hourly must not re-DM the user every hour.
                    embed = discord.Embed(
                        title="❌ Priority Role Removed",
                        description=(
                            f"Your **30-day Priority** subscription in **{guild.name}** has expired.\n\n"
                            f"{removal_renew_text}"
                        ),
                        color=discord.Color.red()
                    )
                    removal_dms.append(member.send(embed=embed))

                removal_records.append((record, user_mention))

        if warning_dms:
            logger.info(f"Sending {len(warning_dms)} priority warning DM(s) in batches...")
            await send_dms_batched(warning_dms)

        if removal_dms:
            logger.info(f"Sending {len(removal_dms)} priority removal DM(s) in batches...")
            await send_dms_batched(removal_dms)

        for record, user_mention in removal_records:
            _guild = self.bot.get_guild(record['guild_id'])
            _member = _guild.get_member(record['user_id']) if _guild else None
            _days = (datetime.now(timezone.utc) - datetime.fromisoformat(record['assigned_at'])).days
            await database.remove_tracked_role(record['guild_id'], record['user_id'], 'priority', member=_member, guild_obj=_guild, removal_reason="expired", days_elapsed=_days)

    @check_priority_roles.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

async def setup(bot):
    await bot.add_cog(PriorityTask(bot))
    logger.info("✅ PriorityTask cog loaded")