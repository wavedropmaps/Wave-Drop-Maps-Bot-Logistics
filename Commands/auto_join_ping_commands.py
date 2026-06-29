"""
Auto Join Ghost-Ping — Configuration Commands (Wave Logistics Bot only)
========================================================================
Admin-facing commands to configure the ghost-ping-on-join system.

Config is stored in the SHARED SQLite DB (dm_shared_queue.db) under the
`tippy_join_config` table. The actual ghost-ping work lives in
Tasks/auto_join_ping_task.py and runs on BOTH bots — they coordinate via
the `join_ping_claims` table.
"""

import asyncio
import aiosqlite
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands

logger = logging.getLogger('discord')

SHARED_DB = "C:/Users/kiere/Desktop/dm_shared_queue.db"
MANAGEMENT_ROLES = ('Management', '007', '+')

DEFAULT_DELETE_DELAY_MS = 1000
DEFAULT_BATCH_WINDOW_MS = 800
DEFAULT_REJOIN_COOLDOWN_SECONDS = 60

DELAY_MIN_MS = 100
DELAY_MAX_MS = 5000
BATCH_MIN_MS = 100
BATCH_MAX_MS = 5000
COOLDOWN_MIN_SECONDS = 0
COOLDOWN_MAX_SECONDS = 86400


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


async def _ensure_row(db, guild_id: int):
    await db.execute("""
        INSERT OR IGNORE INTO tippy_join_config
        (guild_id, enabled, channel_ids, log_channel_id,
         delete_delay_ms, batch_window_ms, rejoin_cooldown_seconds,
         total_pings, total_batches, last_join_at)
        VALUES (?, 0, '[]', NULL, ?, ?, ?, 0, 0, NULL)
    """, (
        guild_id,
        DEFAULT_DELETE_DELAY_MS,
        DEFAULT_BATCH_WINDOW_MS,
        DEFAULT_REJOIN_COOLDOWN_SECONDS,
    ))


async def _ensure_schema(db):
    """Idempotent schema creation — mirrors auto_join_ping_task._init_db."""
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS tippy_join_config (
            guild_id                INTEGER PRIMARY KEY,
            enabled                 INTEGER DEFAULT 0,
            channel_ids             TEXT DEFAULT '[]',
            log_channel_id          INTEGER,
            delete_delay_ms         INTEGER DEFAULT 1000,
            batch_window_ms         INTEGER DEFAULT 800,
            rejoin_cooldown_seconds INTEGER DEFAULT 60,
            total_pings             INTEGER DEFAULT 0,
            total_batches           INTEGER DEFAULT 0,
            last_join_at            REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS join_ping_claims (
            member_id         INTEGER NOT NULL,
            guild_id          INTEGER NOT NULL,
            claimed_by_bot_id INTEGER NOT NULL,
            claimed_at        REAL NOT NULL,
            PRIMARY KEY (member_id, guild_id)
        )
    """)


async def _get_config(guild_id: int) -> dict:
    async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        db.row_factory = aiosqlite.Row
        await _ensure_row(db, guild_id)
        await db.commit()
        async with db.execute(
            "SELECT * FROM tippy_join_config WHERE guild_id=?", (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
    try:
        channel_ids = json.loads(row['channel_ids'] or '[]')
    except json.JSONDecodeError:
        channel_ids = []
    return {
        'enabled': bool(row['enabled']),
        'channel_ids': channel_ids,
        'log_channel_id': row['log_channel_id'],
        'delete_delay_ms': row['delete_delay_ms'] or DEFAULT_DELETE_DELAY_MS,
        'batch_window_ms': row['batch_window_ms'] or DEFAULT_BATCH_WINDOW_MS,
        'rejoin_cooldown_seconds': (
            row['rejoin_cooldown_seconds'] or DEFAULT_REJOIN_COOLDOWN_SECONDS
        ),
        'total_pings': row['total_pings'] or 0,
        'total_batches': row['total_batches'] or 0,
        'last_join_at': row['last_join_at'],
    }


async def _set_field(guild_id: int, field: str, value):
    allowed = {
        'enabled', 'channel_ids', 'log_channel_id',
        'delete_delay_ms', 'batch_window_ms', 'rejoin_cooldown_seconds',
    }
    if field not in allowed:
        raise ValueError(f"Refusing to update unknown field: {field}")
    async with aiosqlite.connect(SHARED_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await _ensure_row(db, guild_id)
        await db.execute(
            f"UPDATE tippy_join_config SET {field} = ? WHERE guild_id = ?",
            (value, guild_id)
        )
        await db.commit()


def _check_required_perms(channel: discord.TextChannel) -> dict:
    me = channel.guild.me
    if me is None:
        return {"view": False, "send": False, "manage": False}
    perms = channel.permissions_for(me)
    return {
        "view": perms.view_channel,
        "send": perms.send_messages,
        "manage": perms.manage_messages,
    }


class AutoJoinPingCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── enable / disable ───────────────────────────────────────────────────

    @commands.command(name='enableautojoinping')
    @is_authorized()
    async def enable_auto_join_ping(self, ctx):
        """Enable the ghost-ping-on-join system for this server."""
        cfg = await _get_config(ctx.guild.id)
        await _set_field(ctx.guild.id, 'enabled', 1)

        watched_count = len(cfg['channel_ids'])
        next_steps = (
            "**Next steps:**\n"
            "`-z autojoinpingadd #channel` — channels to ghost-ping (required)\n"
            "`-z autojoinpinglogchannel #channel` — optional cleanup audit log\n"
            "`-z autojoinpingstatus` — view config + stats\n"
            "`-z autojoinpingtest` — simulate a join on yourself"
        )
        warn = ""
        if watched_count == 0:
            warn = "\n\n⚠️ No channels configured — add at least one with `-z autojoinpingadd #channel`."

        embed = discord.Embed(
            title="✅ Auto Join Ghost-Ping Enabled",
            description=(
                "When a new member joins, they will be ghost-pinged in the "
                "configured channels (mention + immediate delete).\n\n"
                + next_steps + warn
            ),
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} enabled auto join ping in {ctx.guild.name}")

    @commands.command(name='disableautojoinping')
    @is_authorized()
    async def disable_auto_join_ping(self, ctx):
        """Disable the ghost-ping-on-join system for this server."""
        await _set_field(ctx.guild.id, 'enabled', 0)
        embed = discord.Embed(
            title="🛑 Auto Join Ghost-Ping Disabled",
            description=(
                "New members will no longer be ghost-pinged. Configured "
                "channels are preserved — re-enable with `-z enableautojoinping`."
            ),
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        logger.info(f"{ctx.author} disabled auto join ping in {ctx.guild.name}")

    # ── watched channels ───────────────────────────────────────────────────

    @commands.command(name='autojoinpingadd')
    @is_authorized()
    async def auto_join_ping_add(self, ctx, *channels: discord.TextChannel):
        """Add one or more channels where new members will be ghost-pinged."""
        if not channels:
            await ctx.send(embed=discord.Embed(
                title="❌ No Channels Provided",
                description="Usage: `-z autojoinpingadd #channel [#channel2 ...]`",
                color=discord.Color.red()
            ))
            return

        cfg = await _get_config(ctx.guild.id)
        watched = list(cfg['channel_ids'])
        added, already, perm_warnings = [], [], []

        for ch in channels:
            if ch.id in watched:
                already.append(ch)
                continue
            watched.append(ch.id)
            added.append(ch)
            perms = _check_required_perms(ch)
            missing = []
            if not perms['view']: missing.append("View Channel")
            if not perms['send']: missing.append("Send Messages")
            if not perms['manage']: missing.append("Manage Messages")
            if missing:
                perm_warnings.append(f"{ch.mention} — missing: {', '.join(missing)}")

        await _set_field(ctx.guild.id, 'channel_ids', json.dumps(watched))

        embed = discord.Embed(title="✅ Ghost-Ping Channels Updated", color=discord.Color.green())
        if added:
            embed.add_field(name="Added", value=", ".join(c.mention for c in added), inline=False)
        if already:
            embed.add_field(name="Already configured", value=", ".join(c.mention for c in already), inline=False)
        embed.add_field(name="Total channels", value=str(len(watched)), inline=False)
        if perm_warnings:
            embed.add_field(
                name="⚠️ Permission warnings",
                value="\n".join(perm_warnings),
                inline=False
            )
            embed.color = discord.Color.orange()
        await ctx.send(embed=embed)
        logger.info(
            f"{ctx.author} added {len(added)} channel(s) to auto join ping in {ctx.guild.name}"
        )

    @commands.command(name='autojoinpingremove')
    @is_authorized()
    async def auto_join_ping_remove(self, ctx, *channels: discord.TextChannel):
        """Remove one or more channels from the ghost-ping list."""
        if not channels:
            await ctx.send(embed=discord.Embed(
                title="❌ No Channels Provided",
                description="Usage: `-z autojoinpingremove #channel [#channel2 ...]`",
                color=discord.Color.red()
            ))
            return

        cfg = await _get_config(ctx.guild.id)
        watched = list(cfg['channel_ids'])
        removed, not_present = [], []

        for ch in channels:
            if ch.id in watched:
                watched.remove(ch.id)
                removed.append(ch)
            else:
                not_present.append(ch)

        await _set_field(ctx.guild.id, 'channel_ids', json.dumps(watched))

        embed = discord.Embed(title="✅ Ghost-Ping Channels Updated", color=discord.Color.green())
        if removed:
            embed.add_field(name="Removed", value=", ".join(c.mention for c in removed), inline=False)
        if not_present:
            embed.add_field(name="Not in list", value=", ".join(c.mention for c in not_present), inline=False)
        embed.add_field(name="Total channels", value=str(len(watched)), inline=False)
        await ctx.send(embed=embed)
        logger.info(
            f"{ctx.author} removed {len(removed)} channel(s) from auto join ping in {ctx.guild.name}"
        )

    @commands.command(name='autojoinpinglist')
    @is_authorized()
    async def auto_join_ping_list(self, ctx):
        """List all configured ghost-ping channels and their permission status."""
        cfg = await _get_config(ctx.guild.id)
        if not cfg['channel_ids']:
            await ctx.send(embed=discord.Embed(
                title="📋 Ghost-Ping Channels",
                description="No channels configured. Use `-z autojoinpingadd #channel`.",
                color=discord.Color.blurple()
            ))
            return

        lines = []
        for cid in cfg['channel_ids']:
            ch = ctx.guild.get_channel(cid)
            if ch is None:
                lines.append(f"⚠️ Unknown channel `{cid}` (deleted?)")
                continue
            p = _check_required_perms(ch)
            view_m = "✅" if p['view'] else "❌"
            send_m = "✅" if p['send'] else "❌"
            mng_m = "✅" if p['manage'] else "❌"
            lines.append(
                f"{ch.mention} — View {view_m} · Send {send_m} · Manage Messages {mng_m}"
            )

        embed = discord.Embed(
            title=f"📋 Ghost-Ping Channels ({len(cfg['channel_ids'])})",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="❌ on Manage Messages will cause the ghost ping to stay visible")
        await ctx.send(embed=embed)

    # ── log channel ────────────────────────────────────────────────────────

    @commands.command(name='autojoinpinglogchannel')
    @is_authorized()
    async def auto_join_ping_log_channel(self, ctx, channel: str = None):
        """Set or clear the channel where ghost-ping events are logged."""
        if channel is None:
            await ctx.send(embed=discord.Embed(
                title="❌ Missing Argument",
                description=(
                    "Usage: `-z autojoinpinglogchannel #channel`\n"
                    "Or to clear: `-z autojoinpinglogchannel none`"
                ),
                color=discord.Color.red()
            ))
            return

        if channel.lower() == "none":
            await _set_field(ctx.guild.id, 'log_channel_id', None)
            await ctx.send(embed=discord.Embed(
                title="✅ Log Channel Cleared",
                description="Ghost-ping events will no longer be logged to a channel.",
                color=discord.Color.green()
            ))
            return

        converter = commands.TextChannelConverter()
        try:
            target = await converter.convert(ctx, channel)
        except commands.BadArgument:
            await ctx.send(embed=discord.Embed(
                title="❌ Invalid Channel",
                description="Could not find that channel. Mention with `#channel-name` or use `none`.",
                color=discord.Color.red()
            ))
            return

        await _set_field(ctx.guild.id, 'log_channel_id', target.id)
        await ctx.send(embed=discord.Embed(
            title="✅ Log Channel Set",
            description=(
                f"Ghost-ping events will be logged in {target.mention}.\n"
                "Use `-z autojoinpinglogchannel none` to clear."
            ),
            color=discord.Color.green()
        ))
        logger.info(
            f"{ctx.author} set auto join ping log channel to #{target} in {ctx.guild.name}"
        )

    # ── tuning ─────────────────────────────────────────────────────────────

    @commands.command(name='autojoinpingdelay')
    @is_authorized()
    async def auto_join_ping_delay(self, ctx, ms: int = None):
        """Set how long to wait between send and delete (notification window)."""
        if ms is None:
            cfg = await _get_config(ctx.guild.id)
            await ctx.send(
                f"Current delete delay: **{cfg['delete_delay_ms']} ms**. "
                f"Usage: `-z autojoinpingdelay <ms>` "
                f"({DELAY_MIN_MS}-{DELAY_MAX_MS})."
            )
            return
        if ms < DELAY_MIN_MS or ms > DELAY_MAX_MS:
            await ctx.send(
                f"❌ `ms` must be between {DELAY_MIN_MS} and {DELAY_MAX_MS}."
            )
            return
        await _set_field(ctx.guild.id, 'delete_delay_ms', ms)
        await ctx.send(f"✅ Delete delay set to **{ms} ms**.")

    @commands.command(name='autojoinpingbatch')
    @is_authorized()
    async def auto_join_ping_batch(self, ctx, ms: int = None):
        """Set the batch window — how long to wait gathering join events before sending."""
        if ms is None:
            cfg = await _get_config(ctx.guild.id)
            await ctx.send(
                f"Current batch window: **{cfg['batch_window_ms']} ms**. "
                f"Usage: `-z autojoinpingbatch <ms>` "
                f"({BATCH_MIN_MS}-{BATCH_MAX_MS})."
            )
            return
        if ms < BATCH_MIN_MS or ms > BATCH_MAX_MS:
            await ctx.send(
                f"❌ `ms` must be between {BATCH_MIN_MS} and {BATCH_MAX_MS}."
            )
            return
        await _set_field(ctx.guild.id, 'batch_window_ms', ms)
        await ctx.send(f"✅ Batch window set to **{ms} ms**.")

    @commands.command(name='autojoinpingcooldown')
    @is_authorized()
    async def auto_join_ping_cooldown(self, ctx, seconds: int = None):
        """Set the rejoin cooldown — don't re-ping a member within this many seconds."""
        if seconds is None:
            cfg = await _get_config(ctx.guild.id)
            await ctx.send(
                f"Current rejoin cooldown: **{cfg['rejoin_cooldown_seconds']} s**. "
                f"Usage: `-z autojoinpingcooldown <seconds>` "
                f"({COOLDOWN_MIN_SECONDS}-{COOLDOWN_MAX_SECONDS})."
            )
            return
        if seconds < COOLDOWN_MIN_SECONDS or seconds > COOLDOWN_MAX_SECONDS:
            await ctx.send(
                f"❌ `seconds` must be between {COOLDOWN_MIN_SECONDS} and "
                f"{COOLDOWN_MAX_SECONDS}."
            )
            return
        await _set_field(ctx.guild.id, 'rejoin_cooldown_seconds', seconds)
        await ctx.send(f"✅ Rejoin cooldown set to **{seconds} s**.")

    # ── status ─────────────────────────────────────────────────────────────

    @commands.command(name='autojoinpingstatus')
    @is_authorized()
    async def auto_join_ping_status(self, ctx):
        """Show current ghost-ping configuration and stats."""
        cfg = await _get_config(ctx.guild.id)

        if cfg['channel_ids']:
            ch_lines = []
            for cid in cfg['channel_ids']:
                ch = ctx.guild.get_channel(cid)
                ch_lines.append(ch.mention if ch else f"`{cid}` (missing)")
            ch_str = ", ".join(ch_lines)
        else:
            ch_str = "*None*"

        if cfg['log_channel_id']:
            log_ch = ctx.guild.get_channel(cfg['log_channel_id'])
            log_str = log_ch.mention if log_ch else f"`{cfg['log_channel_id']}` (missing)"
        else:
            log_str = "*Not set*"

        if cfg['last_join_at']:
            last_dt = datetime.fromtimestamp(cfg['last_join_at'], tz=timezone.utc)
            last = discord.utils.format_dt(last_dt, style='R')
        else:
            last = "*Never*"

        embed = discord.Embed(
            title="🧹 Auto Join Ghost-Ping Status",
            color=discord.Color.green() if cfg['enabled'] else discord.Color.red()
        )
        embed.add_field(name="Enabled", value="✅ Yes" if cfg['enabled'] else "🛑 No", inline=True)
        embed.add_field(name="Channels", value=str(len(cfg['channel_ids'])), inline=True)
        embed.add_field(name="Log channel", value=log_str, inline=True)
        embed.add_field(name="Watched", value=ch_str, inline=False)
        embed.add_field(
            name="Timing",
            value=(
                f"Batch window: **{cfg['batch_window_ms']} ms**\n"
                f"Delete delay: **{cfg['delete_delay_ms']} ms**\n"
                f"Rejoin cooldown: **{cfg['rejoin_cooldown_seconds']} s**"
            ),
            inline=False
        )
        embed.add_field(name="Total members pinged", value=str(cfg['total_pings']), inline=True)
        embed.add_field(name="Total batches sent", value=str(cfg['total_batches']), inline=True)
        embed.add_field(name="Last join handled", value=last, inline=True)
        await ctx.send(embed=embed)

    # ── test ───────────────────────────────────────────────────────────────

    @commands.command(name='autojoinpingtest')
    @is_authorized()
    async def auto_join_ping_test(self, ctx, member: Optional[discord.Member] = None):
        """Simulate a member-join ghost ping in ALL configured channels.

        Bypasses the cross-bot claim system so admins can verify locally.
        Mirrors the real on_member_join flow: pings in every configured channel
        with the configured delete delay.
        """
        target = member or ctx.author
        cfg = await _get_config(ctx.guild.id)
        if not cfg['enabled']:
            await ctx.send("⚠️ System is disabled. Run `-z enableautojoinping` first.")
            return
        if not cfg['channel_ids']:
            await ctx.send("⚠️ No channels configured. Run `-z autojoinpingadd #channel` first.")
            return

        # Resolve all configured channels + check permissions up front
        resolved: list[discord.TextChannel] = []
        missing_channels: list[str] = []
        perm_errors: list[str] = []

        for cid in cfg['channel_ids']:
            ch = ctx.guild.get_channel(cid)
            if ch is None or not isinstance(ch, discord.TextChannel):
                missing_channels.append(f"`{cid}`")
                continue
            perms = _check_required_perms(ch)
            missing = []
            if not perms['send']: missing.append("Send Messages")
            if not perms['manage']: missing.append("Manage Messages")
            if missing:
                perm_errors.append(f"{ch.mention} — missing: {', '.join(missing)}")
                continue
            resolved.append(ch)

        if not resolved:
            await ctx.send(
                "❌ Cannot test — no usable channels.\n"
                + (f"Missing: {', '.join(missing_channels)}\n" if missing_channels else "")
                + ("\n".join(perm_errors) if perm_errors else "")
            )
            return

        await ctx.send(
            f"🧪 Sending test ghost-ping to {target.mention} in "
            f"**{len(resolved)}** channel(s): "
            f"{', '.join(c.mention for c in resolved)}\n"
            f"Using **{cfg['delete_delay_ms']}ms** delete delay per channel."
            + (f"\n⚠️ Skipped: {', '.join(perm_errors)}" if perm_errors else "")
        )

        # Ping each channel in parallel — same as the real batch worker
        async def _ghost_ping(channel: discord.TextChannel) -> tuple[discord.TextChannel, bool, str]:
            try:
                msg = await channel.send(
                    content=f"<@{target.id}>",
                    allowed_mentions=discord.AllowedMentions(
                        users=True, everyone=False, roles=False, replied_user=False
                    )
                )
            except discord.HTTPException as e:
                return channel, False, f"send failed: `{e}`"

            await asyncio.sleep(cfg['delete_delay_ms'] / 1000.0)

            try:
                await msg.delete()
                return channel, True, ""
            except discord.NotFound:
                return channel, True, ""
            except discord.HTTPException as e:
                return channel, False, f"delete failed: `{e}` (msg_id=`{msg.id}`)"

        results = await asyncio.gather(
            *(_ghost_ping(ch) for ch in resolved),
            return_exceptions=False
        )

        successes = [ch for ch, ok, _ in results if ok]
        failures = [(ch, err) for ch, ok, err in results if not ok]

        lines = []
        if successes:
            lines.append(
                f"✅ Test complete in **{len(successes)}** channel(s): "
                f"{', '.join(c.mention for c in successes)}"
            )
            lines.append(
                f"Check if {target.mention} sees unread notifications on those channels."
            )
        if failures:
            lines.append("⚠️ Failures:")
            for ch, err in failures:
                lines.append(f"  • {ch.mention} — {err}")

        await ctx.send("\n".join(lines))


async def setup(bot):
    await bot.add_cog(AutoJoinPingCommands(bot))
    logger.info("✅ AutoJoinPingCommands cog loaded")
