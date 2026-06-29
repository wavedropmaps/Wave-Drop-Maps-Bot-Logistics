import discord
from discord.ext import commands
import json
import os
import logging
import asyncio
import time
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import math
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger('discord')

CONFIG_PATH = "server_config.json"


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    with open(CONFIG_PATH, 'r') as f:
        content = f.read().strip()
        if not content:
            return {}
        return json.loads(content)


def get_antinuke_config(guild_id: int) -> dict:
    config = load_config()
    return config.get(str(guild_id), {}).get('antinuke', {})


# ==================== ACTION TRACKER ====================

class ActionTracker:
    """
    Tracks how many times each user has performed an action
    within 1-minute, 1-hour, and 1-day rolling windows.
    """
    def __init__(self):
        # {guild_id: {user_id: {action: [timestamps]}}}
        self._data: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    def record(self, guild_id: int, user_id: int, action: str):
        now = datetime.now(timezone.utc)
        self._data[guild_id][user_id][action].append(now)
        cutoff = now - timedelta(days=1)
        self._data[guild_id][user_id][action] = [
            t for t in self._data[guild_id][user_id][action] if t > cutoff
        ]

    def count_in_window(self, guild_id: int, user_id: int, action: str, seconds: int) -> int:
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=seconds)
        return sum(1 for t in self._data[guild_id][user_id][action] if t > cutoff)

    def clear_user(self, guild_id: int, user_id: int):
        if guild_id in self._data and user_id in self._data[guild_id]:
            del self._data[guild_id][user_id]


tracker = ActionTracker()

# (guild_id, user_id) -> time.monotonic() of the last quarantine fire.
# Suppresses burst double-fires (audit-log events arrive in clusters: one nuke
# can trigger several listeners for the same user within seconds) — but only
# for QUARANTINE_DEDUP_SECONDS. After that the key expires, so a user whom
# staff un-quarantine and who re-offends gets quarantined again instead of
# being immune for the rest of the bot's uptime.
QUARANTINE_DEDUP_SECONDS = 600  # 10 minutes
_recent_quarantines: dict = {}


# ==================== WHITELIST HELPERS ====================

def is_whitelisted(user_id: int, an_config: dict) -> bool:
    """Full whitelist — completely immune from quarantine."""
    return user_id in an_config.get('whitelist', [])


def is_weighted_whitelisted(member: discord.Member | None, an_config: dict) -> bool:
    """
    Weighted whitelist — specific users who get 50% higher thresholds before quarantine triggers.
    """
    if not member:
        return False
    return member.id in an_config.get('weighted_whitelist', [])


def get_threshold(base: int, member: discord.Member | None, an_config: dict) -> int:
    """
    Return the effective threshold for a user.
    Weighted whitelist role → ceil(base * 1.5).
    e.g. base 3 → 5, base 5 → 8, base 7 → 11, base 100 → 150
    """
    if is_weighted_whitelisted(member, an_config):
        return math.ceil(base * 1.5)
    return base


def is_antinuke_active(an_config: dict) -> bool:
    return an_config.get('enabled', False)


# ==================== QUARANTINE LOGIC ====================

async def quarantine_user(
    guild: discord.Guild,
    user: discord.Member | discord.User,
    reason: str,
    an_config: dict,
    bot
):
    """Apply quarantine role to a user after a 3 second delay, then log it."""
    key = (guild.id, user.id)
    now_mono = time.monotonic()
    last = _recent_quarantines.get(key)
    if last is not None and (now_mono - last) < QUARANTINE_DEDUP_SECONDS:
        return
    _recent_quarantines[key] = now_mono

    await asyncio.sleep(3)

    quarantine_role_id = an_config.get('quarantine_role_id')
    log_channel_id = an_config.get('log_channel_id')

    quarantine_role = guild.get_role(quarantine_role_id) if quarantine_role_id else None
    log_channel = guild.get_channel(log_channel_id) if log_channel_id else None

    member = guild.get_member(user.id)

    quarantine_success = False
    stripped_role_count = 0

    if member and quarantine_role:
        try:
            removable_roles = [r for r in member.roles if r != guild.default_role and r != quarantine_role]
            stripped_role_count = len(removable_roles)
            if removable_roles:
                await member.remove_roles(*removable_roles, reason="AntiNuke: stripping roles before quarantine")
            await member.add_roles(quarantine_role, reason=f"AntiNuke: {reason}")
            quarantine_success = True
            logger.info(f"[AntiNuke] Quarantined {member} in {guild.name} — stripped {stripped_role_count} role(s) — {reason}")
        except discord.Forbidden:
            logger.warning(f"[AntiNuke] Missing permissions to quarantine {user} in {guild.name}")
        except Exception as e:
            logger.error(f"[AntiNuke] Error quarantining {user}: {e}")
    elif not quarantine_role:
        logger.warning(f"[AntiNuke] No quarantine role set for {guild.name} — cannot quarantine {user}")

    # Wave-Logging dashboard event (antinuke tab)
    try:
        from utils.global_logger import log_event as _wl_event
        await _wl_event(
            category="antinuke",
            action="quarantine_triggered" if quarantine_success else "quarantine_attempted",
            target=user,
            guild=guild,
            details={
                "reason": reason,
                "stripped_role_count": stripped_role_count,
                "quarantine_role_set": bool(quarantine_role),
            },
        )
    except Exception:
        pass

    if log_channel:
        embed = discord.Embed(
            title="🚨 AntiNuke — User Quarantined",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="User", value=f"{user.mention} (`{user.id}`)", inline=True)
        embed.add_field(name="Action", value=reason, inline=True)
        embed.add_field(
            name="Quarantine Role",
            value=quarantine_role.mention if quarantine_role else "❌ Not configured",
            inline=True
        )
        embed.set_footer(text=f"Guild: {guild.name}")
        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            logger.error(f"[AntiNuke] Failed to send log: {e}")

    tracker.clear_user(guild.id, user.id)


# ==================== ANTINUKE TASK COG ====================

class AntiNukeTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── @everyone / @here pings ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild:
            return
        if message.author.bot:
            return
        if not message.mention_everyone:
            return

        an = get_antinuke_config(message.guild.id)
        if not is_antinuke_active(an):
            return
        if is_whitelisted(message.author.id, an):
            return

        member = message.guild.get_member(message.author.id)
        tracker.record(message.guild.id, message.author.id, 'everyone_ping')

        per_min  = tracker.count_in_window(message.guild.id, message.author.id, 'everyone_ping', 60)
        per_hour = tracker.count_in_window(message.guild.id, message.author.id, 'everyone_ping', 3600)
        per_day  = tracker.count_in_window(message.guild.id, message.author.id, 'everyone_ping', 86400)

        # Base: 3/min, 3/hr, 3/day  |  Weighted: 5/min, 5/hr, 5/day
        t_min  = get_threshold(3, member, an)
        t_hour = get_threshold(3, member, an)
        t_day  = get_threshold(3, member, an)

        if per_min >= t_min or per_hour >= t_hour or per_day >= t_day:
            reason = f"Excessive @everyone pings ({per_min}/min, {per_hour}/hr, {per_day}/day)"
            await quarantine_user(message.guild, message.author, reason, an, self.bot)

    # ── Channel deletions ────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel):
        guild = channel.guild
        an = get_antinuke_config(guild.id)
        if not is_antinuke_active(an):
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.channel_delete):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    user = entry.user
                    if is_whitelisted(user.id, an):
                        return

                    member = guild.get_member(user.id)
                    tracker.record(guild.id, user.id, 'channel_delete')
                    per_min  = tracker.count_in_window(guild.id, user.id, 'channel_delete', 60)
                    per_hour = tracker.count_in_window(guild.id, user.id, 'channel_delete', 3600)
                    per_day  = tracker.count_in_window(guild.id, user.id, 'channel_delete', 86400)

                    # Base: 3/min, 5/hr, 7/day  |  Weighted: 5/min, 8/hr, 11/day
                    t_min  = get_threshold(3, member, an)
                    t_hour = get_threshold(5, member, an)
                    t_day  = get_threshold(7, member, an)

                    if per_min >= t_min or per_hour >= t_hour or per_day >= t_day:
                        reason = f"Mass channel deletions ({per_min}/min, {per_hour}/hr, {per_day}/day)"
                        await quarantine_user(guild, user, reason, an, self.bot)
                    break
        except discord.Forbidden:
            logger.warning(f"[AntiNuke] No audit log access in {guild.name}")
        except Exception as e:
            logger.error(f"[AntiNuke] channel_delete error: {e}")

    # ── Role deletions ───────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role):
        guild = role.guild
        an = get_antinuke_config(guild.id)
        if not is_antinuke_active(an):
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.role_delete):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    user = entry.user
                    if is_whitelisted(user.id, an):
                        return

                    member = guild.get_member(user.id)
                    # Instant-quarantine by default.
                    # Weighted whitelist users get 1 free deletion (triggers on 2nd).
                    if is_weighted_whitelisted(member, an):
                        tracker.record(guild.id, user.id, 'role_delete')
                        if tracker.count_in_window(guild.id, user.id, 'role_delete', 60) < 2:
                            break

                    reason = f"Deleted role: {role.name}"
                    await quarantine_user(guild, user, reason, an, self.bot)
                    break
        except discord.Forbidden:
            logger.warning(f"[AntiNuke] No audit log access in {guild.name}")
        except Exception as e:
            logger.error(f"[AntiNuke] role_delete error: {e}")

    # ── Role permission changes ──────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role):
        guild = before.guild
        an = get_antinuke_config(guild.id)
        if not is_antinuke_active(an):
            return

        if before.permissions == after.permissions:
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.role_update):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    user = entry.user
                    if is_whitelisted(user.id, an):
                        return

                    member = guild.get_member(user.id)
                    # Instant-quarantine by default.
                    # Weighted whitelist users get 1 free perm change (triggers on 2nd).
                    if is_weighted_whitelisted(member, an):
                        tracker.record(guild.id, user.id, 'role_update')
                        if tracker.count_in_window(guild.id, user.id, 'role_update', 60) < 2:
                            break

                    reason = f"Modified permissions on role: {after.name}"
                    await quarantine_user(guild, user, reason, an, self.bot)
                    break
        except discord.Forbidden:
            logger.warning(f"[AntiNuke] No audit log access in {guild.name}")
        except Exception as e:
            logger.error(f"[AntiNuke] role_update error: {e}")

    # ── Mass bans ────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        an = get_antinuke_config(guild.id)
        if not is_antinuke_active(an):
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.ban):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    mod = entry.user
                    if is_whitelisted(mod.id, an):
                        return

                    mod_member = guild.get_member(mod.id)
                    tracker.record(guild.id, mod.id, 'ban')
                    per_min = tracker.count_in_window(guild.id, mod.id, 'ban', 60)

                    # Base: 100/min  |  Weighted: 150/min
                    t_min = get_threshold(100, mod_member, an)

                    if per_min >= t_min:
                        reason = f"Mass banning ({per_min} bans in 1 minute)"
                        await quarantine_user(guild, mod, reason, an, self.bot)
                    break
        except discord.Forbidden:
            logger.warning(f"[AntiNuke] No audit log access in {guild.name}")
        except Exception as e:
            logger.error(f"[AntiNuke] ban error: {e}")

    # ── Mass kicks ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        guild = member.guild
        an = get_antinuke_config(guild.id)
        if not is_antinuke_active(an):
            return

        await asyncio.sleep(1)
        try:
            async for entry in guild.audit_logs(limit=5, action=discord.AuditLogAction.kick):
                if (datetime.now(timezone.utc) - entry.created_at).total_seconds() < 10:
                    mod = entry.user
                    if is_whitelisted(mod.id, an):
                        return

                    mod_member = guild.get_member(mod.id)
                    tracker.record(guild.id, mod.id, 'kick')
                    per_min = tracker.count_in_window(guild.id, mod.id, 'kick', 60)

                    # Base: 100/min  |  Weighted: 150/min
                    t_min = get_threshold(100, mod_member, an)

                    if per_min >= t_min:
                        reason = f"Mass kicking ({per_min} kicks in 1 minute)"
                        await quarantine_user(guild, mod, reason, an, self.bot)
                    break
        except discord.Forbidden:
            pass
        except Exception as e:
            logger.error(f"[AntiNuke] kick error: {e}")


async def setup(bot):
    await bot.add_cog(AntiNukeTask(bot))
    logger.info("✅ AntiNukeTask cog loaded")