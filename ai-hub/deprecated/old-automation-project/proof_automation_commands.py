"""
Proof Automation — Enable/Disable Command
==========================================
Single command to toggle the proof automation task on or off per server.

  -z prooftoggle        — toggle on/off for the current guild
  -z proofstatus        — show current enabled state

Only works in guilds that are configured in Tasks/proof_automation_tasks.py.
Requires Administrator or a Management role.
"""

import aiosqlite
import logging

import discord
from discord.ext import commands

from Tasks.proof_automation_tasks import GUILD_CONFIG

logger = logging.getLogger('discord')

LOCAL_DB         = "Database/roles.db"
MANAGEMENT_ROLES = ('Management', '007', '+')


def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        if {r.name for r in ctx.author.roles} & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure(
            "You need **Administrator** or a **Management** role to use this command."
        )
    return commands.check(predicate)


async def _ensure_schema(db):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_automation_state (
            guild_id             INTEGER PRIMARY KEY,
            creator_code_index   INTEGER DEFAULT 0,
            enabled              INTEGER DEFAULT 1
        )
    """)
    # Add enabled column if upgrading from older schema without it
    try:
        await db.execute("ALTER TABLE proof_automation_state ADD COLUMN enabled INTEGER DEFAULT 1")
    except Exception:
        pass  # Column already exists


async def _get_enabled(guild_id: int) -> bool:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            "INSERT OR IGNORE INTO proof_automation_state (guild_id, creator_code_index, enabled) VALUES (?, 0, 1)",
            (guild_id,)
        )
        await db.commit()
        async with db.execute(
            "SELECT enabled FROM proof_automation_state WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return bool(row[0]) if row else True


async def _set_enabled(guild_id: int, value: bool):
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            "INSERT OR IGNORE INTO proof_automation_state (guild_id, creator_code_index, enabled) VALUES (?, 0, 1)",
            (guild_id,)
        )
        await db.execute(
            "UPDATE proof_automation_state SET enabled=? WHERE guild_id=?",
            (int(value), guild_id)
        )
        await db.commit()


class ProofAutomationCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='prooftoggle')
    @is_authorized()
    async def proof_toggle(self, ctx):
        """Toggle proof automation on or off for this server."""
        if ctx.guild.id not in GUILD_CONFIG:
            await ctx.send(embed=discord.Embed(
                title="❌ Not Configured",
                description="This server is not set up for proof automation.",
                color=discord.Color.red()
            ))
            return

        current = await _get_enabled(ctx.guild.id)
        new_state = not current
        await _set_enabled(ctx.guild.id, new_state)

        cfg = GUILD_CONFIG[ctx.guild.id]
        if new_state:
            embed = discord.Embed(
                title="✅ Proof Automation Enabled",
                description=(
                    f"Proof automation is now **enabled** for **{cfg['name']}**.\n"
                    f"Watching <#{cfg['watch_channel_id']}> for image submissions."
                ),
                color=discord.Color.green()
            )
        else:
            embed = discord.Embed(
                title="🛑 Proof Automation Disabled",
                description=(
                    f"Proof automation is now **disabled** for **{cfg['name']}**.\n"
                    f"No replies, roles or stolen-proof checks until re-enabled.\n"
                    f"-# Image fingerprints are still collected so proofs posted "
                    f"now stay protected against future copying."
                ),
                color=discord.Color.red()
            )

        logger.info(
            f"[ProofAuto] {'Enabled' if new_state else 'Disabled'} by "
            f"{ctx.author} ({ctx.author.id}) in guild {ctx.guild.id}"
        )
        await ctx.send(embed=embed)

    @commands.command(name='proofautostatus')
    @is_authorized()
    async def proof_auto_status(self, ctx):
        """Show current proof automation status for this server."""
        if ctx.guild.id not in GUILD_CONFIG:
            await ctx.send(embed=discord.Embed(
                title="❌ Not Configured",
                description="This server is not set up for proof automation.",
                color=discord.Color.red()
            ))
            return

        enabled = await _get_enabled(ctx.guild.id)
        cfg = GUILD_CONFIG[ctx.guild.id]

        embed = discord.Embed(
            title="🤖 Proof Automation Status",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        embed.add_field(name="Server", value=cfg["name"], inline=True)
        embed.add_field(name="Status", value="✅ Enabled" if enabled else "🛑 Disabled", inline=True)
        embed.add_field(name="Watch channel", value=f"<#{cfg['watch_channel_id']}>", inline=True)
        embed.add_field(
            name="Active classes",
            value="\n".join(f"`{c}` — {['Following Only','Liking Only','Liking and Following','Need to press search on code proof','Using the creator code correctly','Zoom Out'][c]}" for c in cfg["active_classes"]),
            inline=False
        )
        embed.set_footer(text="Toggle with -z prooftoggle")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ProofAutomationCommands(bot))
    logger.info("✅ ProofAutomationCommands cog loaded")
