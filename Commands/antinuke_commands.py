import discord
from discord.ext import commands
import json
import os
import logging

logger = logging.getLogger('discord')

CONFIG_PATH = "server_config.json"
MANAGEMENT_ROLES = ('+', '999', '007')


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
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure("You need one of these roles to use this command: '+', '999', or '007'.")
    return commands.check(predicate)


def get_antinuke_config(guild_id: int) -> dict:
    config = load_config()
    return config.get(str(guild_id), {}).get('antinuke', {})


def save_antinuke_config(guild_id: int, antinuke: dict):
    config = load_config()
    guild_id_str = str(guild_id)
    if guild_id_str not in config:
        config[guild_id_str] = {}
    config[guild_id_str]['antinuke'] = antinuke
    save_config(config)


class AntiNukeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='enableantinuke')
    @is_authorized()
    async def enable_antinuke(self, ctx):
        """Enable the antinuke system for this server."""
        an = get_antinuke_config(ctx.guild.id)
        an['enabled'] = True
        save_antinuke_config(ctx.guild.id, an)
        embed = discord.Embed(
            title="✅ AntiNuke Enabled",
            description=(
                "The antinuke system is now **active** in this server.\n\n"
                "Make sure to set up:\n"
                "`-z setquarantine @role` — role to assign to nukers\n"
                "`-z setantinukelog #channel` — channel to log detections\n"
                "`-z whitelist add <user_id>` — fully whitelist trusted users\n"
                "`-z weightedwhitelist add <user_id>` — give user 50% higher thresholds"
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} enabled antinuke in {ctx.guild.name}")

    @commands.command(name='disableantinuke')
    @is_authorized()
    async def disable_antinuke(self, ctx):
        """Disable the antinuke system for this server."""
        an = get_antinuke_config(ctx.guild.id)
        an['enabled'] = False
        save_antinuke_config(ctx.guild.id, an)
        embed = discord.Embed(
            title="🔴 AntiNuke Disabled",
            description="The antinuke system has been **disabled** for this server.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} disabled antinuke in {ctx.guild.name}")

    @commands.command(name='setquarantine')
    @is_authorized()
    async def set_quarantine(self, ctx, role: discord.Role):
        """Set the quarantine role assigned to nukers."""
        an = get_antinuke_config(ctx.guild.id)
        an['quarantine_role_id'] = role.id
        save_antinuke_config(ctx.guild.id, an)
        embed = discord.Embed(
            title="✅ Quarantine Role Set",
            description=f"{role.mention} will be assigned to any detected nuker.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} set quarantine role to {role.name} in {ctx.guild.name}")

    @commands.command(name='setantinukelog')
    @is_authorized()
    async def set_antinuke_log(self, ctx, channel: discord.TextChannel):
        """Set the channel where antinuke detections are logged."""
        an = get_antinuke_config(ctx.guild.id)
        an['log_channel_id'] = channel.id
        save_antinuke_config(ctx.guild.id, an)
        embed = discord.Embed(
            title="✅ AntiNuke Log Channel Set",
            description=f"Antinuke detections will be logged to {channel.mention}.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} set antinuke log to #{channel.name} in {ctx.guild.name}")

    # ── Weighted Whitelist ───────────────────────────────────────────────────

    @commands.command(name='weightedwhitelist')
    @is_authorized()
    async def weighted_whitelist(self, ctx, action: str, *targets: str):
        """
        Manage the weighted whitelist — users who need 50% more actions before being quarantined.
        Usage: -z weightedwhitelist add @user 123456789
               -z weightedwhitelist remove @user 123456789
               -z weightedwhitelist list
        """
        action = action.lower()
        an = get_antinuke_config(ctx.guild.id)
        ww = an.get('weighted_whitelist', [])

        if action == 'list':
            embed = discord.Embed(
                title="📋 Weighted Whitelist",
                description="\n".join(f"<@{uid}> (`{uid}`)" for uid in ww) if ww else "No users on the weighted whitelist.",
                color=discord.Color.blurple()
            )
            await ctx.send(embed=embed)
            return

        if action not in ('add', 'remove'):
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Action",
                description="Use `add`, `remove`, or `list`.",
                color=discord.Color.red()
            ))
            return

        if not targets:
            await ctx.send(embed=discord.Embed(
                title="❌ Missing Targets",
                description="Provide at least one @mention or user ID.\nExample: `-z weightedwhitelist add @user 123456789`",
                color=discord.Color.red()
            ))
            return

        resolved_ids: list[int] = []
        failed: list[str] = []

        for raw in targets:
            if raw.startswith('<@') and raw.endswith('>'):
                try:
                    resolved_ids.append(int(raw.strip('<@!>')))
                except ValueError:
                    failed.append(f"`{raw}` (invalid mention)")
            else:
                try:
                    resolved_ids.append(int(raw))
                except ValueError:
                    failed.append(f"`{raw}` (not a valid ID or mention)")

        added, removed, skipped = [], [], []

        for uid in resolved_ids:
            if action == 'add':
                if uid in ww:
                    skipped.append(uid)
                else:
                    ww.append(uid)
                    added.append(uid)
            else:
                if uid not in ww:
                    skipped.append(uid)
                else:
                    ww.remove(uid)
                    removed.append(uid)

        an['weighted_whitelist'] = ww
        save_antinuke_config(ctx.guild.id, an)

        desc_parts = []
        if action == 'add':
            if added:
                desc_parts.append("**Added:**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in added))
            if skipped:
                desc_parts.append("**Already on list:**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in skipped))
        else:
            if removed:
                desc_parts.append("**Removed:**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in removed))
            if skipped:
                desc_parts.append("**Not on list:**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in skipped))
        if failed:
            desc_parts.append("**Failed:**\n" + "\n".join(failed))

        changed = bool(added or removed)
        await ctx.send(embed=discord.Embed(
            title="✅ Weighted Whitelist Updated" if changed else "⚠️ Nothing Changed",
            description="\n\n".join(desc_parts) or "No changes made.",
            color=discord.Color.green() if changed else discord.Color.orange()
        ))
        logger.info(f"{ctx.author} ran weightedwhitelist {action} in {ctx.guild.name}: added={added} removed={removed} skipped={skipped}")

    # ── Full Whitelist ───────────────────────────────────────────────────────

    @commands.command(name='whitelist')
    @is_authorized()
    async def whitelist(self, ctx, action: str, *targets: str):
        """
        Manage the antinuke full whitelist (completely immune users).
        Usage: -z whitelist add @user 123456789 @role
               -z whitelist remove @user 123456789
               -z whitelist list
        """
        action = action.lower()
        an = get_antinuke_config(ctx.guild.id)
        whitelist = an.get('whitelist', [])

        if action == 'list':
            if not whitelist:
                embed = discord.Embed(
                    title="📋 Antinuke Whitelist",
                    description="No users are currently whitelisted.",
                    color=discord.Color.blue()
                )
            else:
                lines = [f"<@{uid}> (`{uid}`)" for uid in whitelist]
                embed = discord.Embed(
                    title="📋 Antinuke Whitelist",
                    description="\n".join(lines),
                    color=discord.Color.blue()
                )
            await ctx.send(embed=embed)
            return

        if action not in ('add', 'remove'):
            embed = discord.Embed(
                title="❌ Invalid Action",
                description="Use `add`, `remove`, or `list`.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if not targets:
            embed = discord.Embed(
                title="❌ Missing Targets",
                description=(
                    "Please provide at least one user ID, @mention, or @role.\n"
                    "Example: `-z whitelist add @user 123456789 @SomeRole`"
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        resolved_ids: list[int] = []
        failed: list[str] = []

        for raw in targets:
            if raw.startswith('<@&') and raw.endswith('>'):
                try:
                    role_id = int(raw[3:-1])
                    role = ctx.guild.get_role(role_id)
                    if role:
                        for member in role.members:
                            if member.id not in resolved_ids:
                                resolved_ids.append(member.id)
                    else:
                        failed.append(f"`{raw}` (role not found)")
                except ValueError:
                    failed.append(f"`{raw}` (invalid role mention)")
            elif raw.startswith('<@') and raw.endswith('>'):
                try:
                    uid = int(raw.strip('<@!>'))
                    if uid not in resolved_ids:
                        resolved_ids.append(uid)
                except ValueError:
                    failed.append(f"`{raw}` (invalid mention)")
            else:
                try:
                    uid = int(raw)
                    if uid not in resolved_ids:
                        resolved_ids.append(uid)
                except ValueError:
                    failed.append(f"`{raw}` (not a valid ID or mention)")

        if not resolved_ids and failed:
            embed = discord.Embed(
                title="❌ No valid targets",
                description="\n".join(failed),
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        added:   list[int] = []
        removed: list[int] = []
        skipped: list[int] = []

        for uid in resolved_ids:
            if action == 'add':
                if uid in whitelist:
                    skipped.append(uid)
                else:
                    whitelist.append(uid)
                    added.append(uid)
            elif action == 'remove':
                if uid not in whitelist:
                    skipped.append(uid)
                else:
                    whitelist.remove(uid)
                    removed.append(uid)

        an['whitelist'] = whitelist
        save_antinuke_config(ctx.guild.id, an)

        if action == 'add':
            title = "✅ Whitelist Updated" if added else "⚠️ Nothing Changed"
            color = discord.Color.green() if added else discord.Color.orange()
            desc_parts = []
            if added:
                desc_parts.append(f"**Added ({len(added)}):**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in added))
            if skipped:
                desc_parts.append(f"**Already whitelisted ({len(skipped)}):**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in skipped))
        else:
            title = "✅ Whitelist Updated" if removed else "⚠️ Nothing Changed"
            color = discord.Color.green() if removed else discord.Color.orange()
            desc_parts = []
            if removed:
                desc_parts.append(f"**Removed ({len(removed)}):**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in removed))
            if skipped:
                desc_parts.append(f"**Not on whitelist ({len(skipped)}):**\n" + "\n".join(f"<@{u}> (`{u}`)" for u in skipped))

        if failed:
            desc_parts.append(f"**Failed to resolve ({len(failed)}):**\n" + "\n".join(failed))

        embed = discord.Embed(
            title=title,
            description="\n\n".join(desc_parts) or "No changes made.",
            color=color
        )
        await ctx.send(embed=embed)
        logger.info(
            f"{ctx.author} ran whitelist {action} in {ctx.guild.name}: "
            f"added={added} removed={removed} skipped={skipped} failed={failed}"
        )

    @commands.command(name='clearwhitelist')
    @is_authorized()
    async def clear_whitelist(self, ctx):
        """Remove all users from the antinuke full whitelist at once."""
        an = get_antinuke_config(ctx.guild.id)
        count = len(an.get('whitelist', []))

        if count == 0:
            embed = discord.Embed(
                title="⚠️ Whitelist Already Empty",
                description="There are no users currently on the whitelist.",
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)
            return

        an['whitelist'] = []
        save_antinuke_config(ctx.guild.id, an)

        embed = discord.Embed(
            title="✅ Whitelist Cleared",
            description=f"Removed **{count}** user(s) from the antinuke whitelist.",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} cleared the antinuke whitelist in {ctx.guild.name} ({count} user(s) removed)")

    @commands.command(name='antinukeinfo')
    @is_authorized()
    async def antinuke_info(self, ctx):
        """Show current antinuke configuration for this server."""
        an = get_antinuke_config(ctx.guild.id)

        enabled = an.get('enabled', False)
        quarantine_role_id = an.get('quarantine_role_id')
        log_channel_id = an.get('log_channel_id')
        whitelist = an.get('whitelist', [])
        weighted_whitelist = an.get('weighted_whitelist', [])
        weighted_role_id = an.get('weighted_whitelist_role_id')  # legacy, unused

        quarantine_role = ctx.guild.get_role(quarantine_role_id) if quarantine_role_id else None
        log_channel     = ctx.guild.get_channel(log_channel_id) if log_channel_id else None

        embed = discord.Embed(
            title="🛡️ AntiNuke Configuration",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(name="Status",          value="✅ Enabled" if enabled else "🔴 Disabled",                  inline=True)
        embed.add_field(name="Quarantine Role", value=quarantine_role.mention if quarantine_role else "❌ Not set", inline=True)
        embed.add_field(name="Log Channel",     value=log_channel.mention if log_channel else "❌ Not set",        inline=True)
        embed.add_field(
            name="Weighted Whitelist (50% higher thresholds)",
            value="\n".join(f"<@{uid}>" for uid in weighted_whitelist) if weighted_whitelist else "None",
            inline=False
        )
        embed.add_field(
            name="Fully Whitelisted Users",
            value="\n".join([f"<@{uid}>" for uid in whitelist]) if whitelist else "None",
            inline=False
        )
        embed.add_field(
            name="📋 Triggers (standard → weighted)",
            value=(
                "• `@everyone` pings: **3**/min/hr/day → **5**/min/hr/day\n"
                "• Channel deletions: **3**/min · **5**/hr · **7**/day → **5** · **8** · **11**\n"
                "• Role deletions: instant → **2nd** deletion\n"
                "• Role perm changes: instant → **2nd** change\n"
                "• Mass bans/kicks: **100**/min → **150**/min"
            ),
            inline=False
        )
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(AntiNukeCommands(bot))
    logger.info("✅ AntiNuke cog loaded")