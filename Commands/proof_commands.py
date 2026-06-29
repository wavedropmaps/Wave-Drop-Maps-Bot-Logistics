"""
Proof Archival — Configuration Commands
========================================
Admin commands to configure the proof-archival system.

A designated "proof channel" is watched by Tasks/proof.py. When a user with
the "Role Giver" role (case-insensitive) replies to a message in that
channel, the bot downloads all attachments on the ORIGINAL (replied-to)
message into the local `proof_assets/` folder.

Config is stored in the LOCAL bot DB (Database/roles.db) under the
`proof_config` table. Schema is self-managed here (CREATE TABLE IF NOT
EXISTS) so this feature does not require touching database_improved.py.
"""

import aiosqlite
import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

LOCAL_DB = "Database/roles.db"
MANAGEMENT_ROLES = ('Management', '007', '+')

# Staff role that triggers a proof save when they reply to a message.
# Anyone with administrator permissions OR a role named "Staff" (case-insensitive)
# can trigger a save.
TRIGGER_ROLE_NAME = "Staff"


def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure(
            "You need **Administrator** or a **Management** role to use this command."
        )
    return commands.check(predicate)


async def _ensure_schema(db):
    """Idempotent schema creation — mirrored in Tasks/proof.py."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_config (
            guild_id       INTEGER PRIMARY KEY,
            channel_id     INTEGER,
            enabled        INTEGER DEFAULT 1,
            total_saved    INTEGER DEFAULT 0,
            last_saved_at  REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_saved_messages (
            guild_id    INTEGER NOT NULL,
            message_id  INTEGER NOT NULL,
            saved_at    REAL NOT NULL,
            file_count  INTEGER NOT NULL,
            PRIMARY KEY (guild_id, message_id)
        )
    """)


async def _ensure_row(db, guild_id: int):
    await db.execute("""
        INSERT OR IGNORE INTO proof_config
        (guild_id, channel_id, enabled, total_saved, last_saved_at)
        VALUES (?, NULL, 1, 0, NULL)
    """, (guild_id,))


async def _get_config(guild_id: int) -> dict:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        db.row_factory = aiosqlite.Row
        await _ensure_row(db, guild_id)
        await db.commit()
        async with db.execute(
            "SELECT * FROM proof_config WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
    return {
        'channel_id': row['channel_id'],
        'enabled': bool(row['enabled']),
        'total_saved': row['total_saved'] or 0,
        'last_saved_at': row['last_saved_at'],
    }


async def _set_field(guild_id: int, field: str, value):
    allowed = {'channel_id', 'enabled'}
    if field not in allowed:
        raise ValueError(f"Refusing to update unknown field: {field}")
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await _ensure_row(db, guild_id)
        await db.execute(
            f"UPDATE proof_config SET {field} = ? WHERE guild_id = ?",
            (value, guild_id)
        )
        await db.commit()


class ProofCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='setproofchannel')
    @is_authorized()
    async def set_proof_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel where Role Giver replies trigger a proof save."""
        if channel is None:
            await ctx.send(embed=discord.Embed(
                title="❌ Missing Argument",
                description="Usage: `-z setproofchannel #channel`",
                color=discord.Color.red()
            ))
            return

        await _set_field(ctx.guild.id, 'channel_id', channel.id)
        await _set_field(ctx.guild.id, 'enabled', 1)

        embed = discord.Embed(
            title="✅ Proof Channel Set",
            description=(
                f"Proof channel set to {channel.mention}.\n\n"
                f"When a member with **administrator permissions** or the **{TRIGGER_ROLE_NAME}** role replies to "
                "a message in this channel, the bot will download any "
                "attachments on the original message into `proof_assets/`.\n\n"
                "Clear with `-z clearproofchannel`.\n"
                "View config with `-z proofstatus`."
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(
            f"{ctx.author} set proof channel to #{channel} in {ctx.guild.name}"
        )

    @commands.command(name='clearproofchannel')
    @is_authorized()
    async def clear_proof_channel(self, ctx):
        """Clear the proof channel — disables proof archival for this guild."""
        await _set_field(ctx.guild.id, 'channel_id', None)
        embed = discord.Embed(
            title="✅ Proof Channel Cleared",
            description=(
                "Proof archival is now disabled for this guild. "
                "Re-enable with `-z setproofchannel #channel`."
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} cleared proof channel in {ctx.guild.name}")

    @commands.command(name='proofstatus')
    @is_authorized()
    async def proof_status(self, ctx):
        """Show current proof-archival configuration and stats."""
        cfg = await _get_config(ctx.guild.id)

        if cfg['channel_id']:
            ch = ctx.guild.get_channel(cfg['channel_id'])
            ch_str = ch.mention if ch else f"`{cfg['channel_id']}` (missing)"
        else:
            ch_str = "*Not set*"

        if cfg['last_saved_at']:
            last_dt = datetime.fromtimestamp(cfg['last_saved_at'], tz=timezone.utc)
            last = discord.utils.format_dt(last_dt, style='R')
        else:
            last = "*Never*"

        active = bool(cfg['channel_id'])
        embed = discord.Embed(
            title="📁 Proof Archival Status",
            color=discord.Color.green() if active else discord.Color.red()
        )
        embed.add_field(name="Active", value="✅ Yes" if active else "🛑 No", inline=True)
        embed.add_field(name="Channel", value=ch_str, inline=True)
        embed.add_field(
            name="Trigger",
            value=f"**Administrator** perms or **{TRIGGER_ROLE_NAME}** role (case-insensitive)",
            inline=False
        )
        embed.add_field(name="Total messages saved", value=str(cfg['total_saved']), inline=True)
        embed.add_field(name="Last save", value=last, inline=True)
        embed.set_footer(text="Files are saved to ./proof_assets/<guild>/<date>/<user>/")
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ProofCommands(bot))
    logger.info("✅ ProofCommands cog loaded")
