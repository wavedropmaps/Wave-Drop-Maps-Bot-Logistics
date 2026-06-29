import discord
import discord.abc

# ── Capture original send BEFORE any code can patch it ──────────────────────
# Both Member.send and User.send delegate to discord.abc.Messageable.send,
# so we grab that base method. The wrappers below let the worker call the
# unpatched send to actually hit Discord, bypassing our queue-routing patch.
_original_messageable_send = discord.abc.Messageable.send

async def _original_user_send(self, *args, **kwargs):
    return await _original_messageable_send(self, *args, **kwargs)

async def _original_member_send(self, *args, **kwargs):
    return await _original_messageable_send(self, *args, **kwargs)

from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio
import json
from datetime import datetime, timezone, timedelta

import Database.database_improved as database
from Tasks.priority_task import send_assignment_dm as send_priority_dm
from Tasks.contributor_task import send_assignment_dm as send_contributor_dm
from Tasks.streak_tasks import post_streak_info
from utils.logging import setup_logging
from database_backups import database_backup

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

# Setup centralized logging
setup_logging()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="-z ", intents=intents, help_command=None)

@bot.before_invoke
async def check_allowed_channel(ctx):
    """
    Global check to ensure commands are only executed in allowed channels.
    If no channels are configured, all channels are allowed.
    """
    # Skip check for DMs
    if ctx.guild is None:
        return

    # Cross-bot bridge: the Wave Management bot's automated commands (e.g. the surge
    # `-z removequeue` fired on completion) are trusted and exempt from the human-facing
    # channel allowlist, so the bridge works regardless of which channel they land in.
    if ctx.author.id == WAVE_MANAGEMENT_BOT_ID:
        return

    # Get allowed channels for this guild
    allowed_channels = await database.get_allowed_channels(ctx.guild.id)
    
    # If no channels are configured, all channels are allowed
    if not allowed_channels:
        return
    
    # Check if current channel is in allowed list
    channel_allowed = await database.is_channel_allowed(ctx.guild.id, ctx.channel.id)
    
    if not channel_allowed:
        # Get list of allowed channel mentions
        channel_mentions = []
        for channel_data in allowed_channels:
            channel_id = channel_data["channel_id"]
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                channel_mentions.append(channel.mention)
        
        if channel_mentions:
            allowed_list = ", ".join(channel_mentions)
            message = f"This command can only be used in: {allowed_list}"
        else:
            message = "This command cannot be used in this channel."
        
        # Send error message
        embed = discord.Embed(
            title="❌ Channel Not Allowed",
            description=message,
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        raise commands.CommandError("Channel not allowed")

async def send_dms_batched(tasks: list, batch_size: int = 10, delay: float = 1.0):
    """
    Sends DMs in batches of batch_size with a delay between each batch.
    Prevents hitting Discord rate limits when DMing large numbers of users.
    """
    for i in range(0, len(tasks), batch_size):
        batch = tasks[i:i + batch_size]
        await asyncio.gather(*batch, return_exceptions=True)
        if i + batch_size < len(tasks):
            await asyncio.sleep(delay)

@bot.event
async def on_command(ctx):
    from utils.logging import log_command
    log_command(ctx)

@bot.event
async def on_command_error(ctx, error):
    from utils.logging import log_command, log_error
    
    if isinstance(error, commands.CommandNotFound):
        if ctx.guild is not None:
            allowed_channels = await database.get_allowed_channels(ctx.guild.id)
            if allowed_channels:
                channel_allowed = await database.is_channel_allowed(ctx.guild.id, ctx.channel.id)
                if not channel_allowed:
                    channel_mentions = []
                    for channel_data in allowed_channels:
                        channel = ctx.guild.get_channel(channel_data["channel_id"])
                        if channel:
                            channel_mentions.append(channel.mention)
                    message = f"This command can only be used in: {', '.join(channel_mentions)}" if channel_mentions else "This command cannot be used in this channel."
                    embed = discord.Embed(
                        title="❌ Channel Not Allowed",
                        description=message,
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return
        embed = discord.Embed(
            title="❌ Unknown Command",
            description="That command doesn't exist!\n\nUse `-z help` to see all available commands.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        log_command(ctx, "Unknown command")
    elif isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            title="🚫 No Permission",
            description="You don't have permission to use this command.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
    else:
        log_error(ctx, error)

# Wave Management Bot — allowlisted for bot-to-bot command automation
# (e.g. the drop-map voting winner auto-runs `-z addmap`). Must match the
# same constant in Commands/map_commands.py.
WAVE_MANAGEMENT_BOT_ID = 1269188273201352768

@bot.event
async def on_message(message):
    # discord.py's process_commands() ignores ALL bot authors, so commands sent
    # by the Wave Management Bot would be silently dropped (no invoke, no error).
    # For that one allowlisted bot we build the context and invoke manually to
    # bypass the author.bot guard. Every other author keeps the normal path.
    if message.author.bot:
        if message.author.id == WAVE_MANAGEMENT_BOT_ID:
            ctx = await bot.get_context(message)
            await bot.invoke(ctx)
        return
    await bot.process_commands(message)

@bot.event
async def on_member_update(before, after):
    config_path = "server_config.json"
    if not os.path.exists(config_path):
        return
    with open(config_path, 'r') as f:
        content = f.read().strip()
        if not content:
            return
        config = json.loads(content)

    guild_id = str(after.guild.id)
    if guild_id not in config:
        return

    added_roles = set(after.roles) - set(before.roles)
    removed_roles = set(before.roles) - set(after.roles)

    priority_role_id = config[guild_id].get('priority_role_id')
    contributor_role_id = config[guild_id].get('contributor_role_id')

    for role in added_roles:
        if role.id == priority_role_id:
            existing = await database.get_tracked_role(after.guild.id, after.id, 'priority')
            if existing:
                await database.remove_tracked_role(after.guild.id, after.id, 'priority', member=after, guild_obj=after.guild, removal_reason="timer_reset")
                logging.info(f"Reset priority role timer for {after} in {after.guild.name}")
            await database.add_tracked_role(after.guild.id, after.id, 'priority', member=after, guild_obj=after.guild)
            await database.increment_streak(after.guild.id, after.id, 'priority')
            logging.info(f"Started tracking priority role for {after} in {after.guild.name}")
            await send_priority_dm(after, after.guild, 'priority')

        if role.id == contributor_role_id:
            existing = await database.get_tracked_role(after.guild.id, after.id, 'contributor')
            if existing:
                await database.remove_tracked_role(after.guild.id, after.id, 'contributor', member=after, guild_obj=after.guild, removal_reason="timer_reset")
                logging.info(f"Reset contributor role timer for {after} in {after.guild.name}")
            await database.add_tracked_role(after.guild.id, after.id, 'contributor', member=after, guild_obj=after.guild)
            await database.increment_streak(after.guild.id, after.id, 'contributor')
            logging.info(f"Started tracking contributor role for {after} in {after.guild.name}")
            await send_contributor_dm(after, after.guild, 'contributor')

    for role in removed_roles:
        if role.id == priority_role_id:
            await database.remove_tracked_role(after.guild.id, after.id, 'priority', member=after, guild_obj=after.guild, removal_reason="manual_removal")
            logging.info(f"Stopped tracking priority role for {after} — role manually removed")
        if role.id == contributor_role_id:
            await database.remove_tracked_role(after.guild.id, after.id, 'contributor', member=after, guild_obj=after.guild, removal_reason="manual_removal")
            logging.info(f"Stopped tracking contributor role for {after} — role manually removed")

async def startup_role_audit(guild, config):
    guild_id = str(guild.id)
    if guild_id not in config:
        return

    priority_role_id = config[guild_id].get('priority_role_id')
    contributor_role_id = config[guild_id].get('contributor_role_id')
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    missed = {}

    # Read up to 1000 entries, but in throttled batches. Discord serves audit
    # logs 100-per-request; firing those back-to-back floods the per-guild
    # bucket with 429s. We pause briefly every PAGE_SIZE entries so the
    # requests spread out instead of bursting.
    MAX_ENTRIES = 1000
    PAGE_SIZE   = 100        # one Discord audit-log page
    BATCH_PAUSE = 1.0        # seconds to wait between pages
    scanned = 0

    try:
        # Newest-first: when a server exceeds MAX_ENTRIES in 24h, we keep the
        # most RECENT changes (the ones that matter) rather than the oldest.
        async for entry in guild.audit_logs(
            action=discord.AuditLogAction.member_role_update,
            after=cutoff,
            limit=MAX_ENTRIES
        ):
            added_roles = getattr(entry.changes.after, 'roles', []) or []
            for role in added_roles:
                role_type = None
                if priority_role_id and role.id == priority_role_id:
                    role_type = 'priority'
                elif contributor_role_id and role.id == contributor_role_id:
                    role_type = 'contributor'
                if role_type:
                    # Keep the most recent assignment time — scan order is
                    # newest-first, so don't let an older entry overwrite it.
                    key = (entry.target.id, role_type)
                    prev = missed.get(key)
                    if prev is None or entry.created_at > prev:
                        missed[key] = entry.created_at

            scanned += 1
            # Pause at each page boundary to avoid bursting the audit-log API.
            if scanned % PAGE_SIZE == 0:
                await asyncio.sleep(BATCH_PAUSE)

    except discord.Forbidden:
        logging.warning(f"[Startup Audit] Missing audit log permission in {guild.name} — skipping")
        return

    found = 0
    for (user_id, role_type), assigned_at in missed.items():
        existing = await database.get_tracked_role(guild.id, user_id, role_type)
        if existing:
            continue

        member = guild.get_member(user_id)
        if not member:
            continue

        role_id = priority_role_id if role_type == 'priority' else contributor_role_id
        role_obj = guild.get_role(role_id)
        if not role_obj or role_obj not in member.roles:
            continue

        await database.add_tracked_role_with_time(guild.id, user_id, role_type, assigned_at, member=member, guild_obj=guild)
        logging.info(f"[Startup Audit] Caught missed {role_type} role for {member} in {guild.name} — assigned at {assigned_at}")
        found += 1

    logging.info(f"[Startup Audit] {guild.name} — {found} missed assignment(s) recovered")


_startup_done = False

@bot.event
async def on_ready():
    global _startup_done
    logging.info(f"Logged in as {bot.user}")
    logging.info(f"Bot is in {len(bot.guilds)} server(s)")

    # on_ready fires again on every full reconnect. The startup block below
    # (audit walks, slash sync, leaderboard/sticky posts) must run ONCE, or
    # each reconnect re-floods the audit-log API with 429s.
    if _startup_done:
        logging.info("on_ready fired again (reconnect) — skipping startup work.")
        return
    _startup_done = True

    config_path = "server_config.json"
    if os.path.exists(config_path):
        with open(config_path, 'r') as f:
            content = f.read().strip()
            config = json.loads(content) if content else {}

        logging.info("Running startup role audit...")
        for guild in bot.guilds:
            await startup_role_audit(guild, config)

        logging.info("Posting streak leaderboard...")
        for guild in bot.guilds:
            await post_streak_info(guild, config)

    logging.info("Syncing slash commands...")
    await bot.tree.sync()
    logging.info("Global slash command sync done.")
    for guild in bot.guilds:
        await bot.tree.sync(guild=guild)
        logging.info(f"Slash commands synced to {guild.name}")

    logging.info("Refreshing sticky messages for drop_map guilds...")
    from Commands.map_commands import refresh_sticky_message
    for guild in bot.guilds:
        try:
            queue_config = await database.get_server_queue_config(guild.id)
            if (queue_config
                    and queue_config.get("server_mode") == "drop_map"
                    and queue_config.get("queue_channel_id")):
                channel = guild.get_channel(queue_config["queue_channel_id"])
                if channel:
                    await refresh_sticky_message(channel, guild)
                    logging.info(f"Sticky message refreshed for {guild.name}")
        except Exception as e:
            logging.warning(f"Could not refresh sticky for {guild.name}: {e}")

    logging.info("Re-registering persistent HITL claim views...")
    try:
        from Database.database_improved import get_all_unresolved_hitl, get_db
        from Tasks.proof_automation_tasks import HITLClaimView

        # Release any stale claims from before the restart
        _db = await get_db()
        await _db.execute(
            "UPDATE hitl_claim_state SET claimed_by = NULL, claimed_at = NULL "
            "WHERE resolved = 0 AND claimed_by IS NOT NULL"
        )
        await _db.commit()

        unresolved = await get_all_unresolved_hitl()
        for row in unresolved:
            view = HITLClaimView(row['message_id'])
            bot.add_view(view, message_id=row['message_id'])
        logging.info(f"Re-registered {len(unresolved)} HITL claim view(s).")
    except Exception as e:
        logging.warning(f"HITL view re-registration failed: {e}")

    logging.info("Starting 24h database backup loop...")
    backup_task = asyncio.create_task(database_backup.schedule_backup_loop())
    bot.backup_task = backup_task
    logging.info("Backup loop started.")

    logging.info("Startup complete.")

# ── DMQueueCog resolver ────────────────────────────────────────────────────
# Three fallback paths so we can find the cog under any registered name and
# survive the dual-module-instance quirk (same as Wave Management Bot).
_resolver_diag_logged = {'get_cog': False, 'cogs_walk': False, 'sys_modules': False, 'all_failed': False}

def _resolve_dm_queue_cog():
    cog = bot.get_cog('DMQueueCog')
    if cog is not None:
        if not _resolver_diag_logged['get_cog']:
            logging.info("[DMQueue:RESOLVE] First success via bot.get_cog")
            _resolver_diag_logged['get_cog'] = True
        return cog

    for c in bot.cogs.values():
        if type(c).__name__ == 'DMQueueCog':
            if not _resolver_diag_logged['cogs_walk']:
                logging.warning("[DMQueue:RESOLVE] get_cog returned None — found via bot.cogs walk")
                _resolver_diag_logged['cogs_walk'] = True
            return c

    import sys
    mod = sys.modules.get('Tasks.dm_queue')
    if mod is not None:
        inst = getattr(mod, '_dm_queue_instance', None)
        if inst is not None:
            if not _resolver_diag_logged['sys_modules']:
                logging.warning("[DMQueue:RESOLVE] Falling back to sys.modules singleton")
                _resolver_diag_logged['sys_modules'] = True
            return inst

    if not _resolver_diag_logged['all_failed']:
        logging.error(f"[DMQueue:RESOLVE] All resolver paths failed. bot.cogs keys: {list(bot.cogs.keys())}")
        _resolver_diag_logged['all_failed'] = True
    return None


# ── Monkey-patch User.send and Member.send to route through the shared queue ──
# Both bots' shared dm_queue.db handles load balancing; whichever bot has
# capacity picks up the DM. Returns None immediately (queued async). No code
# in this bot relies on the return value of user.send() / member.send().
async def _patched_user_send(self, content=None, **kwargs):
    inst = _resolve_dm_queue_cog()
    if inst is not None:
        inst.enqueue(self, content=content, **kwargs)
        return None
    # Fallback: cog not loaded yet (very early startup, before extensions complete)
    logging.warning(f"[DMQueue] PATCH_FALLBACK: cog not loaded, sending directly to {self}")
    return await _original_user_send(self, content=content, **kwargs)

async def _patched_member_send(self, content=None, **kwargs):
    inst = _resolve_dm_queue_cog()
    if inst is not None:
        inst.enqueue(self, content=content, **kwargs)
        return None
    logging.warning(f"[DMQueue] PATCH_FALLBACK: cog not loaded, sending directly to {self}")
    return await _original_member_send(self, content=content, **kwargs)

discord.User.send   = _patched_user_send
discord.Member.send = _patched_member_send
# Set the guard flag so the legacy dm_handling.py _patch_send() (if still
# present) sees a patch is already applied and skips its own.
discord.User._dm_logging_patched = True
print("[OK] DM queue-routing monkey-patch applied (User.send + Member.send)")


async def main():
    async with bot:
        await database.init_db()
        await database_backup.initialize_backup_system()
        # Load dm_queue FIRST so the cog is available before any other code
        # can call user.send() (which now routes through it via the monkey-patch).
        await bot.load_extension("Tasks.dm_queue")  # Shared DM queue + load balancing
        await bot.load_extension("Commands.priority_commands")
        await bot.load_extension("Commands.contributor_commands")
        await bot.load_extension("Commands.member_commands")
        await bot.load_extension("Commands.manual_control")
        await bot.load_extension("Commands.antinuke_commands")
        await bot.load_extension("Commands.map_commands")  # Added map request commands
        await bot.load_extension("Commands.streak_commands")
        await bot.load_extension("Commands.help")
        await bot.load_extension("Commands.local_server_config")  # Added allowed channels config
        await bot.load_extension("Commands.dm_commands")  # Added DM configuration commands
        await bot.load_extension("Commands.auto_join_ping_commands")  # Auto-join ghost-ping config commands
        await bot.load_extension("Commands.proof_commands")  # Proof archival configuration commands
        await bot.load_extension("Tasks.dm_handling")
        await bot.load_extension("Tasks.dm_processor")  # Added DM message listener and processor
        await bot.load_extension("Tasks.auto_reply")  # Sticky-note auto-reply (fires when reply_dm_duty has armed a note)
        await bot.load_extension("Tasks.antinuke_tasks")
        await bot.load_extension("Tasks.priority_task")
        await bot.load_extension("Tasks.contributor_task")
        await bot.load_extension("Tasks.streak_tasks")
        await bot.load_extension("Tasks.auto_join_ping_task")  # Auto-join ghost-ping cross-bot coordination
        await bot.load_extension("Tasks.auto_cleanup_task")  # Auto-cleanup ghost-ping messages
        await bot.load_extension("Tasks.proof")  # Proof archival background listener
        await bot.load_extension("Tasks.proof_automation_tasks")  # Auto-detect proof images via YOLO
        await bot.load_extension("Commands.proof_automation_commands")  # Enable/disable proof automation
        await bot.load_extension("Commands.review_queue_commands")  # Clear stale/bugged HITL reviews from the queue
        await bot.load_extension("Tasks.wave_logging")  # Global logging → pushes to Wave-Logging repo
        await bot.load_extension("Tasks.vouch_dming")  # Vouch channel DM enforcement
        await bot.load_extension("Tasks.surge_bridge")  # Surge cross-bot dispatch → Management surge-maps channel
        await bot.load_extension("Tasks.loot_bridge")   # Loot cross-bot dispatch → Management maps-not-taken channel
        await bot.start(TOKEN)

if __name__ == "__main__":
    asyncio.run(main())