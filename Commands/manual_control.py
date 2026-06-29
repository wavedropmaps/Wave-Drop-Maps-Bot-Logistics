import discord
from discord.ext import commands
import json
import os
import logging
from datetime import datetime, timezone, timedelta
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Database.database_improved as database

logger = logging.getLogger('discord')
CONFIG_PATH = "server_config.json"
MANAGEMENT_ROLES = ('Management', '007', '+')

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)

def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure("You need **Administrator** or a **Management** role to use this command.")
    return commands.check(predicate)

class ManualControl(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='setexpiry')
    @is_authorized()
    async def set_expiry(self, ctx, role_type: str, *, args: str):
        """
        Set expiry for a single user or all tracked users of a role type.
        Usage (individual): -z setexpiry <priority|contributor> @user <DD/MM/YYYY HH:MM>
        Usage (all):        -z setexpiry <priority|contributor> <DD/MM/YYYY HH:MM>
        Example: -z setexpiry priority @Wave 25/04/2026 18:00
        Example: -z setexpiry priority 25/04/2026 18:00
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Check if a member mention is the first argument
        target_member = None
        date_str = args.strip()

        if ctx.message.mentions:
            target_member = ctx.message.mentions[0]
            # Strip the mention from the args to get the date string
            mention_str = f'<@{target_member.id}>'
            mention_str_nick = f'<@!{target_member.id}>'
            date_str = date_str.replace(mention_str_nick, '').replace(mention_str, '').strip()

        try:
            expiry_dt = datetime.strptime(date_str, "%d/%m/%Y %H:%M")
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            embed = discord.Embed(
                title="❌ Invalid Date Format",
                description=(
                    "Use the format: `DD/MM/YYYY HH:MM`\n\n"
                    "**Individual:** `-z setexpiry priority @user 25/04/2026 18:00`\n"
                    "**All users:** `-z setexpiry priority 25/04/2026 18:00`"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if expiry_dt <= datetime.now(timezone.utc):
            embed = discord.Embed(
                title="❌ Date in the Past",
                description="The expiry date must be in the future.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        new_assigned_at = expiry_dt - timedelta(days=30)

        # --- Individual mode ---
        if target_member:
            record = await database.get_tracked_role(ctx.guild.id, target_member.id, role_type)
            if not record:
                embed = discord.Embed(
                    title="⚠️ Not Tracked",
                    description=f"{target_member.mention} is not currently tracked for **{role_type.capitalize()}**.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

            await database.update_assigned_at(record['id'], new_assigned_at)
            await database.reset_warned(record['id'])

            embed = discord.Embed(
                title="✅ Expiry Date Updated",
                description=(
                    f"Updated expiry for {target_member.mention} (**{role_type.capitalize()}**).\n\n"
                    f"⏰ **New expiry:** {expiry_dt.strftime('%d/%m/%Y at %H:%M')} UTC\n"
                    f"⚠️ **Warning DM** will fire 3 days before "
                    f"(`{(expiry_dt - timedelta(days=3)).strftime('%d/%m/%Y at %H:%M')} UTC`)"
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} set {role_type} expiry to {expiry_dt} for {target_member} in {ctx.guild.name}")
            return

        # --- All users mode --- pull from Discord role, not just DB
        config = load_config()
        guild_config = config.get(str(ctx.guild.id), {})
        role_id = guild_config.get(f"{role_type}_role_id")

        if not role_id:
            embed = discord.Embed(
                title="❌ Role Not Configured",
                description=f"No Discord role is configured for **{role_type.capitalize()}** in this guild.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        discord_role = ctx.guild.get_role(int(role_id))
        if not discord_role:
            embed = discord.Embed(
                title="❌ Role Not Found",
                description=f"Could not find the configured **{role_type.capitalize()}** role in this server.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        members_with_role = [m for m in ctx.guild.members if discord_role in m.roles]

        if not members_with_role:
            embed = discord.Embed(
                title="⚠️ No Members Found",
                description=f"No members currently have the **{discord_role.name}** role.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        updated = 0
        for member in members_with_role:
            existing = await database.get_tracked_role(ctx.guild.id, member.id, role_type)
            if existing:
                await database.update_assigned_at(existing['id'], new_assigned_at)
                await database.reset_warned(existing['id'])
            else:
                await database.add_tracked_role_with_time(ctx.guild.id, member.id, role_type, new_assigned_at, member=member, guild_obj=ctx.guild)
            updated += 1

        embed = discord.Embed(
            title="✅ Expiry Date Updated",
            description=(
                f"Updated **{updated}** tracked **{role_type.capitalize()}** user(s) in **{ctx.guild.name}**.\n\n"
                f"⏰ **New expiry:** {expiry_dt.strftime('%d/%m/%Y at %H:%M')} UTC\n"
                f"⚠️ **Warning DM** will fire 3 days before "
                f"(`{(expiry_dt - timedelta(days=3)).strftime('%d/%m/%Y at %H:%M')} UTC`)"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} set {role_type} expiry to {expiry_dt} for {updated} user(s) in {ctx.guild.name}")

    @commands.command(name='listexpiry')
    @is_authorized()
    async def list_expiry(self, ctx, role_type: str):
        """
        List all tracked users and their expiry dates.
        Usage: -z listexpiry <priority|contributor>
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        records = await database.get_all_tracked_roles()
        guild_records = [
            r for r in records
            if r['guild_id'] == ctx.guild.id and r['role_type'] == role_type
        ]

        if not guild_records:
            embed = discord.Embed(
                title="⚠️ No Users Found",
                description=f"No tracked **{role_type.capitalize()}** users in this guild.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        now = datetime.now(timezone.utc)
        lines = []
        for record in guild_records:
            assigned_at = datetime.fromisoformat(record['assigned_at'])
            expiry = assigned_at + timedelta(days=30)
            days_left = (expiry - now).days
            warned_str = "⚠️ warned" if record['warned'] else ""
            lines.append(
                f"<@{record['user_id']}> — expires **{expiry.strftime('%d/%m/%Y %H:%M')} UTC** "
                f"(`{days_left}d left`) {warned_str}"
            )

        embed = discord.Embed(
            title=f"📋 {role_type.capitalize()} Role Expiry List — {ctx.guild.name}",
            description="\n".join(lines),
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    @commands.command(name='removetracked')
    @is_authorized()
    async def remove_tracked(self, ctx, role_type: str, member: discord.Member):
        """
        Remove a user from tracking without removing their role.
        Usage: -z removetracked <priority|contributor> @user
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        existing = await database.get_tracked_role(ctx.guild.id, member.id, role_type)
        if not existing:
            embed = discord.Embed(
                title="⚠️ Not Tracked",
                description=f"{member.mention} is not currently tracked for **{role_type.capitalize()}**.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        await database.remove_tracked_role(ctx.guild.id, member.id, role_type, member=member, guild_obj=ctx.guild, removal_reason="admin_manual_remove")
        embed = discord.Embed(
            title="✅ Removed from Tracking",
            description=f"{member.mention} removed from **{role_type.capitalize()}** tracking. Their role was **not** removed.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} manually removed {member} from {role_type} tracking in {ctx.guild.name}")

    @commands.command(name='addtracked')
    @is_authorized()
    async def add_tracked(self, ctx, role_type: str, member: discord.Member, *, date_str: str = None):
        """
        Manually add a user to tracking with an optional custom start date.
        Usage: -z addtracked <priority|contributor> @user [DD/MM/YYYY HH:MM]
        No date = starts from now, expires in 30 days.
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if date_str:
            try:
                assigned_at = datetime.strptime(date_str.strip(), "%d/%m/%Y %H:%M")
                assigned_at = assigned_at.replace(tzinfo=timezone.utc)
            except ValueError:
                embed = discord.Embed(
                    title="❌ Invalid Date Format",
                    description="Use the format: `DD/MM/YYYY HH:MM`\nExample: `01/04/2026 12:00`",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
        else:
            assigned_at = None

        existing = await database.get_tracked_role(ctx.guild.id, member.id, role_type)
        if existing:
            await database.remove_tracked_role(ctx.guild.id, member.id, role_type, member=member, guild_obj=ctx.guild, removal_reason="timer_reset")

        if assigned_at:
            await database.add_tracked_role_with_time(ctx.guild.id, member.id, role_type, assigned_at, member=member, guild_obj=ctx.guild)
        else:
            await database.add_tracked_role(ctx.guild.id, member.id, role_type, member=member, guild_obj=ctx.guild)

        expiry = (assigned_at or datetime.now(timezone.utc)) + timedelta(days=30)
        embed = discord.Embed(
            title="✅ User Added to Tracking",
            description=(
                f"{member.mention} is now tracked for **{role_type.capitalize()}**.\n\n"
                f"⏰ **Expires:** {expiry.strftime('%d/%m/%Y at %H:%M')} UTC"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} manually added {member} to {role_type} tracking in {ctx.guild.name} — expiry {expiry}")

    @commands.command(name='removetrackedall')
    @is_authorized()
    async def remove_tracked_all(self, ctx, role_type: str):
        """
        Remove ALL tracked users of a role type from tracking without removing their roles.
        Usage: -z removetrackedall <priority|contributor>
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        records = await database.get_all_tracked_roles()
        guild_records = [
            r for r in records
            if r['guild_id'] == ctx.guild.id and r['role_type'] == role_type
        ]

        if not guild_records:
            embed = discord.Embed(
                title="⚠️ No Users Found",
                description=f"There are no tracked **{role_type.capitalize()}** users in this guild.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        removed = 0
        for record in guild_records:
            await database.remove_tracked_role(ctx.guild.id, record['user_id'], role_type, guild_obj=ctx.guild, removal_reason="admin_bulk_remove")
            removed += 1

        embed = discord.Embed(
            title="✅ All Users Removed from Tracking",
            description=(
                f"Removed **{removed}** tracked **{role_type.capitalize()}** user(s) from tracking in **{ctx.guild.name}**.\n"
                f"Their roles were **not** removed."
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} bulk-removed all {role_type} tracked users in {ctx.guild.name} ({removed} users)")

    @commands.command(name='addtrackedall')
    @is_authorized()
    async def add_tracked_all(self, ctx, role_type: str, *, date_str: str = None):
        """
        Manually add ALL members with the matching Discord role to tracking with an optional custom start date.
        Usage: -z addtrackedall <priority|contributor> [DD/MM/YYYY HH:MM]
        No date = starts from now, expires in 30 days.
        """
        role_type = role_type.lower()
        if role_type not in ('priority', 'contributor'):
            embed = discord.Embed(
                title="❌ Invalid Role Type",
                description="Role type must be `priority` or `contributor`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if date_str:
            try:
                assigned_at = datetime.strptime(date_str.strip(), "%d/%m/%Y %H:%M")
                assigned_at = assigned_at.replace(tzinfo=timezone.utc)
            except ValueError:
                embed = discord.Embed(
                    title="❌ Invalid Date Format",
                    description="Use the format: `DD/MM/YYYY HH:MM`\nExample: `01/04/2026 12:00`",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
        else:
            assigned_at = None

        config = load_config()
        guild_config = config.get(str(ctx.guild.id), {})
        role_id = guild_config.get(f'{role_type}_role_id')

        if not role_id:
            embed = discord.Embed(
                title="❌ Role Not Configured",
                description=f"No Discord role is configured for **{role_type.capitalize()}** in this guild. Check your server config.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        discord_role = ctx.guild.get_role(int(role_id))
        if not discord_role:
            embed = discord.Embed(
                title="❌ Role Not Found",
                description=f"Could not find the configured **{role_type.capitalize()}** role in this server.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        members_with_role = [m for m in ctx.guild.members if discord_role in m.roles]

        if not members_with_role:
            embed = discord.Embed(
                title="⚠️ No Members Found",
                description=f"No members currently have the **{discord_role.name}** role.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        added = 0
        for member in members_with_role:
            existing = await database.get_tracked_role(ctx.guild.id, member.id, role_type)
            if existing:
                await database.remove_tracked_role(ctx.guild.id, member.id, role_type, member=member, guild_obj=ctx.guild, removal_reason="timer_reset")

            if assigned_at:
                await database.add_tracked_role_with_time(ctx.guild.id, member.id, role_type, assigned_at, member=member, guild_obj=ctx.guild)
            else:
                await database.add_tracked_role(ctx.guild.id, member.id, role_type, member=member, guild_obj=ctx.guild)
            added += 1

        expiry = (assigned_at or datetime.now(timezone.utc)) + timedelta(days=30)
        embed = discord.Embed(
            title="✅ All Users Added to Tracking",
            description=(
                f"Added **{added}** member(s) with the **{discord_role.name}** role to **{role_type.capitalize()}** tracking.\n\n"
                f"⏰ **Expires:** {expiry.strftime('%d/%m/%Y at %H:%M')} UTC"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} bulk-added {added} {role_type} users in {ctx.guild.name} — expiry {expiry}")


async def setup(bot):
    await bot.add_cog(ManualControl(bot))
    logger.info("✅ ManualControl cog loaded")