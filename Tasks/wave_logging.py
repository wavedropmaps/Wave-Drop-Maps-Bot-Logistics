"""
Wave Logging — Wave Logistics Bot side

This cog is the heaviest one in the new logging system because Logistics
is the designated server watcher. It captures:

  Bot-side events (category = "logistics" via BOT_NAME)
    • slash + prefix command completion
    • command + app-command errors
    • bot lifecycle (ready, disconnect, resume)

  Server / guild events (category = various, bot = "server")
    • member joins / leaves
    • bans / kicks / timeouts / unbans
    • role changes (added / removed)
    • nickname changes
    • channel create / delete / edit
    • role create / delete / update
    • voice activity (join / leave / mute / deafen / move)
    • soundboard plays
    • message deletes / edits
    • server (guild) settings updates
    • emoji / sticker updates
    • invite create / delete

Plus a 15-min push loop and nightly rollup. Plus startup audit replay
that backfills events from the audit log for the last 24 h so that
anything that happened while the bot was offline still lands in the
website.
"""

import asyncio
import logging
import traceback
from datetime import datetime, time, timedelta, timezone
from typing import Optional

import discord
from discord import AuditLogAction, app_commands
from discord.ext import commands, tasks

from utils.global_logger import (
    BOT_NAME,
    ensure_table,
    fetch_audit,
    install_terminal_log_capture,
    log_event,
    serialize_channel,
    serialize_guild_full,
    serialize_message,
    serialize_role,
    serialize_user,
    serialize_voice_state,
)

logger = logging.getLogger("discord")

# Constant string we pass as `bot=` for server-watcher events. Routes
# them under data/server/<category>/... on the website.
SERVER_BOT = "server"


def _snapshot_sound(sound) -> Optional[dict]:
    """Light serializer for soundboard sounds — discord.py doesn't expose
    a stable type yet, so we duck-type the obvious fields."""
    if sound is None:
        return None
    payload: dict = {
        "id":        str(getattr(sound, "id", "")),
        "name":      getattr(sound, "name", None),
        "volume":    getattr(sound, "volume", None),
        "emoji":     str(getattr(sound, "emoji", None)) if getattr(sound, "emoji", None) else None,
        "available": getattr(sound, "available", None),
    }
    user = getattr(sound, "user", None)
    if user is not None:
        payload["uploader"] = {"id": str(getattr(user, "id", "")), "name": str(user)}
    return payload


def _snapshot_emoji(emoji) -> Optional[dict]:
    """Full snapshot of a guild emoji including image URL + roles."""
    if emoji is None:
        return None
    payload: dict = {
        "id":        str(getattr(emoji, "id", "")),
        "name":      getattr(emoji, "name", None),
        "animated":  bool(getattr(emoji, "animated", False)),
        "managed":   bool(getattr(emoji, "managed", False)),
        "available": bool(getattr(emoji, "available", True)),
        "require_colons": bool(getattr(emoji, "require_colons", True)),
        "url":       str(getattr(emoji, "url", "")) if getattr(emoji, "url", None) else None,
    }
    roles = getattr(emoji, "roles", None)
    if roles:
        payload["roles"] = [{"id": str(r.id), "name": r.name} for r in roles]
    user = getattr(emoji, "user", None)
    if user is not None:
        payload["uploader"] = {"id": str(getattr(user, "id", "")), "name": str(user)}
    return payload


def _snapshot_sticker(sticker) -> Optional[dict]:
    """Full snapshot of a guild sticker including image URL + tags."""
    if sticker is None:
        return None
    payload: dict = {
        "id":          str(getattr(sticker, "id", "")),
        "name":        getattr(sticker, "name", None),
        "description": getattr(sticker, "description", None),
        "format":      str(getattr(sticker, "format", None)),
        "emoji":       getattr(sticker, "emoji", None),
        "available":   bool(getattr(sticker, "available", True)),
        "url":         str(getattr(sticker, "url", "")) if getattr(sticker, "url", None) else None,
    }
    user = getattr(sticker, "user", None)
    if user is not None:
        payload["uploader"] = {"id": str(getattr(user, "id", "")), "name": str(user)}
    return payload


def _snapshot_scheduled_event(event) -> Optional[dict]:
    """Full snapshot of a Discord scheduled event."""
    if event is None:
        return None
    payload: dict = {
        "id":            str(getattr(event, "id", "")),
        "name":          getattr(event, "name", None),
        "description":   getattr(event, "description", None),
        "status":        str(getattr(event, "status", None)),
        "entity_type":   str(getattr(event, "entity_type", None)),
        "privacy_level": str(getattr(event, "privacy_level", None)),
        "user_count":    getattr(event, "user_count", None),
        "location":      getattr(event, "location", None),
        "url":           getattr(event, "url", None),
    }
    for attr in ("start_time", "end_time"):
        dt = getattr(event, attr, None)
        if dt is not None:
            try:
                payload[attr] = dt.isoformat()
            except Exception:
                pass
    ch = getattr(event, "channel", None)
    if ch is not None:
        payload["channel"] = {"id": str(ch.id), "name": getattr(ch, "name", None)}
    creator = getattr(event, "creator", None)
    if creator is not None:
        payload["creator"] = {"id": str(getattr(creator, "id", "")), "name": str(creator)}
    cover = getattr(event, "cover_image", None)
    if cover is not None:
        url = getattr(cover, "url", None)
        if url:
            payload["cover_image_url"] = url
    return payload


def _snapshot_stage(stage) -> Optional[dict]:
    """Full snapshot of a stage instance."""
    if stage is None:
        return None
    payload: dict = {
        "id":            str(getattr(stage, "id", "")),
        "topic":         getattr(stage, "topic", None),
        "privacy_level": str(getattr(stage, "privacy_level", None)),
        "discoverable_disabled": bool(getattr(stage, "discoverable_disabled", False)),
    }
    ch = getattr(stage, "channel", None)
    if ch is not None:
        payload["channel"] = {"id": str(ch.id), "name": getattr(ch, "name", None)}
    return payload


def _snapshot_automod_rule(rule) -> Optional[dict]:
    """Full snapshot of an AutoMod rule including trigger + actions."""
    if rule is None:
        return None
    payload: dict = {
        "id":           str(getattr(rule, "id", "")),
        "name":         getattr(rule, "name", None),
        "enabled":      bool(getattr(rule, "enabled", False)),
        "event_type":   str(getattr(rule, "event_type", None)),
        "trigger_type": str(getattr(rule, "trigger_type", None)),
    }
    creator = getattr(rule, "creator", None)
    if creator is not None:
        payload["creator"] = {"id": str(getattr(creator, "id", "")), "name": str(creator)}
    # Trigger metadata (keyword filter, regex, etc.)
    trigger = getattr(rule, "trigger", None)
    if trigger is not None:
        try:
            payload["trigger"] = {
                k: v for k, v in trigger.__dict__.items() if not k.startswith("_")
            }
        except Exception:
            payload["trigger"] = str(trigger)
    # Actions list
    actions = getattr(rule, "actions", None) or []
    try:
        payload["actions"] = [
            {
                "type":     str(getattr(a, "type", None)),
                "metadata": {k: str(v) for k, v in
                             (getattr(a, "metadata", None) or {}).items()}
                            if hasattr(a, "metadata") and a.metadata else None,
            }
            for a in actions
        ]
    except Exception:
        pass
    # Exempt roles / channels
    er = getattr(rule, "exempt_roles", None)
    if er:
        payload["exempt_roles"] = [{"id": str(r.id), "name": r.name} for r in er]
    ec = getattr(rule, "exempt_channels", None)
    if ec:
        payload["exempt_channels"] = [{"id": str(c.id), "name": getattr(c, "name", None)} for c in ec]
    return payload


def _snapshot_integration(integration) -> Optional[dict]:
    """Snapshot of a guild integration (bots, twitch, youtube, etc.)."""
    if integration is None:
        return None
    payload: dict = {
        "id":      str(getattr(integration, "id", "")),
        "name":    getattr(integration, "name", None),
        "type":    str(getattr(integration, "type", None)),
        "enabled": bool(getattr(integration, "enabled", False)),
        "account": {
            "id":   str(getattr(getattr(integration, "account", None), "id", "")),
            "name": getattr(getattr(integration, "account", None), "name", None),
        } if getattr(integration, "account", None) else None,
    }
    user = getattr(integration, "user", None)
    if user is not None:
        payload["user"] = {"id": str(getattr(user, "id", "")), "name": str(user)}
    application = getattr(integration, "application", None)
    if application is not None:
        payload["application"] = {
            "id":   str(getattr(application, "id", "")),
            "name": getattr(application, "name", None),
        }
    return payload


def _snapshot_invite(invite) -> Optional[dict]:
    """Full snapshot of an invite including all metadata."""
    if invite is None:
        return None
    payload: dict = {
        "code":      getattr(invite, "code", None),
        "url":       getattr(invite, "url", None),
        "uses":      getattr(invite, "uses", None),
        "max_uses":  getattr(invite, "max_uses", None),
        "max_age":   getattr(invite, "max_age", None),
        "temporary": bool(getattr(invite, "temporary", False)),
        "created_at": invite.created_at.isoformat()
                      if getattr(invite, "created_at", None) else None,
        "expires_at": invite.expires_at.isoformat()
                      if getattr(invite, "expires_at", None) else None,
    }
    ch = getattr(invite, "channel", None)
    if ch is not None:
        payload["channel"] = {"id": str(ch.id), "name": getattr(ch, "name", None),
                              "type": str(getattr(ch, "type", None))}
    inviter = getattr(invite, "inviter", None)
    if inviter is not None:
        payload["inviter"] = {"id": str(getattr(inviter, "id", "")), "name": str(inviter)}
    target_user = getattr(invite, "target_user", None)
    if target_user is not None:
        payload["target_user"] = {"id": str(getattr(target_user, "id", "")), "name": str(target_user)}
    target_type = getattr(invite, "target_type", None)
    if target_type is not None:
        payload["target_type"] = str(target_type)
    return payload


class WaveLoggingCog(commands.Cog):
    """All-in-one logging cog for the Logistics bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._installed_app_listeners = False
        self._startup_logged = False
        self._audit_replay_done = False
        # Member-update debounce — large role assignments fire many events;
        # we still log each one but skip duplicates within a tiny window.
        self._member_update_seen: dict[tuple[int, int], float] = {}

    # ----- cog lifecycle -----

    async def cog_load(self) -> None:
        await ensure_table()
        self._install_app_command_listeners()
        # Mirror every logger.* call (INFO+) into bot_logs for the
        # Terminal Logs dashboard tab. Idempotent on cog reload.
        install_terminal_log_capture()
        self.push_loop.start()
        self.nightly_rollup.start()
        logger.info("[wave_logging] Cog loaded — listeners installed, terminal capture on, push loops started")

    def cog_unload(self) -> None:
        if self.push_loop.is_running():
            self.push_loop.cancel()
        if self.nightly_rollup.is_running():
            self.nightly_rollup.cancel()
        logger.info("[wave_logging] Cog unloaded")

    # ============================================================
    # BOT-SIDE: slash commands, prefix commands, lifecycle
    # ============================================================

    def _install_app_command_listeners(self) -> None:
        if self._installed_app_listeners:
            return
        tree = self.bot.tree
        prev_on_completion = getattr(tree, "on_completion", None)
        prev_on_error = getattr(tree, "on_error", None)

        async def _on_completion(interaction: discord.Interaction, command: app_commands.Command):
            try:
                ns = getattr(interaction, "namespace", None)
                ns_dict = dict(ns.__dict__) if ns else None
                await log_event(
                    category="commands",
                    action="slash_command_completed",
                    actor=interaction.user,
                    guild=interaction.guild,
                    details={
                        "command": getattr(command, "qualified_name", str(command)),
                        "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                        "channel_name": getattr(interaction.channel, "name", None),
                        "namespace": ns_dict,
                        "interaction_id": str(interaction.id),
                        "interaction_locale": str(getattr(interaction, "locale", None)),
                        "guild_locale": str(getattr(interaction, "guild_locale", None)),
                        "type": str(getattr(interaction, "type", None)),
                    },
                )
            except Exception as e:
                logger.error(f"[wave_logging] on_completion failed: {e}")
            if callable(prev_on_completion):
                try:
                    await prev_on_completion(interaction, command)
                except Exception:
                    pass

        async def _on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            try:
                cmd_name = "unknown"
                if interaction.command is not None:
                    cmd_name = getattr(interaction.command, "qualified_name", str(interaction.command))
                tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
                ns = getattr(interaction, "namespace", None)
                ns_dict = dict(ns.__dict__) if ns else None
                await log_event(
                    category="errors",
                    action="slash_command_error",
                    actor=interaction.user,
                    guild=interaction.guild,
                    details={
                        "command": cmd_name,
                        "error_type": type(error).__name__,
                        "error_message": str(error),
                        "traceback": tb,
                        "namespace": ns_dict,
                        "interaction_id": str(interaction.id),
                        "channel_id": str(interaction.channel_id) if interaction.channel_id else None,
                        "channel_name": getattr(interaction.channel, "name", None),
                    },
                )
            except Exception as e:
                logger.error(f"[wave_logging] on_error failed: {e}")
            if callable(prev_on_error):
                try:
                    await prev_on_error(interaction, error)
                except Exception:
                    pass

        tree.on_completion = _on_completion  # type: ignore[assignment]
        tree.on_error = _on_error            # type: ignore[assignment]
        self._installed_app_listeners = True

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        try:
            await log_event(
                category="commands",
                action="prefix_command_completed",
                actor=ctx.author,
                guild=ctx.guild,
                details={
                    "command": ctx.command.qualified_name if ctx.command else "unknown",
                    "prefix": ctx.prefix,
                    "invoked_with": getattr(ctx, "invoked_with", None),
                    "args": [str(a) for a in (getattr(ctx, "args", []) or [])[2:]],
                    "kwargs": {k: str(v) for k, v in (getattr(ctx, "kwargs", {}) or {}).items()},
                    "channel_id": str(ctx.channel.id) if ctx.channel else None,
                    "channel_name": getattr(ctx.channel, "name", None),
                    "message": serialize_message(ctx.message) if ctx.message else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_command_completion failed: {e}")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        try:
            tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
            await log_event(
                category="errors",
                action="prefix_command_error",
                actor=ctx.author,
                guild=ctx.guild,
                details={
                    "command": ctx.command.qualified_name if ctx.command else "unknown",
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "traceback": tb,
                    "message": serialize_message(ctx.message) if ctx.message else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_command_error failed: {e}")

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if not self._startup_logged:
            self._startup_logged = True
            try:
                await log_event(
                    category="bot_lifecycle",
                    action="bot_started",
                    actor={"id": str(self.bot.user.id) if self.bot.user else None,
                           "name": str(self.bot.user) if self.bot.user else BOT_NAME},
                    details={
                        "guild_count": len(self.bot.guilds),
                        "guilds": [
                            {"id": str(g.id), "name": g.name, "member_count": g.member_count}
                            for g in self.bot.guilds
                        ],
                    },
                )
            except Exception as e:
                logger.error(f"[wave_logging] on_ready failed: {e}")

        # Run startup audit replay once per process
        if not self._audit_replay_done:
            self._audit_replay_done = True
            asyncio.create_task(self._startup_audit_replay())

    @commands.Cog.listener()
    async def on_disconnect(self) -> None:
        try:
            await log_event(
                category="bot_lifecycle",
                action="bot_disconnected",
                actor={"id": str(self.bot.user.id) if self.bot.user else None,
                       "name": str(self.bot.user) if self.bot.user else BOT_NAME},
            )
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_resumed(self) -> None:
        try:
            await log_event(
                category="bot_lifecycle",
                action="bot_resumed",
                actor={"id": str(self.bot.user.id) if self.bot.user else None,
                       "name": str(self.bot.user) if self.bot.user else BOT_NAME},
            )
        except Exception:
            pass

    # ============================================================
    # SERVER-SIDE EVENTS (bot=SERVER_BOT)
    # ============================================================

    # ---- members ----

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        try:
            account_age_days = None
            if member.created_at:
                account_age_days = (datetime.now(timezone.utc) - member.created_at).days
            await log_event(
                category="member_join",
                action="member_joined",
                target=member,
                guild=member.guild,
                bot=SERVER_BOT,
                details={
                    "account_created":  member.created_at.isoformat() if member.created_at else None,
                    "account_age_days": account_age_days,
                    "is_bot":           member.bot,
                    "pending":          bool(getattr(member, "pending", False)),
                    "guild_member_count": getattr(member.guild, "member_count", None),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_member_join failed: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        # member_leave is Gateway-only — could mean voluntary leave OR kick
        # OR ban. We check the audit log to disambiguate kick vs leave.
        try:
            joined_at = getattr(member, "joined_at", None)
            tenure_days = None
            if joined_at:
                tenure_days = (datetime.now(timezone.utc) - joined_at).days
            # Race the audit log for a kick entry on this user
            kick_audit = await fetch_audit(
                member.guild, AuditLogAction.kick, target_id=member.id,
            )
            details = {
                "joined_at":     joined_at.isoformat() if joined_at else None,
                "tenure_days":   tenure_days,
                "nick":          getattr(member, "nick", None),
                "display_name":  getattr(member, "display_name", None),
                "roles_at_departure": [
                    {"id": str(r.id), "name": r.name}
                    for r in getattr(member, "roles", [])
                    if r.name != "@everyone"
                ],
                "audit_kick": kick_audit,
            }
            await log_event(
                category="member_leave",
                action="member_kicked" if kick_audit else "member_left",
                actor=kick_audit.get("actor") if kick_audit else None,
                target=member,
                guild=member.guild,
                bot=SERVER_BOT,
                details=details,
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_member_remove failed: {e}")

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user) -> None:
        try:
            audit = await fetch_audit(
                guild, AuditLogAction.ban, target_id=getattr(user, "id", None),
            )
            await log_event(
                category="ban",
                action="member_banned",
                actor=audit.get("actor") if audit else None,
                target=user,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "audit": audit,
                    "user_id": str(getattr(user, "id", "")),
                    "username": getattr(user, "name", None),
                    "joined_at": getattr(user, "joined_at", None) and getattr(user, "joined_at").isoformat(),
                    "reason": audit.get("reason") if audit else None,
                    "banned_by": (audit.get("actor") or {}).get("display_name") or (audit.get("actor") or {}).get("name") if audit else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_member_ban failed: {e}")

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user) -> None:
        try:
            audit = await fetch_audit(
                guild, AuditLogAction.unban, target_id=getattr(user, "id", None),
            )
            await log_event(
                category="unban",
                action="member_unbanned",
                actor=audit.get("actor") if audit else None,
                target=user,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "audit": audit,
                    "user_id": str(getattr(user, "id", "")),
                    "username": getattr(user, "name", None),
                    "reason": audit.get("reason") if audit else None,
                    "unbanned_by": (audit.get("actor") or {}).get("display_name") or (audit.get("actor") or {}).get("name") if audit else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_member_unban failed: {e}")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        try:
            # Role changes → AuditLogAction.member_role_update
            before_roles = set(before.roles)
            after_roles = set(after.roles)
            added = after_roles - before_roles
            removed = before_roles - after_roles
            if added or removed:
                audit = await fetch_audit(
                    after.guild, AuditLogAction.member_role_update,
                    target_id=after.id,
                )
                await log_event(
                    category="member_role_update",
                    action="member_roles_changed",
                    target=after,
                    guild=after.guild,
                    bot=SERVER_BOT,
                    details={
                        "added":   [serialize_role(r) for r in added],
                        "removed": [serialize_role(r) for r in removed],
                        "audit":   audit,
                    },
                )

            # Nickname changes → AuditLogAction.member_update
            if before.nick != after.nick:
                audit = await fetch_audit(
                    after.guild, AuditLogAction.member_update, target_id=after.id,
                )
                await log_event(
                    category="member_update",
                    action="nickname_changed",
                    target=after,
                    guild=after.guild,
                    bot=SERVER_BOT,
                    details={
                        "before": before.nick,
                        "after":  after.nick,
                        "audit":  audit,
                    },
                )

            # Timeouts → also AuditLogAction.member_update
            before_to = getattr(before, "timed_out_until", None)
            after_to  = getattr(after,  "timed_out_until", None)
            if before_to != after_to:
                audit = await fetch_audit(
                    after.guild, AuditLogAction.member_update, target_id=after.id,
                )
                if after_to is not None:
                    duration_seconds = None
                    try:
                        duration_seconds = (after_to - datetime.now(timezone.utc)).total_seconds()
                    except Exception:
                        pass
                    await log_event(
                        category="member_update",
                        action="member_timed_out",
                        target=after,
                        guild=after.guild,
                        bot=SERVER_BOT,
                        details={
                            "until":            after_to.isoformat(),
                            "duration_seconds": duration_seconds,
                            "audit":            audit,
                        },
                    )
                else:
                    await log_event(
                        category="member_update",
                        action="member_timeout_cleared",
                        target=after,
                        guild=after.guild,
                        bot=SERVER_BOT,
                        details={"audit": audit},
                    )

            # Boost / unboost → premium_since changes
            before_boost = getattr(before, "premium_since", None)
            after_boost  = getattr(after,  "premium_since", None)
            if before_boost != after_boost:
                if after_boost is not None and before_boost is None:
                    await log_event(
                        category="member_boost",
                        action="member_boosted",
                        target=after,
                        guild=after.guild,
                        bot=SERVER_BOT,
                        details={"since": after_boost.isoformat()},
                    )
                elif before_boost is not None and after_boost is None:
                    await log_event(
                        category="member_boost",
                        action="member_unboosted",
                        target=after,
                        guild=after.guild,
                        bot=SERVER_BOT,
                        details={"was_since": before_boost.isoformat()},
                    )

            # Pending (Membership Screening) flip
            if bool(getattr(before, "pending", False)) != bool(getattr(after, "pending", False)):
                await log_event(
                    category="member_update",
                    action="screening_passed" if not after.pending else "screening_pending",
                    target=after,
                    guild=after.guild,
                    bot=SERVER_BOT,
                )

            # Avatar guild-specific change (per-guild avatar)
            ga_before = getattr(before, "guild_avatar", None)
            ga_after  = getattr(after,  "guild_avatar", None)
            if ga_before != ga_after:
                await log_event(
                    category="member_update",
                    action="guild_avatar_changed",
                    target=after,
                    guild=after.guild,
                    bot=SERVER_BOT,
                    details={
                        "before_url": getattr(ga_before, "url", None) if ga_before else None,
                        "after_url":  getattr(ga_after,  "url", None) if ga_after  else None,
                    },
                )
        except Exception as e:
            logger.error(f"[wave_logging] on_member_update failed: {e}")

    # ---- channels ----

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel) -> None:
        try:
            audit = await fetch_audit(
                channel.guild, AuditLogAction.channel_create, target_id=channel.id,
            )
            await log_event(
                category="channel_create",
                action="channel_created",
                guild=channel.guild,
                bot=SERVER_BOT,
                details={
                    "channel": serialize_channel(channel),
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_channel_create failed: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel) -> None:
        try:
            # Snapshot the channel BEFORE the audit lookup — after the
            # sleep it may already be GC'd from cache, but the object
            # passed in is still complete.
            snapshot = serialize_channel(channel)
            audit = await fetch_audit(
                channel.guild, AuditLogAction.channel_delete, target_id=channel.id,
            )
            await log_event(
                category="channel_delete",
                action="channel_deleted",
                guild=channel.guild,
                bot=SERVER_BOT,
                details={
                    "channel": snapshot,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_channel_delete failed: {e}")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after) -> None:
        try:
            changes: dict = {}
            for attr in ("name", "topic", "nsfw", "slowmode_delay", "position",
                         "bitrate", "user_limit", "rtc_region",
                         "default_auto_archive_duration", "type"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {
                        "before": str(b) if b is not None else None,
                        "after":  str(a) if a is not None else None,
                    }
            # Permission overwrites diff
            try:
                before_ovw = {str(t.id): p.pair() for t, p in (before.overwrites or {}).items()}
                after_ovw  = {str(t.id): p.pair() for t, p in (after.overwrites  or {}).items()}
                if before_ovw != after_ovw:
                    changes["overwrites"] = {
                        "before": serialize_channel(before).get("overwrites"),
                        "after":  serialize_channel(after).get("overwrites"),
                    }
            except Exception:
                pass
            if not changes:
                return
            audit = await fetch_audit(
                after.guild, AuditLogAction.channel_update, target_id=after.id,
            )
            await log_event(
                category="channel_update",
                action="channel_edited",
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "channel": serialize_channel(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_channel_update failed: {e}")

    # ---- roles ----

    @commands.Cog.listener()
    async def on_guild_role_create(self, role: discord.Role) -> None:
        try:
            audit = await fetch_audit(
                role.guild, AuditLogAction.role_create, target_id=role.id,
            )
            await log_event(
                category="role_create",
                action="role_created",
                guild=role.guild,
                bot=SERVER_BOT,
                details={
                    "role":  serialize_role(role),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_role_create failed: {e}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        try:
            snapshot = serialize_role(role)
            audit = await fetch_audit(
                role.guild, AuditLogAction.role_delete, target_id=role.id,
            )
            await log_event(
                category="role_delete",
                action="role_deleted",
                guild=role.guild,
                bot=SERVER_BOT,
                details={
                    "role":  snapshot,
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_role_delete failed: {e}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before: discord.Role, after: discord.Role) -> None:
        try:
            changes: dict = {}
            for attr in ("name", "color", "mentionable", "hoist", "position",
                         "icon", "unicode_emoji"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": str(b), "after": str(a)}
            if before.permissions.value != after.permissions.value:
                changes["permissions"] = {
                    "before":       before.permissions.value,
                    "after":        after.permissions.value,
                    "before_names": [n for n, v in before.permissions if v],
                    "after_names":  [n for n, v in after.permissions  if v],
                }
            if not changes:
                return
            audit = await fetch_audit(
                after.guild, AuditLogAction.role_update, target_id=after.id,
            )
            await log_event(
                category="role_update",
                action="role_updated",
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "role":    serialize_role(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_role_update failed: {e}")

    # ---- voice ----

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                     before: discord.VoiceState,
                                     after: discord.VoiceState) -> None:
        # One packet can carry multiple state changes (e.g. join + self_deaf).
        # Build the full action list, then log every change separately.
        try:
            ch_before = before.channel
            ch_after  = after.channel
            actions: list[str] = []
            # Channel transitions (mutually exclusive with each other)
            if ch_before is None and ch_after is not None:
                actions.append("voice_joined")
            elif ch_before is not None and ch_after is None:
                actions.append("voice_left")
            elif ch_before is not None and ch_after is not None and ch_before.id != ch_after.id:
                actions.append("voice_moved")
            # Independent boolean flips
            if before.self_mute   != after.self_mute:
                actions.append("voice_self_mute"   if after.self_mute   else "voice_self_unmute")
            if before.self_deaf   != after.self_deaf:
                actions.append("voice_self_deaf"   if after.self_deaf   else "voice_self_undeaf")
            if before.mute        != after.mute:
                actions.append("voice_server_mute" if after.mute        else "voice_server_unmute")
            if before.deaf        != after.deaf:
                actions.append("voice_server_deaf" if after.deaf        else "voice_server_undeaf")
            if before.self_stream != after.self_stream:
                actions.append("voice_stream_start" if after.self_stream else "voice_stream_stop")
            if before.self_video  != after.self_video:
                actions.append("voice_video_start"  if after.self_video  else "voice_video_stop")
            if getattr(before, "suppress", False) != getattr(after, "suppress", False):
                actions.append("voice_suppressed" if after.suppress else "voice_unsuppressed")
            if not actions:
                return
            base_details = {
                "before": serialize_voice_state(before),
                "after":  serialize_voice_state(after),
                "channel_before_id": str(ch_before.id) if ch_before else None,
                "channel_after_id":  str(ch_after.id)  if ch_after  else None,
            }
            for action in actions:
                await log_event(
                    category="voice_state_changed",
                    action=action,
                    actor=member,
                    guild=member.guild,
                    bot=SERVER_BOT,
                    details=base_details,
                )
        except Exception as e:
            logger.error(f"[wave_logging] on_voice_state_update failed: {e}")

    # ---- soundboard ----
    # discord.py exposes this as on_soundboard_sound_create / etc; the
    # actual play event is gateway-level and may not be exposed on
    # older versions. Wrap in try/except — silently skip if missing.

    @commands.Cog.listener()
    async def on_soundboard_sound_create(self, sound) -> None:
        try:
            guild = getattr(sound, "guild", None)
            sound_id = getattr(sound, "id", None)
            audit = await fetch_audit(
                guild, AuditLogAction.soundboard_sound_create, target_id=sound_id,
            ) if guild else None
            await log_event(
                category="soundboard_sound_create",
                action="soundboard_sound_created",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "sound": _snapshot_sound(sound),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_soundboard_sound_create failed: {e}")

    @commands.Cog.listener()
    async def on_soundboard_sound_delete(self, sound) -> None:
        try:
            guild = getattr(sound, "guild", None)
            sound_id = getattr(sound, "id", None)
            snapshot = _snapshot_sound(sound)
            audit = await fetch_audit(
                guild, AuditLogAction.soundboard_sound_delete, target_id=sound_id,
            ) if guild else None
            await log_event(
                category="soundboard_sound_delete",
                action="soundboard_sound_deleted",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "sound": snapshot,
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_soundboard_sound_delete failed: {e}")

    @commands.Cog.listener()
    async def on_soundboard_sound_update(self, before, after) -> None:
        try:
            guild = getattr(after, "guild", None)
            sound_id = getattr(after, "id", None)
            changes: dict = {}
            for attr in ("name", "volume", "emoji", "available"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": str(b), "after": str(a)}
            if not changes:
                return
            audit = await fetch_audit(
                guild, AuditLogAction.soundboard_sound_update, target_id=sound_id,
            ) if guild else None
            await log_event(
                category="soundboard_sound_update",
                action="soundboard_sound_updated",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "sound":   _snapshot_sound(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_soundboard_sound_update failed: {e}")

    # ---- messages ----

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        # We log bot messages too — moderation deletions of webhook/bot
        # output are real events. The deletion path snapshots the FULL
        # message before the audit-log sleep so nothing is lost to GC.
        try:
            snapshot = serialize_message(message)
            audit = await fetch_audit(
                message.guild, AuditLogAction.message_delete,
                target_id=getattr(message.author, "id", None),
            ) if message.guild else None
            await log_event(
                category="message_delete",
                action="message_deleted",
                actor=message.author,
                guild=message.guild,
                bot=SERVER_BOT,
                details={
                    "message": snapshot,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_message_delete failed: {e}")

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        # Log every edit including pin/unpin and embed-only changes —
        # we differentiate via the `change_type` field so the dashboard
        # can filter if needed.
        try:
            change_type: list[str] = []
            if before.content != after.content:
                change_type.append("content")
            if bool(before.pinned) != bool(after.pinned):
                change_type.append("pinned" if after.pinned else "unpinned")
            # Embed diff (link unfurls, suppressed embeds, etc.)
            before_embeds = [e.to_dict() for e in (before.embeds or []) if hasattr(e, "to_dict")]
            after_embeds  = [e.to_dict() for e in (after.embeds  or []) if hasattr(e, "to_dict")]
            if before_embeds != after_embeds:
                change_type.append("embeds")
            # Attachment list change (rare for edits, but possible)
            before_atts = [getattr(a, "id", None) for a in (before.attachments or [])]
            after_atts  = [getattr(a, "id", None) for a in (after.attachments  or [])]
            if before_atts != after_atts:
                change_type.append("attachments")
            # Flag change (e.g. SUPPRESS_EMBEDS)
            before_flags = getattr(getattr(before, "flags", None), "value", None)
            after_flags  = getattr(getattr(after,  "flags", None), "value", None)
            if before_flags != after_flags:
                change_type.append("flags")
            if not change_type:
                return
            await log_event(
                category="message_edit",
                action="message_edited",
                actor=after.author,
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "change_type": change_type,
                    "before":      serialize_message(before),
                    "after":       serialize_message(after),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_message_edit failed: {e}")

    # ---- guild settings ----

    @commands.Cog.listener()
    async def on_guild_update(self, before: discord.Guild, after: discord.Guild) -> None:
        try:
            changes: dict = {}
            for attr in ("name", "description", "icon", "banner", "splash",
                         "discovery_splash", "verification_level",
                         "explicit_content_filter", "default_notifications",
                         "afk_timeout", "afk_channel", "system_channel",
                         "rules_channel", "public_updates_channel",
                         "preferred_locale", "premium_tier",
                         "premium_subscription_count", "mfa_level",
                         "nsfw_level", "vanity_url_code", "owner_id"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {
                        "before": str(b) if b is not None else None,
                        "after":  str(a) if a is not None else None,
                    }
            # Features list diff
            b_features = set(getattr(before, "features", []) or [])
            a_features = set(getattr(after,  "features", []) or [])
            if b_features != a_features:
                changes["features"] = {
                    "added":   sorted(a_features - b_features),
                    "removed": sorted(b_features - a_features),
                }
            if not changes:
                return
            audit = await fetch_audit(
                after, AuditLogAction.guild_update, target_id=after.id,
            )
            await log_event(
                category="guild_update",
                action="guild_updated",
                guild=after,
                bot=SERVER_BOT,
                details={
                    "guild":   serialize_guild_full(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_update failed: {e}")

    # ---- emoji + stickers ----

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after) -> None:
        try:
            before_by_id = {e.id: e for e in before}
            after_by_id  = {e.id: e for e in after}
            added   = [after_by_id[i]  for i in after_by_id  if i not in before_by_id]
            removed = [before_by_id[i] for i in before_by_id if i not in after_by_id]
            # Renames — id stays the same, name (or other attrs) changes
            renamed: list[dict] = []
            for i in set(before_by_id) & set(after_by_id):
                b, a = before_by_id[i], after_by_id[i]
                if b.name != a.name:
                    renamed.append({
                        "id":   str(i),
                        "before": _snapshot_emoji(b),
                        "after":  _snapshot_emoji(a),
                    })
            if not added and not removed and not renamed:
                return
            if added:
                audit = await fetch_audit(guild, AuditLogAction.emoji_create)
                await log_event(
                    category="emoji_create",
                    action="emojis_added",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "added": [_snapshot_emoji(e) for e in added],
                        "audit": audit,
                    },
                )
            if removed:
                audit = await fetch_audit(guild, AuditLogAction.emoji_delete)
                await log_event(
                    category="emoji_delete",
                    action="emojis_removed",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "removed": [_snapshot_emoji(e) for e in removed],
                        "audit":   audit,
                    },
                )
            if renamed:
                audit = await fetch_audit(guild, AuditLogAction.emoji_update)
                await log_event(
                    category="emoji_update",
                    action="emojis_renamed",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "renamed": renamed,
                        "audit":   audit,
                    },
                )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_emojis_update failed: {e}")

    @commands.Cog.listener()
    async def on_guild_stickers_update(self, guild, before, after) -> None:
        try:
            before_by_id = {s.id: s for s in before}
            after_by_id  = {s.id: s for s in after}
            added   = [after_by_id[i]  for i in after_by_id  if i not in before_by_id]
            removed = [before_by_id[i] for i in before_by_id if i not in after_by_id]
            renamed: list[dict] = []
            for i in set(before_by_id) & set(after_by_id):
                b, a = before_by_id[i], after_by_id[i]
                if b.name != a.name or getattr(b, "description", None) != getattr(a, "description", None):
                    renamed.append({
                        "id":     str(i),
                        "before": _snapshot_sticker(b),
                        "after":  _snapshot_sticker(a),
                    })
            if not added and not removed and not renamed:
                return
            if added:
                audit = await fetch_audit(guild, AuditLogAction.sticker_create)
                await log_event(
                    category="sticker_create",
                    action="stickers_added",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "added": [_snapshot_sticker(s) for s in added],
                        "audit": audit,
                    },
                )
            if removed:
                audit = await fetch_audit(guild, AuditLogAction.sticker_delete)
                await log_event(
                    category="sticker_delete",
                    action="stickers_removed",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "removed": [_snapshot_sticker(s) for s in removed],
                        "audit":   audit,
                    },
                )
            if renamed:
                audit = await fetch_audit(guild, AuditLogAction.sticker_update)
                await log_event(
                    category="sticker_update",
                    action="stickers_renamed",
                    guild=guild,
                    bot=SERVER_BOT,
                    details={
                        "renamed": renamed,
                        "audit":   audit,
                    },
                )
        except Exception as e:
            logger.error(f"[wave_logging] on_guild_stickers_update failed: {e}")

    # ---- invites ----

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        try:
            audit = await fetch_audit(
                invite.guild, AuditLogAction.invite_create,
            ) if invite.guild else None
            await log_event(
                category="invite_create",
                action="invite_created",
                actor=invite.inviter,
                guild=invite.guild,
                bot=SERVER_BOT,
                details={
                    "invite": _snapshot_invite(invite),
                    "audit":  audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_invite_create failed: {e}")

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        try:
            snapshot = _snapshot_invite(invite)
            audit = await fetch_audit(
                invite.guild, AuditLogAction.invite_delete,
            ) if invite.guild else None
            await log_event(
                category="invite_delete",
                action="invite_deleted",
                guild=invite.guild,
                bot=SERVER_BOT,
                details={
                    "invite": snapshot,
                    "audit":  audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_invite_delete failed: {e}")

    # ---- raw message events (uncached fallbacks) ----
    # The regular on_message_delete/edit only fire if the message is in
    # the bot's RAM cache. These raw events fire for EVERY delete/edit,
    # cache or no — so nothing slips by.

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent) -> None:
        # Skip if the regular listener already handled it (cached_message present
        # means on_message_delete already fired with full content).
        if payload.cached_message is not None:
            return
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            audit = await fetch_audit(
                guild, AuditLogAction.message_delete,
                channel_id=payload.channel_id,
            ) if guild else None
            note = (
                "message was not in cache — only IDs available"
                if audit
                else "message was not in cache — no audit entry (likely self-deleted or bot auto-delete)"
            )
            await log_event(
                category="message_delete",
                action="raw_message_deleted",
                actor=audit.get("actor") if audit else None,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "message_id": str(payload.message_id),
                    "channel_id": str(payload.channel_id),
                    "note":       note,
                    "audit":      audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_message_delete failed: {e}")

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent) -> None:
        if payload.cached_message is not None:
            return
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            channel = self.bot.get_channel(payload.channel_id)
            raw = payload.data or {}

            # Extract author — Discord sends author at top level or inside member.user
            author_raw = raw.get("author") or (raw.get("member") or {}).get("user") or {}
            actor = None
            if author_raw.get("id"):
                actor = {
                    "id": author_raw["id"],
                    "name": author_raw.get("username"),
                    "display_name": author_raw.get("global_name") or author_raw.get("username"),
                }

            await log_event(
                category="message_edit",
                action="raw_message_edited",
                actor=actor,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "message_id":   str(payload.message_id),
                    "channel_id":   str(payload.channel_id),
                    "channel_name": getattr(channel, "name", None),
                    "content":      raw.get("content"),
                    "edited_at":    raw.get("edited_timestamp"),
                    "note":         "message was not in cache — pre-edit content unknown",
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_message_edit failed: {e}")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            audit = await fetch_audit(
                guild, AuditLogAction.message_bulk_delete,
            ) if guild else None
            cached_snapshots = [
                serialize_message(m) for m in (payload.cached_messages or [])
            ]
            await log_event(
                category="message_bulk_delete",
                action="bulk_messages_deleted",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "channel_id":     str(payload.channel_id),
                    "message_ids":    [str(i) for i in payload.message_ids],
                    "count":          len(payload.message_ids),
                    "cached_messages": cached_snapshots,
                    "audit":          audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_bulk_message_delete failed: {e}")

    # ---- reactions ----
    # Use raw_* variants so reactions on uncached messages still count.

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            actor = payload.member or (self.bot.get_user(payload.user_id) if payload.user_id else None)
            await log_event(
                category="reaction_add",
                action="reaction_added",
                actor=actor,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "emoji":         str(payload.emoji),
                    "emoji_id":      str(payload.emoji.id) if payload.emoji.id else None,
                    "emoji_name":    payload.emoji.name,
                    "emoji_animated": bool(getattr(payload.emoji, "animated", False)),
                    "message_id":    str(payload.message_id),
                    "channel_id":    str(payload.channel_id),
                    "user_id":       str(payload.user_id),
                    "burst":         bool(getattr(payload, "burst", False)),
                    "type":          str(getattr(payload, "type", None)),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_reaction_add failed: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            actor = self.bot.get_user(payload.user_id) if payload.user_id else None
            await log_event(
                category="reaction_remove",
                action="reaction_removed",
                actor=actor,
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "emoji":      str(payload.emoji),
                    "emoji_id":   str(payload.emoji.id) if payload.emoji.id else None,
                    "emoji_name": payload.emoji.name,
                    "message_id": str(payload.message_id),
                    "channel_id": str(payload.channel_id),
                    "user_id":    str(payload.user_id),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_reaction_remove failed: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload: discord.RawReactionClearEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            await log_event(
                category="reaction_clear",
                action="reactions_cleared",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "message_id": str(payload.message_id),
                    "channel_id": str(payload.channel_id),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_reaction_clear failed: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload: discord.RawReactionClearEmojiEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            await log_event(
                category="reaction_clear",
                action="reaction_emoji_cleared",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "emoji":      str(payload.emoji),
                    "emoji_id":   str(payload.emoji.id) if payload.emoji.id else None,
                    "emoji_name": payload.emoji.name,
                    "message_id": str(payload.message_id),
                    "channel_id": str(payload.channel_id),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_reaction_clear_emoji failed: {e}")

    # ---- threads ----

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        try:
            audit = await fetch_audit(
                thread.guild, AuditLogAction.thread_create, target_id=thread.id,
            )
            await log_event(
                category="thread_create",
                action="thread_created",
                actor=getattr(thread, "owner", None),
                guild=thread.guild,
                bot=SERVER_BOT,
                details={
                    "thread": serialize_channel(thread),
                    "audit":  audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_thread_create failed: {e}")

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread) -> None:
        try:
            snapshot = serialize_channel(thread)
            audit = await fetch_audit(
                thread.guild, AuditLogAction.thread_delete, target_id=thread.id,
            )
            await log_event(
                category="thread_delete",
                action="thread_deleted",
                guild=thread.guild,
                bot=SERVER_BOT,
                details={
                    "thread": snapshot,
                    "audit":  audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_thread_delete failed: {e}")

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread) -> None:
        try:
            changes: dict = {}
            for attr in ("name", "archived", "locked", "auto_archive_duration",
                         "slowmode_delay", "invitable", "flags"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": str(b), "after": str(a)}
            if not changes:
                return
            audit = await fetch_audit(
                after.guild, AuditLogAction.thread_update, target_id=after.id,
            )
            await log_event(
                category="thread_update",
                action="thread_updated",
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "thread":  serialize_channel(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_thread_update failed: {e}")

    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember) -> None:
        try:
            thread = getattr(member, "thread", None)
            guild  = getattr(thread, "guild", None) if thread else None
            await log_event(
                category="thread_member",
                action="thread_member_joined",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "user_id":   str(getattr(member, "id", "")),
                    "thread_id": str(getattr(thread, "id", "")) if thread else None,
                    "thread_name": getattr(thread, "name", None) if thread else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_thread_member_join failed: {e}")

    @commands.Cog.listener()
    async def on_thread_member_remove(self, member: discord.ThreadMember) -> None:
        try:
            thread = getattr(member, "thread", None)
            guild  = getattr(thread, "guild", None) if thread else None
            await log_event(
                category="thread_member",
                action="thread_member_left",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "user_id":   str(getattr(member, "id", "")),
                    "thread_id": str(getattr(thread, "id", "")) if thread else None,
                    "thread_name": getattr(thread, "name", None) if thread else None,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_thread_member_remove failed: {e}")

    # ---- webhooks ----

    @commands.Cog.listener()
    async def on_webhooks_update(self, channel) -> None:
        # Gateway only tells us "something changed" in this channel's
        # webhooks; the audit log carries the create/delete/update detail.
        try:
            guild = channel.guild
            # Try each webhook audit action in turn — whichever matched
            # within the last 15s wins.
            audit = None
            for action in (AuditLogAction.webhook_create,
                           AuditLogAction.webhook_update,
                           AuditLogAction.webhook_delete):
                audit = await fetch_audit(guild, action, sleep_seconds=0.5)
                if audit:
                    break
            await log_event(
                category="webhook_update",
                action="webhooks_changed",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "channel_id":   str(channel.id),
                    "channel_name": getattr(channel, "name", None),
                    "audit":        audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_webhooks_update failed: {e}")

    # ---- scheduled events ----

    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event: discord.ScheduledEvent) -> None:
        try:
            audit = await fetch_audit(
                event.guild, AuditLogAction.scheduled_event_create,
                target_id=event.id,
            )
            await log_event(
                category="scheduled_event_create",
                action="scheduled_event_created",
                actor=event.creator,
                guild=event.guild,
                bot=SERVER_BOT,
                details={
                    "event": _snapshot_scheduled_event(event),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_scheduled_event_create failed: {e}")

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        try:
            snapshot = _snapshot_scheduled_event(event)
            audit = await fetch_audit(
                event.guild, AuditLogAction.scheduled_event_delete,
                target_id=event.id,
            )
            await log_event(
                category="scheduled_event_delete",
                action="scheduled_event_deleted",
                guild=event.guild,
                bot=SERVER_BOT,
                details={
                    "event": snapshot,
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_scheduled_event_delete failed: {e}")

    @commands.Cog.listener()
    async def on_scheduled_event_update(self,
                                         before: discord.ScheduledEvent,
                                         after:  discord.ScheduledEvent) -> None:
        try:
            changes: dict = {}
            for attr in ("name", "description", "status", "start_time", "end_time",
                         "channel_id", "location", "entity_type",
                         "privacy_level", "user_count"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": str(b), "after": str(a)}
            if not changes:
                return
            audit = await fetch_audit(
                after.guild, AuditLogAction.scheduled_event_update,
                target_id=after.id,
            )
            await log_event(
                category="scheduled_event_update",
                action="scheduled_event_updated",
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "event":   _snapshot_scheduled_event(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_scheduled_event_update failed: {e}")

    @commands.Cog.listener()
    async def on_scheduled_event_user_add(self,
                                           event: discord.ScheduledEvent,
                                           user) -> None:
        try:
            await log_event(
                category="scheduled_event_user",
                action="user_interested",
                actor=user,
                guild=event.guild,
                bot=SERVER_BOT,
                details={
                    "event_id":   str(event.id),
                    "event_name": event.name,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_scheduled_event_user_add failed: {e}")

    @commands.Cog.listener()
    async def on_scheduled_event_user_remove(self,
                                              event: discord.ScheduledEvent,
                                              user) -> None:
        try:
            await log_event(
                category="scheduled_event_user",
                action="user_uninterested",
                actor=user,
                guild=event.guild,
                bot=SERVER_BOT,
                details={
                    "event_id":   str(event.id),
                    "event_name": event.name,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_scheduled_event_user_remove failed: {e}")

    # ---- stage instances ----

    @commands.Cog.listener()
    async def on_stage_instance_create(self, stage: discord.StageInstance) -> None:
        try:
            audit = await fetch_audit(
                stage.guild, AuditLogAction.stage_instance_create,
                target_id=stage.id,
            )
            await log_event(
                category="stage_instance_create",
                action="stage_started",
                guild=stage.guild,
                bot=SERVER_BOT,
                details={
                    "stage": _snapshot_stage(stage),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_stage_instance_create failed: {e}")

    @commands.Cog.listener()
    async def on_stage_instance_delete(self, stage: discord.StageInstance) -> None:
        try:
            snapshot = _snapshot_stage(stage)
            audit = await fetch_audit(
                stage.guild, AuditLogAction.stage_instance_delete,
                target_id=stage.id,
            )
            await log_event(
                category="stage_instance_delete",
                action="stage_ended",
                guild=stage.guild,
                bot=SERVER_BOT,
                details={
                    "stage": snapshot,
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_stage_instance_delete failed: {e}")

    @commands.Cog.listener()
    async def on_stage_instance_update(self,
                                        before: discord.StageInstance,
                                        after:  discord.StageInstance) -> None:
        try:
            changes: dict = {}
            for attr in ("topic", "privacy_level", "discoverable_disabled"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": str(b), "after": str(a)}
            if not changes:
                return
            audit = await fetch_audit(
                after.guild, AuditLogAction.stage_instance_update,
                target_id=after.id,
            )
            await log_event(
                category="stage_instance_update",
                action="stage_updated",
                guild=after.guild,
                bot=SERVER_BOT,
                details={
                    "stage":   _snapshot_stage(after),
                    "changes": changes,
                    "audit":   audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_stage_instance_update failed: {e}")

    # ---- AutoMod (huge for moderation visibility) ----

    @commands.Cog.listener()
    async def on_automod_rule_create(self, rule: discord.AutoModRule) -> None:
        try:
            audit = await fetch_audit(
                rule.guild, AuditLogAction.automod_rule_create, target_id=rule.id,
            )
            await log_event(
                category="automod_rule_create",
                action="automod_rule_created",
                guild=rule.guild,
                bot=SERVER_BOT,
                details={
                    "rule":  _snapshot_automod_rule(rule),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_automod_rule_create failed: {e}")

    @commands.Cog.listener()
    async def on_automod_rule_update(self, rule: discord.AutoModRule) -> None:
        try:
            audit = await fetch_audit(
                rule.guild, AuditLogAction.automod_rule_update, target_id=rule.id,
            )
            await log_event(
                category="automod_rule_update",
                action="automod_rule_updated",
                guild=rule.guild,
                bot=SERVER_BOT,
                details={
                    "rule":  _snapshot_automod_rule(rule),
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_automod_rule_update failed: {e}")

    @commands.Cog.listener()
    async def on_automod_rule_delete(self, rule: discord.AutoModRule) -> None:
        try:
            snapshot = _snapshot_automod_rule(rule)
            audit = await fetch_audit(
                rule.guild, AuditLogAction.automod_rule_delete, target_id=rule.id,
            )
            await log_event(
                category="automod_rule_delete",
                action="automod_rule_deleted",
                guild=rule.guild,
                bot=SERVER_BOT,
                details={
                    "rule":  snapshot,
                    "audit": audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_automod_rule_delete failed: {e}")

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction) -> None:
        try:
            guild = getattr(execution, "guild", None)
            await log_event(
                category="automod_action",
                action="automod_triggered",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "rule_id":            str(getattr(execution, "rule_id", "")),
                    "rule_trigger_type":  str(getattr(execution, "rule_trigger_type", None)),
                    "action_type":        str(getattr(getattr(execution, "action", None), "type", None)),
                    "user_id":            str(getattr(execution, "user_id", "")),
                    "channel_id":         str(getattr(execution, "channel_id", "")),
                    "message_id":         str(getattr(execution, "message_id", "")),
                    "alert_system_message_id":
                        str(getattr(execution, "alert_system_message_id", "")),
                    "content":            getattr(execution, "content", None),
                    "matched_keyword":    getattr(execution, "matched_keyword", None),
                    "matched_content":    getattr(execution, "matched_content", None),
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_automod_action failed: {e}")

    # ---- integrations ----

    @commands.Cog.listener()
    async def on_integration_create(self, integration) -> None:
        try:
            guild = getattr(integration, "guild", None)
            audit = await fetch_audit(
                guild, AuditLogAction.integration_create,
            ) if guild else None
            await log_event(
                category="integration_create",
                action="integration_created",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "integration": _snapshot_integration(integration),
                    "audit":       audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_integration_create failed: {e}")

    @commands.Cog.listener()
    async def on_integration_update(self, integration) -> None:
        try:
            guild = getattr(integration, "guild", None)
            audit = await fetch_audit(
                guild, AuditLogAction.integration_update,
            ) if guild else None
            await log_event(
                category="integration_update",
                action="integration_updated",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "integration": _snapshot_integration(integration),
                    "audit":       audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_integration_update failed: {e}")

    @commands.Cog.listener()
    async def on_raw_integration_delete(self,
                                         payload: discord.RawIntegrationDeleteEvent) -> None:
        try:
            guild = self.bot.get_guild(payload.guild_id) if payload.guild_id else None
            audit = await fetch_audit(
                guild, AuditLogAction.integration_delete,
            ) if guild else None
            await log_event(
                category="integration_delete",
                action="integration_deleted",
                guild=guild,
                bot=SERVER_BOT,
                details={
                    "integration_id": str(payload.integration_id),
                    "application_id": str(payload.application_id)
                                      if payload.application_id else None,
                    "audit":          audit,
                },
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_raw_integration_delete failed: {e}")

    # ---- global user updates (username / avatar / discriminator) ----

    @commands.Cog.listener()
    async def on_user_update(self, before: discord.User, after: discord.User) -> None:
        # Fires for ANY user the bot can see — could be thousands of users
        # across guilds. We only log changes to fields that actually flipped.
        try:
            changes: dict = {}
            for attr in ("name", "discriminator", "global_name"):
                b = getattr(before, attr, None)
                a = getattr(after,  attr, None)
                if b != a:
                    changes[attr] = {"before": b, "after": a}
            # Avatar URL change
            b_av = getattr(getattr(before, "display_avatar", None), "url", None)
            a_av = getattr(getattr(after,  "display_avatar", None), "url", None)
            if b_av != a_av:
                changes["avatar_url"] = {"before": b_av, "after": a_av}
            if not changes:
                return
            await log_event(
                category="user_update",
                action="user_profile_changed",
                target=after,
                bot=SERVER_BOT,
                details={"changes": changes},
            )
        except Exception as e:
            logger.error(f"[wave_logging] on_user_update failed: {e}")

    # ============================================================
    # STARTUP AUDIT REPLAY — backfill events missed while offline
    # ============================================================

    async def _startup_audit_replay(self) -> None:
        """Walk each guild's audit log for the last 24h and log entries
        we likely missed (events from while bot was offline). Best effort —
        skips guilds where audit-log perm is missing."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        total = 0
        for i, guild in enumerate(self.bot.guilds):
            if i > 0:
                await asyncio.sleep(2)
            try:
                async for entry in guild.audit_logs(after=cutoff, limit=500, oldest_first=True):
                    try:
                        # Use the action name as BOTH the category and the
                        # action so each AuditLogAction lands in its own tab
                        # on the dashboard (channel_create, ban, etc.).
                        action_name = str(entry.action).replace("AuditLogAction.", "")
                        await log_event(
                            category=action_name,
                            action=action_name,
                            actor=entry.user,
                            target=entry.target,
                            guild=guild,
                            bot=SERVER_BOT,
                            details={
                                "source": "audit_replay",
                                "reason": entry.reason,
                                "audit_id": str(entry.id),
                                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                            },
                        )
                        total += 1
                    except Exception:
                        continue
            except discord.Forbidden:
                logger.warning(f"[wave_logging] No audit-log perm in {guild.name}, skipping replay")
            except Exception as e:
                logger.error(f"[wave_logging] audit replay {guild.name}: {e}")
        logger.info(f"[wave_logging] Startup audit replay complete — {total} entries backfilled")

    # ============================================================
    # PUSH + ROLLUP LOOPS
    # ============================================================

    @tasks.loop(minutes=5)
    async def push_loop(self) -> None:
        # Push cadence dropped from 15min → 5min because fat events are
        # ~10-20× larger; smaller, more frequent deltas keep individual
        # files well under the 8MB per-file cap.
        try:
            from push_wave_logging import push_unpushed_events
            await push_unpushed_events(bot=self.bot)
        except Exception as e:
            logger.error(f"[wave_logging] push_loop error: {e}")
            traceback.print_exc()

    @push_loop.before_loop
    async def _before_push_loop(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(time=time(hour=0, minute=10, tzinfo=timezone.utc))
    async def nightly_rollup(self) -> None:
        # Run 5 min after Manager's rollup to avoid concurrent edits.
        try:
            from push_wave_logging import rollup_yesterday
            await rollup_yesterday()
        except Exception as e:
            logger.error(f"[wave_logging] nightly_rollup error: {e}")
            traceback.print_exc()

    @nightly_rollup.before_loop
    async def _before_rollup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WaveLoggingCog(bot))
