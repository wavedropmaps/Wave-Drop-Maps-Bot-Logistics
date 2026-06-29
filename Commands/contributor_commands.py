import discord
from discord.ext import commands
import json
import os
import logging

logger = logging.getLogger('discord')

CONFIG_PATH = "server_config.json"
MANAGEMENT_ROLES = ('Management', '007', '+')

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        return json.load(f)

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

class ContributorCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='setcontributor')
    @is_authorized()
    async def set_contributor(self, ctx, role: discord.Role):
        config = load_config()
        guild_id = str(ctx.guild.id)
        if guild_id not in config:
            config[guild_id] = {}
        config[guild_id]['contributor_role_id'] = role.id
        save_config(config)
        logger.info(f"Contributor role set to {role.name} ({role.id}) in {ctx.guild.name}")
        embed = discord.Embed(
            title="✅ Contributor Role Set",
            description=f"Now tracking {role.mention} as the **Contributor** role.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(ContributorCommands(bot))
    logger.info("✅ ContributorCommands cog loaded")