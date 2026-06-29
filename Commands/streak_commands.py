import discord
from discord.ext import commands
import json
import os
import logging

logger = logging.getLogger('discord')

CONFIG_PATH = "server_config.json"
MANAGEMENT_ROLES = ('Management', '007', '+')

VALID_TYPES = ('drop_maps', 'loot_routes')

TYPE_LABELS = {
    'drop_maps': '🗺️ Drop Maps',
    'loot_routes': '📍 Loot Routes',
}


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


def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure("You need **Administrator** or a **Management** role to use this command.")
    return commands.check(predicate)


def get_server_type(guild_id: int) -> str:
    """Returns the server type for a guild, defaulting to 'drop_maps'."""
    config = load_config()
    return config.get(str(guild_id), {}).get('server_type', 'drop_maps')


class StreakCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='setservertype')
    @is_authorized()
    async def set_server_type(self, ctx, server_type: str):
        """
        Sets the server type which controls streak perk descriptions.
        Usage: -z setservertype <drop_maps|loot_routes>
        """
        server_type = server_type.lower()

        if server_type not in VALID_TYPES:
            embed = discord.Embed(
                title="❌ Invalid Server Type",
                description=(
                    f"Valid options are:\n"
                    f"`drop_maps` — for drop map servers\n"
                    f"`loot_routes` — for loot route servers"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        config = load_config()
        guild_id = str(ctx.guild.id)
        if guild_id not in config:
            config[guild_id] = {}

        old_type = config[guild_id].get('server_type', 'drop_maps')
        config[guild_id]['server_type'] = server_type
        save_config(config)

        label = TYPE_LABELS[server_type]
        embed = discord.Embed(
            title="✅ Server Type Set",
            description=(
                f"This server is now set to **{label}**.\n\n"
                "Streak perks and the streak info message will reflect this. "
                "Restart the bot or use `-z refreshstreak` to update the posted message."
            ),
            color=discord.Color.green()
        )
        if old_type != server_type:
            embed.set_footer(text=f"Changed from: {TYPE_LABELS.get(old_type, old_type)}")
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} set server type to '{server_type}' in {ctx.guild.name}")

    @commands.command(name='servertype')
    @is_authorized()
    async def show_server_type(self, ctx):
        """Shows the current server type for this server."""
        config = load_config()
        guild_id = str(ctx.guild.id)
        server_type = config.get(guild_id, {}).get('server_type', 'drop_maps')
        label = TYPE_LABELS.get(server_type, server_type)

        embed = discord.Embed(
            title="📋 Current Server Type",
            description=f"This server is set to **{label}**.",
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Use -z setservertype <drop_maps|loot_routes> to change it.")
        await ctx.send(embed=embed)

    @commands.command(name='refreshstreak')
    @is_authorized()
    async def refresh_streak(self, ctx):
        """Manually refreshes the streak info and leaderboard messages in the configured channel."""
        from Tasks.streak_tasks import post_streak_info, load_config as streak_load_config
        config = streak_load_config()
        await post_streak_info(ctx.guild, config)
        embed = discord.Embed(
            title="✅ Streak Messages Refreshed",
            description="The streak info and leaderboard have been updated in the configured channel.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} manually refreshed streak messages in {ctx.guild.name}")


async def setup(bot):
    await bot.add_cog(StreakCommands(bot))
    logger.info("✅ StreakCommands cog loaded")