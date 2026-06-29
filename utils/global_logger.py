"""
Global Event Logger — Wave Logistics Bot

Mirror of the Wave Manager Bot's core/global_logger.py, but:
  - BOT_NAME = "logistics" (so events route to the right website section)
  - DB_FILE  = "Database/roles.db" (Logistics's existing DB)
  - Supports overriding `bot=` per call so server-event listeners can
    write under the "server" section (Logistics is the designated
    server watcher per the project plan).

Isolated logger namespace (`wave_log`) with propagate=False so events
do NOT flow into any existing handler that would mirror to Discord.
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import aiosqlite

import os

BOT_NAME = "logistics"
# Dynamically find the Wave-Management-Bot folder, assuming it is stored directly next to this bot's folder
_UTILS_DIR = os.path.dirname(os.path.abspath(__file__))
_LOGISTICS_ROOT = os.path.dirname(_UTILS_DIR)
_PARENT_DIR = os.path.dirname(_LOGISTICS_ROOT)
_DB_FILE = os.path.join(_PARENT_DIR, "Wave Management Bot", "bot_database.db")

_diag = logging.getLogger("wave_log")
_diag.propagate = False
if not _diag.handlers:
    _diag.addHandler(logging.NullHandler())


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bot_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp     TEXT    NOT NULL,
    bot           TEXT    NOT NULL,
    category      TEXT    NOT NULL,
    action        TEXT    NOT NULL,
    actor_json    TEXT,
    target_json   TEXT,
    details_json  TEXT,
    guild_id      TEXT,
    guild_name    TEXT,
    pushed_at     TEXT
);
"""

CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_bot_logs_pushed ON bot_logs (pushed_at, id);",
    "CREATE INDEX IF NOT EXISTS idx_bot_logs_bot_cat ON bot_logs (bot, category, timestamp);",
    "CREATE INDEX IF NOT EXISTS idx_bot_logs_guild ON bot_logs (guild_id, timestamp);",
]


async def ensure_table(db_file: str = _DB_FILE) -> None:
    try:
        async with aiosqlite.connect(db_file) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            # Wait up to 30s for any existing writer to release the lock
            # instead of failing instantly with "database is locked".
            await conn.execute("PRAGMA busy_timeout=30000")
            await conn.execute(CREATE_TABLE_SQL)
            for idx_sql in CREATE_INDEX_SQL:
                await conn.execute(idx_sql)
            await conn.commit()
    except Exception as e:
        _diag.error(f"ensure_table failed: {e}")


def _serialize_actor_target(obj: Any) -> Optional[str]:
    """Backward-compat shim — accepts the same inputs as before but now
    delegates to serialize_user() so every actor/target arg gets the full
    fat snapshot automatically (no listener changes required)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return json.dumps(obj, default=str)
    payload = serialize_user(obj)
    if not payload:
        return None
    return json.dumps(payload, default=str)


def _serialize_guild(guild: Any) -> tuple[Optional[str], Optional[str]]:
    if guild is None:
        return None, None
    if isinstance(guild, int):
        return str(guild), None
    gid = getattr(guild, "id", None)
    gname = getattr(guild, "name", None)
    return (str(gid) if gid is not None else None, gname)


# ==================== FAT SERIALIZERS ====================
# All return plain dicts (not JSON strings) so callers can compose them
# into nested `details` payloads. Duck-typed — no `import discord` needed.

def serialize_user(obj: Any) -> Optional[dict]:
    """Full snapshot of a discord.User or discord.Member."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    payload: dict[str, Any] = {}
    for attr in ("id", "name", "discriminator", "global_name", "display_name", "nick"):
        val = getattr(obj, attr, None)
        if val is not None:
            payload[attr] = str(val) if attr == "id" else val
    for attr in ("bot", "system", "pending"):
        val = getattr(obj, attr, None)
        if val is not None:
            payload[attr] = bool(val)
    for img_attr, key in (("display_avatar", "avatar_url"),
                          ("banner", "banner_url")):
        img = getattr(obj, img_attr, None)
        if img is not None:
            url = getattr(img, "url", None)
            if url:
                payload[key] = url
    accent = getattr(obj, "accent_color", None)
    if accent is not None:
        payload["accent_color"] = str(accent)
    pf = getattr(obj, "public_flags", None)
    if pf is not None:
        try:
            payload["public_flags"] = pf.value
            payload["public_flag_names"] = [f[0] for f in pf if f[1]]
        except Exception:
            try:
                payload["public_flags"] = pf.value
            except Exception:
                pass
    for attr in ("created_at", "joined_at", "premium_since", "timed_out_until"):
        dt = getattr(obj, attr, None)
        if dt is not None:
            try:
                payload[attr] = dt.isoformat()
            except Exception:
                pass
    roles = getattr(obj, "roles", None)
    if roles:
        try:
            payload["roles"] = [
                {"id": str(r.id), "name": r.name,
                 "position": getattr(r, "position", None),
                 "color": str(getattr(r, "color", None))}
                for r in roles if getattr(r, "name", None) != "@everyone"
            ]
            top = max(roles, key=lambda r: getattr(r, "position", 0))
            payload["top_role"] = getattr(top, "name", None)
        except Exception:
            pass
    perms = getattr(obj, "guild_permissions", None)
    if perms is not None:
        try:
            payload["guild_permissions"] = perms.value
        except Exception:
            pass
    vs = getattr(obj, "voice", None)
    if vs is not None:
        try:
            v = serialize_voice_state(vs)
            if v:
                payload["voice"] = v
        except Exception:
            pass
    if not payload:
        payload["raw"] = str(obj)
    return payload


def serialize_message(msg: Any) -> Optional[dict]:
    """Full snapshot of a discord.Message — content, attachments, embeds,
    mentions, reactions, reply reference, stickers, components, flags."""
    if msg is None:
        return None
    payload: dict[str, Any] = {
        "id": str(getattr(msg, "id", "")),
        "content": getattr(msg, "content", "") or "",
        "tts": bool(getattr(msg, "tts", False)),
        "pinned": bool(getattr(msg, "pinned", False)),
        "type": str(getattr(msg, "type", None)),
        "jump_url": getattr(msg, "jump_url", None),
    }
    ch = getattr(msg, "channel", None)
    if ch is not None:
        payload["channel_id"] = str(getattr(ch, "id", ""))
        payload["channel_name"] = getattr(ch, "name", None)
    author = getattr(msg, "author", None)
    if author is not None:
        payload["author"] = serialize_user(author)
    flags = getattr(msg, "flags", None)
    if flags is not None:
        try:
            payload["flags"] = flags.value
        except Exception:
            pass
    for attr in ("created_at", "edited_at"):
        dt = getattr(msg, attr, None)
        if dt is not None:
            try:
                payload[attr] = dt.isoformat()
            except Exception:
                pass
    atts = getattr(msg, "attachments", None) or []
    if atts:
        payload["attachments"] = []
        for a in atts:
            try:
                is_spoiler = a.is_spoiler() if callable(getattr(a, "is_spoiler", None)) \
                             else bool(getattr(a, "is_spoiler", False))
            except Exception:
                is_spoiler = False
            payload["attachments"].append({
                "id": str(getattr(a, "id", "")),
                "filename": getattr(a, "filename", None),
                "url": getattr(a, "url", None),
                "proxy_url": getattr(a, "proxy_url", None),
                "size": getattr(a, "size", None),
                "content_type": getattr(a, "content_type", None),
                "width": getattr(a, "width", None),
                "height": getattr(a, "height", None),
                "is_spoiler": is_spoiler,
            })
    embeds = getattr(msg, "embeds", None) or []
    if embeds:
        try:
            payload["embeds"] = [e.to_dict() for e in embeds if hasattr(e, "to_dict")]
        except Exception:
            pass
    payload["mentions"] = {
        "users":    [{"id": str(u.id), "name": str(u)}
                     for u in (getattr(msg, "mentions", None) or [])],
        "roles":    [{"id": str(r.id), "name": getattr(r, "name", None)}
                     for r in (getattr(msg, "role_mentions", None) or [])],
        "channels": [{"id": str(c.id), "name": getattr(c, "name", None)}
                     for c in (getattr(msg, "channel_mentions", None) or [])],
        "everyone": bool(getattr(msg, "mention_everyone", False)),
    }
    reactions = getattr(msg, "reactions", None) or []
    if reactions:
        payload["reactions"] = [
            {"emoji": str(getattr(r, "emoji", None)),
             "count": getattr(r, "count", None),
             "me": bool(getattr(r, "me", False))}
            for r in reactions
        ]
    ref = getattr(msg, "reference", None)
    if ref is not None:
        payload["reference"] = {
            "message_id": str(getattr(ref, "message_id", "") or "") or None,
            "channel_id": str(getattr(ref, "channel_id", "") or "") or None,
            "guild_id":   str(getattr(ref, "guild_id", "") or "") or None,
        }
    stickers = getattr(msg, "stickers", None) or []
    if stickers:
        payload["stickers"] = [
            {"id": str(getattr(s, "id", "")),
             "name": getattr(s, "name", None),
             "format": str(getattr(s, "format", None))}
            for s in stickers
        ]
    components = getattr(msg, "components", None) or []
    if components:
        try:
            payload["components"] = [
                c.to_dict() if hasattr(c, "to_dict") else str(c)
                for c in components
            ]
        except Exception:
            pass
    return payload


def serialize_channel(ch: Any) -> Optional[dict]:
    """Full snapshot of a discord channel — text/voice/thread/category fields,
    permission overwrites."""
    if ch is None:
        return None
    payload: dict[str, Any] = {
        "id":   str(getattr(ch, "id", "")),
        "name": getattr(ch, "name", None),
        "type": str(getattr(ch, "type", None)),
        "position": getattr(ch, "position", None),
    }
    category = getattr(ch, "category", None)
    if category is not None:
        payload["category"] = {"id": str(category.id), "name": category.name}
    elif getattr(ch, "category_id", None):
        payload["category_id"] = str(ch.category_id)
    for attr in ("topic", "nsfw", "slowmode_delay", "default_auto_archive_duration"):
        val = getattr(ch, attr, None)
        if val is not None:
            payload[attr] = val
    for attr in ("bitrate", "user_limit", "rtc_region", "video_quality_mode"):
        val = getattr(ch, attr, None)
        if val is not None:
            payload[attr] = str(val) if attr in ("rtc_region", "video_quality_mode") else val
    for attr in ("archived", "locked", "auto_archive_duration",
                 "owner_id", "parent_id", "message_count", "member_count"):
        val = getattr(ch, attr, None)
        if val is not None:
            payload[attr] = str(val) if attr in ("owner_id", "parent_id") else val
    overwrites = getattr(ch, "overwrites", None)
    if overwrites:
        try:
            payload["overwrites"] = []
            for target, perm in overwrites.items():
                allow, deny = perm.pair()
                payload["overwrites"].append({
                    "target_type": type(target).__name__.lower(),  # 'role' / 'member'
                    "target_id":   str(getattr(target, "id", "")),
                    "target_name": getattr(target, "name", None),
                    "allow": allow.value,
                    "deny":  deny.value,
                })
        except Exception:
            pass
    return payload


def serialize_role(role: Any) -> Optional[dict]:
    """Full snapshot of a discord.Role — perms (bitmask + named list), tags,
    icon, unicode emoji, position, color, hoist, mentionable, managed."""
    if role is None:
        return None
    payload: dict[str, Any] = {
        "id":          str(getattr(role, "id", "")),
        "name":        getattr(role, "name", None),
        "position":    getattr(role, "position", None),
        "color":       str(getattr(role, "color", None)),
        "hoist":       bool(getattr(role, "hoist", False)),
        "mentionable": bool(getattr(role, "mentionable", False)),
        "managed":     bool(getattr(role, "managed", False)),
    }
    perms = getattr(role, "permissions", None)
    if perms is not None:
        try:
            payload["permissions"] = perms.value
            payload["permission_names"] = [n for n, v in perms if v]
        except Exception:
            pass
    icon = getattr(role, "icon", None)
    if icon is not None:
        url = getattr(icon, "url", None)
        if url:
            payload["icon_url"] = url
    ue = getattr(role, "unicode_emoji", None)
    if ue:
        payload["unicode_emoji"] = ue
    tags = getattr(role, "tags", None)
    if tags is not None:
        try:
            payload["tags"] = {
                "bot_id":         str(tags.bot_id) if getattr(tags, "bot_id", None) else None,
                "integration_id": str(tags.integration_id) if getattr(tags, "integration_id", None) else None,
                "is_premium_subscriber":
                    tags.is_premium_subscriber() if hasattr(tags, "is_premium_subscriber") else None,
            }
        except Exception:
            pass
    return payload


def serialize_guild_full(guild: Any) -> Optional[dict]:
    """Full snapshot of a discord.Guild — settings, channels, features, premium."""
    if guild is None or isinstance(guild, int):
        return None
    payload: dict[str, Any] = {
        "id":   str(getattr(guild, "id", "")),
        "name": getattr(guild, "name", None),
    }
    for attr in ("description", "vanity_url_code", "member_count", "max_members",
                 "max_presences", "premium_tier", "premium_subscription_count",
                 "preferred_locale", "afk_timeout", "verification_level",
                 "explicit_content_filter", "default_notifications", "mfa_level",
                 "nsfw_level"):
        val = getattr(guild, attr, None)
        if val is not None:
            payload[attr] = val if isinstance(val, (int, str, bool, float)) else str(val)
    owner_id = getattr(guild, "owner_id", None)
    if owner_id is not None:
        payload["owner_id"] = str(owner_id)
    for attr in ("afk_channel", "system_channel", "rules_channel", "public_updates_channel"):
        c = getattr(guild, attr, None)
        if c is not None:
            payload[attr] = {"id": str(c.id), "name": getattr(c, "name", None)}
    for img_attr, key in (("icon", "icon_url"), ("banner", "banner_url"), ("splash", "splash_url")):
        img = getattr(guild, img_attr, None)
        if img is not None:
            url = getattr(img, "url", None)
            if url:
                payload[key] = url
    features = getattr(guild, "features", None)
    if features is not None:
        try:
            payload["features"] = list(features)
        except Exception:
            pass
    return payload


def serialize_voice_state(vs: Any) -> Optional[dict]:
    """Full snapshot of a discord.VoiceState — channel, session, all booleans."""
    if vs is None:
        return None
    payload: dict[str, Any] = {}
    for attr in ("self_mute", "self_deaf", "self_stream", "self_video",
                 "mute", "deaf", "suppress", "afk"):
        val = getattr(vs, attr, None)
        if val is not None:
            payload[attr] = bool(val)
    session_id = getattr(vs, "session_id", None)
    if session_id:
        payload["session_id"] = session_id
    rts = getattr(vs, "requested_to_speak_at", None)
    if rts is not None:
        try:
            payload["requested_to_speak_at"] = rts.isoformat()
        except Exception:
            pass
    ch = getattr(vs, "channel", None)
    if ch is not None:
        payload["channel"] = {
            "id":   str(getattr(ch, "id", "")),
            "name": getattr(ch, "name", None),
            "type": str(getattr(ch, "type", None)),
        }
    return payload


# ==================== AUDIT LOG ENRICHMENT ====================

async def fetch_audit(
    guild: Any,
    action: Any,
    *,
    target_id: Optional[int] = None,
    actor_id: Optional[int] = None,
    channel_id: Optional[int] = None,
    sleep_seconds: float = 2.0,
) -> Optional[dict]:
    """After a gateway event fires, the matching audit-log entry usually
    appears within ~1s. Sleep `sleep_seconds`, then look it up.

    Returns {audit_id, actor, reason, changes, extra, created_at} or None
    if no matching entry was found, audit-log perm is missing, or the
    guild is None.
    """
    if guild is None or action is None:
        return None
    try:
        if sleep_seconds > 0:
            await asyncio.sleep(sleep_seconds)
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=15)
        async for entry in guild.audit_logs(action=action, limit=10, after=cutoff):
            if target_id is not None:
                etid = getattr(getattr(entry, "target", None), "id", None)
                if etid != target_id:
                    continue
            if actor_id is not None:
                eaid = getattr(getattr(entry, "user", None), "id", None)
                if eaid != actor_id:
                    continue
            if channel_id is not None:
                ech = getattr(getattr(entry, "extra", None), "channel", None)
                if getattr(ech, "id", None) != channel_id:
                    continue
            return _serialize_audit_entry(entry)
    except Exception as e:
        _diag.error(f"fetch_audit({action}) failed: {e}")
    return None


def _serialize_audit_entry(entry: Any) -> dict:
    """Turn an AuditLogEntry into a dict — actor, reason, before/after changes,
    action-specific extra payload."""
    payload: dict[str, Any] = {
        "audit_id":   str(getattr(entry, "id", "")),
        "actor":      serialize_user(getattr(entry, "user", None)),
        "reason":     getattr(entry, "reason", None),
        "created_at": getattr(entry, "created_at", None).isoformat()
                      if getattr(entry, "created_at", None) else None,
    }
    changes = getattr(entry, "changes", None)
    if changes is not None:
        try:
            before = getattr(changes, "before", None)
            after  = getattr(changes, "after", None)
            before_d = {k: v for k, v in before} if before else {}
            after_d  = {k: v for k, v in after}  if after  else {}
            diff: list[dict] = []
            for key in set(before_d) | set(after_d):
                b, a = before_d.get(key), after_d.get(key)
                if b != a:
                    diff.append({
                        "key":    key,
                        "before": str(b) if b is not None else None,
                        "after":  str(a) if a is not None else None,
                    })
            if diff:
                payload["changes"] = diff
        except Exception as e:
            payload["changes_error"] = str(e)
    extra = getattr(entry, "extra", None)
    if extra is not None:
        try:
            if isinstance(extra, dict):
                payload["extra"] = {k: str(v) for k, v in extra.items()}
            elif hasattr(extra, "__dict__"):
                payload["extra"] = {
                    k: str(v) for k, v in extra.__dict__.items()
                    if not k.startswith("_")
                }
            else:
                payload["extra"] = str(extra)
        except Exception:
            pass
    return payload


async def log_event(
    category: str,
    action: str,
    *,
    actor: Any = None,
    target: Any = None,
    details: Optional[dict] = None,
    guild: Any = None,
    bot: str = BOT_NAME,
    db_file: Optional[str] = None,
) -> None:
    """Record one event row. Never raises; failures go to _diag logger."""
    # Resolve DB path at call time so runtime overrides of _DB_FILE work.
    if db_file is None:
        db_file = _DB_FILE
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        actor_json = _serialize_actor_target(actor)
        target_json = _serialize_actor_target(target)
        details_json = json.dumps(details, default=str) if details else None
        guild_id, guild_name = _serialize_guild(guild)

        async with aiosqlite.connect(db_file) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            # Wait up to 30s for any existing writer to release the lock
            # instead of failing instantly with "database is locked".
            await conn.execute("PRAGMA busy_timeout=30000")
            await conn.execute(
                """
                INSERT INTO bot_logs
                    (timestamp, bot, category, action, actor_json,
                     target_json, details_json, guild_id, guild_name, pushed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (ts, bot, category, action, actor_json, target_json,
                 details_json, guild_id, guild_name),
            )
            await conn.commit()
    except Exception as e:
        _diag.error(f"log_event({category}/{action}) failed: {e}")


def log_event_sync(
    category: str,
    action: str,
    *,
    actor: Any = None,
    target: Any = None,
    details: Optional[dict] = None,
    guild: Any = None,
    bot: str = BOT_NAME,
) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(
        log_event(category, action, actor=actor, target=target,
                  details=details, guild=guild, bot=bot)
    )


# ==================== TERMINAL LOG CAPTURE ====================

class WaveLoggingHandler(logging.Handler):
    """Mirror of the Manager-bot WaveLoggingHandler. Captures every log
    record (above the configured threshold) and writes a row to bot_logs
    under category='terminal_logs' so the dashboard's Terminal Logs tab
    populates. Skips `wave_log` and `aiosqlite` to prevent recursion."""

    _SKIP_PREFIXES = ("wave_log", "aiosqlite")

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if any(record.name == p or record.name.startswith(p + ".") for p in self._SKIP_PREFIXES):
                return
            log_event_sync(
                category="terminal_logs",
                action=record.levelname.lower(),
                details={
                    "logger": record.name,
                    "level": record.levelname,
                    "message": record.getMessage()[:2000],
                    "module": record.module,
                    "func": record.funcName,
                    "line": record.lineno,
                },
                bot=BOT_NAME,
            )
        except Exception:
            pass


def install_terminal_log_capture(level: int = logging.INFO) -> WaveLoggingHandler:
    """Attach a WaveLoggingHandler to the root logger. Idempotent."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, WaveLoggingHandler):
            return h
    handler = WaveLoggingHandler(level=level)
    root.addHandler(handler)
    return handler


async def fetch_unpushed(limit: int = 5000, db_file: Optional[str] = None) -> list[dict]:
    if db_file is None:
        db_file = _DB_FILE
    try:
        async with aiosqlite.connect(db_file) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA journal_mode=WAL")
            cursor = await conn.execute(
                """
                SELECT id, timestamp, bot, category, action,
                       actor_json, target_json, details_json,
                       guild_id, guild_name
                FROM bot_logs
                WHERE pushed_at IS NULL
                ORDER BY id ASC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [_row_to_event(r) for r in rows]
    except Exception as e:
        _diag.error(f"fetch_unpushed failed: {e}")
        return []


async def mark_pushed(row_ids: list[int], db_file: Optional[str] = None) -> None:
    if db_file is None:
        db_file = _DB_FILE
    if not row_ids:
        return
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        placeholders = ",".join("?" for _ in row_ids)
        async with aiosqlite.connect(db_file) as conn:
            await conn.execute("PRAGMA journal_mode=WAL")
            # Wait up to 30s for any existing writer to release the lock
            # instead of failing instantly with "database is locked".
            await conn.execute("PRAGMA busy_timeout=30000")
            await conn.execute(
                f"UPDATE bot_logs SET pushed_at = ? WHERE id IN ({placeholders})",
                (ts, *row_ids),
            )
            await conn.commit()
    except Exception as e:
        _diag.error(f"mark_pushed failed: {e}")


def _row_to_event(row: aiosqlite.Row) -> dict:
    return {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "bot": row["bot"],
        "category": row["category"],
        "action": row["action"],
        "actor": json.loads(row["actor_json"]) if row["actor_json"] else None,
        "target": json.loads(row["target_json"]) if row["target_json"] else None,
        "details": json.loads(row["details_json"]) if row["details_json"] else None,
        "guild_id": row["guild_id"],
        "guild_name": row["guild_name"],
    }
