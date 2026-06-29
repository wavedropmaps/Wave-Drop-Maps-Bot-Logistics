import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import logging
import sys
import os
import json

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Database.database_improved as database

logger = logging.getLogger('discord')

CONFIG_PATH = "server_config.json"
MANAGEMENT_ROLES = ('Management', '007', '+')


def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure("You need **Administrator** or a **Management** role to use this command.")
    return commands.check(predicate)


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def save_config(config):
    # Atomic write: a crash mid-dump must not truncate server_config.json
    # (every cog reads it; a corrupt file would break their listeners).
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


def build_progress_bar(days_left: int, total_days: int = 30, length: int = 12) -> str:
    """Returns a text progress bar showing time remaining."""
    filled = max(0, min(length, round((days_left / total_days) * length)))
    bar = "█" * filled + "░" * (length - filled)
    percent = max(0, round((days_left / total_days) * 100))
    return f"`{bar}` {percent}%"


def build_role_block(record) -> tuple:
    """Returns (formatted text, color) for a single role record."""
    now = datetime.now(timezone.utc)
    assigned_at = datetime.fromisoformat(record['assigned_at'])
    expiry = assigned_at + timedelta(days=30)
    days_left = (expiry - now).days
    hours_left = int((expiry - now).total_seconds() // 3600)

    if days_left <= 0:
        time_str = f"Expiring very soon ({hours_left}h left)"
        color = discord.Color.red()
    elif days_left <= 3:
        time_str = f"⚠️ Expiring soon — **{days_left}d** left"
        color = discord.Color.orange()
    else:
        time_str = f"**{days_left} days** left"
        color = discord.Color.green()

    progress_bar = build_progress_bar(max(0, days_left))

    text = (
        f"📅 **Started:** {assigned_at.strftime('%d/%m/%Y at %H:%M')} UTC\n"
        f"⏰ **Expires:** {expiry.strftime('%d/%m/%Y at %H:%M')} UTC\n"
        f"⏳ **Time left:** {time_str}\n"
        f"{progress_bar}"
    )
    return text, color


def get_milestone_badge(count: int) -> str:
    if count >= 12:
        return "👑"
    elif count >= 6:
        return "💎"
    elif count >= 3:
        return "🔥"
    elif count >= 2:
        return "⭐"
    else:
        return "✨"


def get_milestone_label(count: int) -> str:
    if count >= 12:
        return "Legendary Supporter"
    elif count >= 6:
        return "Diamond Supporter"
    elif count >= 3:
        return "Dedicated Supporter"
    elif count >= 2:
        return "Returning Supporter"
    else:
        return "First-Time Supporter"


def get_server_type(guild_id: int) -> str:
    """Returns the server type for a guild, defaulting to 'drop_maps'."""
    config = load_config()
    return config.get(str(guild_id), {}).get('server_type', 'drop_maps')


def get_contributor_perks(count: int, server_type: str = 'drop_maps') -> str:
    """Returns unlocked and upcoming perks for contributor streaks, based on server type."""
    lines = []

    three_x_label = (
        "2 free premium loot routes" if server_type == 'loot_routes' else "2 free premium drop maps"
    )
    six_x_label = "1 month free"
    twelve_x_label = "1 pro drop map free"

    lines.append(f"👑 **12x** — {twelve_x_label} {'✅ unlocked' if count >= 12 else f'({max(0, 12 - count)} more to go)'}")
    lines.append(f"💎 **6x** — {six_x_label} {'✅ unlocked' if count >= 6 else f'({max(0, 6 - count)} more to go)'}")
    lines.append(f"🔥 **3x** — {three_x_label} {'✅ unlocked' if count >= 3 else f'({max(0, 3 - count)} more to go)'}")

    return "\n".join(lines)


def get_priority_perks(count: int, server_type: str = 'drop_maps') -> str:
    """Returns unlocked and upcoming perks for priority streaks, based on server type."""
    lines = []

    six_x_label = (
        "1 premium loot route free" if server_type == 'loot_routes' else "1 premium drop map free"
    )

    lines.append(f"👑 **12x** — 1 month free {'✅ unlocked' if count >= 12 else f'({max(0, 12 - count)} more to go)'}")
    lines.append(f"💎 **6x** — {six_x_label} {'✅ unlocked' if count >= 6 else f'({max(0, 6 - count)} more to go)'}")
    lines.append(f"🔥 **3x** — 50% off on your 4th month {'✅ unlocked' if count >= 3 else f'({max(0, 3 - count)} more to go)'}")

    return "\n".join(lines)


class MemberCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='setstreakinfo')
    @is_authorized()
    async def set_streak_info(self, ctx, channel: discord.TextChannel):
        config = load_config()
        guild_id = str(ctx.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]['streak_log_channel_id'] = channel.id
        save_config(config)
        logger.info(f"Streak info channel set to #{channel.name} in {ctx.guild.name}")
        embed = discord.Embed(
            title="✅ Streak Info Channel Set",
            description=f"Streak info will be posted to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    @app_commands.command(
        name="status",
        description="Check your or another user's current subscription status — Priority and Contributor role timers."
    )
    @app_commands.describe(user="Optional: Check another user's status (username, ID, or mention)")
    async def status(self, interaction: discord.Interaction, user: discord.User = None):
        config = load_config()
        guild_id = str(interaction.guild.id)
        guild_config = config.get(guild_id, {})

        priority_role_id = guild_config.get('priority_role_id')
        contributor_role_id = guild_config.get('contributor_role_id')

        # Determine target user
        target_user = user if user is not None else interaction.user
        checking_self = target_user.id == interaction.user.id
        
        member_role_ids = {r.id for r in target_user.roles}
        has_priority_role = priority_role_id and priority_role_id in member_role_ids
        has_contributor_role = contributor_role_id and contributor_role_id in member_role_ids

        if not has_priority_role and not has_contributor_role:
            if checking_self:
                title = "🚫 No Active Subscription"
                description = (
                    "You don't have an active **Priority** or **Contributor** role in this server.\n\n"
                    "This command is only available to current subscribers.\n"
                    "If you believe this is a mistake, please contact a staff member."
                )
            else:
                title = "🚫 No Active Subscription"
                description = (
                    f"{target_user.mention} doesn't have an active **Priority** or **Contributor** role in this server.\n\n"
                    "This command is only available to current subscribers."
                )
            
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.red()
            )
            embed.set_footer(text=f"Wave Free Drop Maps | {interaction.guild.name}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        overall_color = discord.Color.green()
        
        # Set appropriate title and description
        if checking_self:
            title = "📋 Your Subscription Status"
            description = f"Here's your active **30-day** role status in **{interaction.guild.name}**:"
        else:
            title = f"📋 {target_user.display_name}'s Subscription Status"
            description = f"Here's {target_user.mention}'s active **30-day** role status in **{interaction.guild.name}**:"
        
        embed = discord.Embed(
            title=title,
            description=description,
            color=overall_color
        )

        if has_priority_role:
            record = await database.get_tracked_role(
                interaction.guild.id, target_user.id, 'priority'
            )
            if record:
                text, color = build_role_block(record)
                embed.add_field(name="⚡ Priority", value=text, inline=False)
                if color == discord.Color.red():
                    overall_color = discord.Color.red()
                elif color == discord.Color.orange() and overall_color != discord.Color.red():
                    overall_color = discord.Color.orange()
            else:
                if checking_self:
                    value_text = "✅ You have this role but no tracking record was found. Please contact staff."
                else:
                    value_text = f"✅ {target_user.mention} has this role but no tracking record was found."
                embed.add_field(
                    name="⚡ Priority",
                    value=value_text,
                    inline=False
                )

        if has_contributor_role:
            record = await database.get_tracked_role(
                interaction.guild.id, target_user.id, 'contributor'
            )
            if record:
                text, color = build_role_block(record)
                embed.add_field(name="🤝 Contributor", value=text, inline=False)
                if color == discord.Color.red():
                    overall_color = discord.Color.red()
                elif color == discord.Color.orange() and overall_color != discord.Color.red():
                    overall_color = discord.Color.orange()
            else:
                if checking_self:
                    value_text = "✅ You have this role but no tracking record was found. Please contact staff."
                else:
                    value_text = f"✅ {target_user.mention} has this role but no tracking record was found."
                embed.add_field(
                    name="🤝 Contributor",
                    value=value_text,
                    inline=False
                )

        embed.color = overall_color
        embed.set_footer(text=f"Wave Free Drop Maps | {interaction.guild.name}")
        embed.set_thumbnail(url=target_user.display_avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if checking_self:
            logger.info(f"{interaction.user} used /status in {interaction.guild.name}")
        else:
            logger.info(f"{interaction.user} checked {target_user}'s status in {interaction.guild.name}")

    @app_commands.command(
        name="streak",
        description="See how many times you or another user has supported Wave — with milestone perks!"
    )
    @app_commands.describe(user="Optional: Check another user's streak (username, ID, or mention)")
    async def streak(self, interaction: discord.Interaction, user: discord.User = None):
        config = load_config()
        guild_id = str(interaction.guild.id)
        guild_config = config.get(guild_id, {})

        priority_role_id = guild_config.get('priority_role_id')
        contributor_role_id = guild_config.get('contributor_role_id')

        # Determine target user
        target_user = user if user is not None else interaction.user
        checking_self = target_user.id == interaction.user.id
        
        member_role_ids = {r.id for r in target_user.roles}
        has_priority_role = priority_role_id and priority_role_id in member_role_ids
        has_contributor_role = contributor_role_id and contributor_role_id in member_role_ids

        if not has_priority_role and not has_contributor_role:
            if checking_self:
                title = "🚫 No Active Subscription"
                description = (
                    "You don't have an active **Priority** or **Contributor** role in this server.\n\n"
                    "This command is only available to current subscribers.\n"
                    "If you believe this is a mistake, please contact a staff member."
                )
            else:
                title = "🚫 No Active Subscription"
                description = (
                    f"{target_user.mention} doesn't have an active **Priority** or **Contributor** role in this server.\n\n"
                    "This command is only available to current subscribers."
                )
            
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.red()
            )
            embed.set_footer(text=f"Wave Free Drop Maps | {interaction.guild.name}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Set appropriate title and description
        if checking_self:
            title = "🏅 Your Support Streak"
            description = f"Here's your support history in **{interaction.guild.name}**:"
        else:
            title = f"🏅 {target_user.display_name}'s Support Streak"
            description = f"Here's {target_user.mention}'s support history in **{interaction.guild.name}**:"

        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.blurple()
        )

        any_history = False

        server_type = get_server_type(interaction.guild.id)

        if has_priority_role and server_type != 'loot_routes':
            entries = await database.get_streak(interaction.guild.id, target_user.id, 'priority')
            count = len(entries)
            if count > 0:
                any_history = True
                badge = get_milestone_badge(count)
                label = get_milestone_label(count)
                first_date = datetime.fromisoformat(entries[0]).strftime('%d/%m/%Y')
                perks = get_priority_perks(count, server_type)
                embed.add_field(
                    name=f"⚡ Priority  {badge} {label}",
                    value=(
                        f"**Total months supported:** {count}x\n"
                        f"**First supported:** {first_date}\n\n"
                        f"**🎁 Perks:**\n{perks}"
                    ),
                    inline=False
                )

        if has_contributor_role:
            entries = await database.get_streak(interaction.guild.id, target_user.id, 'contributor')
            count = len(entries)
            if count > 0:
                any_history = True
                badge = get_milestone_badge(count)
                label = get_milestone_label(count)
                first_date = datetime.fromisoformat(entries[0]).strftime('%d/%m/%Y')
                perks = get_contributor_perks(count, server_type)
                embed.add_field(
                    name=f"🤝 Contributor  {badge} {label}",
                    value=(
                        f"**Total months supported:** {count}x\n"
                        f"**First supported:** {first_date}\n\n"
                        f"**🎁 Perks:**\n{perks}"
                    ),
                    inline=False
                )

        if not any_history:
            if checking_self:
                embed.description = (
                    "You have a role but no streak history was found yet.\n"
                    "Your streak will start counting from your next role assignment. "
                    "If you think this is wrong, please contact staff."
                )
            else:
                embed.description = (
                    f"{target_user.mention} has a role but no streak history was found yet.\n"
                    "Their streak will start counting from their next role assignment."
                )
            embed.color = discord.Color.orange()

        embed.set_footer(text=f"Wave Free Drop Maps | {interaction.guild.name}")
        embed.set_thumbnail(url=target_user.display_avatar.url)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        if checking_self:
            logger.info(f"{interaction.user} used /streak in {interaction.guild.name}")
        else:
            logger.info(f"{interaction.user} checked {target_user}'s streak in {interaction.guild.name}")


async def setup(bot):
    await bot.add_cog(MemberCommands(bot))
    logger.info("✅ MemberCommands cog loaded")