"""
Improved database layer with connection pooling for Wave Logistics Bot.

This module provides a connection pool to avoid opening/closing connections
for every database operation, improving performance.
"""

import aiosqlite
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import asyncio
from utils.queue_encoding import number_to_alpha, alpha_to_number

# Wave-Logging dashboard event emitter. log_event is async, never raises
# (errors go to its own diagnostic logger), and uses an isolated logger
# namespace so DiscordTerminalHandler never sees it.
from utils.global_logger import log_event as _wave_log_event

DB_PATH = "Database/roles.db"

# Connection pool
_db_pool: Optional[aiosqlite.Connection] = None
_lock = asyncio.Lock()

async def get_db() -> aiosqlite.Connection:
    """Get a database connection from the pool (creates if not exists)."""
    global _db_pool
    
    async with _lock:
        if _db_pool is None:
            # Ensure directory exists
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            
            # Create connection with connection pool settings
            _db_pool = await aiosqlite.connect(
                DB_PATH,
                isolation_level=None,  # Use autocommit mode for better performance
                check_same_thread=False
            )
            # Enable WAL mode for better concurrency
            await _db_pool.execute("PRAGMA journal_mode=WAL")
            await _db_pool.execute("PRAGMA synchronous=NORMAL")
            await _db_pool.execute("PRAGMA cache_size=-2000")  # 2MB cache
            # Wait up to 30s for other writers (Tasks/wave_logging.py opens
            # its own connections for bot_logs) instead of failing with
            # "database is locked".
            await _db_pool.execute("PRAGMA busy_timeout=30000")
            await _db_pool.commit()
            
            # Initialize tables
            await _init_tables(_db_pool)
    
    return _db_pool

async def _init_tables(db: aiosqlite.Connection):
    """Initialize database tables."""
    await db.execute('''
        CREATE TABLE IF NOT EXISTS tracked_roles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_type TEXT NOT NULL,
            assigned_at TEXT NOT NULL,
            warned INTEGER DEFAULT 0
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS role_streaks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role_type TEXT NOT NULL,
            assigned_at TEXT NOT NULL
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS map_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            queue_number INTEGER NOT NULL,
            image_url TEXT NOT NULL,
            user_ids TEXT NOT NULL,
            description TEXT,
            map_type TEXT,
            route_type TEXT DEFAULT 'loot_route',
            message_id TEXT,
            backup_message_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            UNIQUE(guild_id, queue_number)
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS server_queue_config (
            guild_id INTEGER PRIMARY KEY,
            queue_channel_id INTEGER,
            server_mode TEXT DEFAULT 'drop_map',
            last_queue_number INTEGER DEFAULT 0,
            enable_queue_notifications INTEGER DEFAULT 0,
            sticky_message_id INTEGER
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS allowed_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            added_by INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            UNIQUE(guild_id, channel_id)
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS dm_config (
            guild_id INTEGER PRIMARY KEY,
            dm_channel_id INTEGER,
            dm_log_channel_id INTEGER,
            dm_template_drop_map TEXT DEFAULT 'Hey {mention} 👋\n\nYour **requested dropmap** is complete, you can find it over at {link}\n\nIf you want wave to keep **making more drop maps**, you can **support wave **by giving a vouch in https://discord.com/channels/988564962802810961/1210814682357698621 and say something you like about the server! \n\nAny and all vouches are** greatly appreciated**, thank you for choosing wave!',
            dm_template_loot_route TEXT DEFAULT 'Hey {mention} 👋\n\nYour **requested loot route** is complete, you can find it here: {link}\n\nIf you''d like Wave to keep **making __free__ loot routes**, consider leaving a vouch in https://discord.com/channels/971731167621574666/1132639885883359302 and sharing what you **enjoy about the server.**\n\nIt really helps and is **greatly appreciated **— thanks for choosing Wave!',
            dm_template_surge_route TEXT DEFAULT 'Hey {mention} 👋\n\nYour **requested surge route** is complete, you can find it here: {link}\n\nIf you''d like Wave to keep **making __free__ surge routes**, consider leaving a vouch in https://discord.com/channels/971731167621574666/1132639885883359302 and sharing what you **enjoy about the server.**\n\nIt really helps and is **greatly appreciated **— thanks for choosing Wave!',
            enabled INTEGER DEFAULT 1
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS hitl_claim_state (
            message_id            INTEGER PRIMARY KEY,
            guild_id              INTEGER NOT NULL,
            channel_id            INTEGER NOT NULL,
            claimed_by            INTEGER,
            claimed_at            REAL,
            resolved              INTEGER DEFAULT 0,
            start_node            TEXT,
            valid_classes_json    TEXT,
            hitl_filenames_json   TEXT,
            original_user_id      INTEGER,
            original_channel_id   INTEGER,
            original_message_id   INTEGER
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS hitl_sticky (
            guild_id    INTEGER PRIMARY KEY,
            channel_id  INTEGER NOT NULL,
            message_id  INTEGER NOT NULL
        )
    ''')
    await db.execute('''
        CREATE TABLE IF NOT EXISTS stolen_detections (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id              INTEGER NOT NULL,
            user_id               INTEGER NOT NULL,
            message_id            INTEGER NOT NULL,
            kind                  TEXT NOT NULL,
            match_type            TEXT,
            original_user_id      INTEGER,
            original_guild_id     INTEGER,
            original_message_id   INTEGER,
            original_submitted_at REAL,
            original_filename     TEXT,
            distance              INTEGER,
            mirror                INTEGER DEFAULT 0,
            detail                TEXT,
            detected_at           REAL NOT NULL
        )
    ''')
    
    # Migration: Ensure enabled column exists (for existing tables)
    async with db.execute("PRAGMA table_info(dm_config)") as cursor:
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if "enabled" not in column_names:
            print("Database migration: Adding 'enabled' column to dm_config table")
            await db.execute("ALTER TABLE dm_config ADD COLUMN enabled INTEGER DEFAULT 1")
    
    # Migration: Ensure enable_queue_notifications column exists (for existing tables)
    async with db.execute("PRAGMA table_info(server_queue_config)") as cursor:
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if "enable_queue_notifications" not in column_names:
            print("Database migration: Adding 'enable_queue_notifications' column to server_queue_config table")
            await db.execute("ALTER TABLE server_queue_config ADD COLUMN enable_queue_notifications INTEGER DEFAULT 0")

    # Migration: Ensure sticky_message_id column exists (for existing tables)
    async with db.execute("PRAGMA table_info(server_queue_config)") as cursor:
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if "sticky_message_id" not in column_names:
            print("Database migration: Adding 'sticky_message_id' column to server_queue_config table")
            await db.execute("ALTER TABLE server_queue_config ADD COLUMN sticky_message_id INTEGER")

    # Migration: Ensure route_type column exists in map_requests (for existing tables)
    async with db.execute("PRAGMA table_info(map_requests)") as cursor:
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if "route_type" not in column_names:
            print("Database migration: Adding 'route_type' column to map_requests table")
            await db.execute("ALTER TABLE map_requests ADD COLUMN route_type TEXT DEFAULT 'loot_route'")
        # Cross-bot surge bridge: marks a surge_route entry that has been dispatched to the
        # Management bot's surge-maps channel, so the reconciliation sweep never double-posts.
        if "dispatched_at" not in column_names:
            print("Database migration: Adding 'dispatched_at' column to map_requests table")
            await db.execute("ALTER TABLE map_requests ADD COLUMN dispatched_at TEXT")

    # Migration: Ensure dm_template_surge_route column exists in dm_config (for existing tables)
    async with db.execute("PRAGMA table_info(dm_config)") as cursor:
        columns = await cursor.fetchall()
        column_names = [col[1] for col in columns]
        if "dm_template_surge_route" not in column_names:
            print("Database migration: Adding 'dm_template_surge_route' column to dm_config table")
            await db.execute(
                "ALTER TABLE dm_config ADD COLUMN dm_template_surge_route TEXT DEFAULT "
                "'Hey {mention} 👋\n\nYour **requested surge route** is complete, you can find it here: {link}\n\n"
                "If you''d like Wave to keep **making __free__ surge routes**, consider leaving a vouch in "
                "https://discord.com/channels/971731167621574666/1132639885883359302 and sharing what you **enjoy about the server.**\n\n"
                "It really helps and is **greatly appreciated **— thanks for choosing Wave!'"
            )
    
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_tracked_roles_guild_user_type
        ON tracked_roles(guild_id, user_id, role_type)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_allowed_channels_guild
        ON allowed_channels(guild_id)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_role_streaks_guild_user_type
        ON role_streaks(guild_id, user_id, role_type)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_map_requests_guild_number
        ON map_requests(guild_id, queue_number)
    ''')
    await db.execute('''
        CREATE INDEX IF NOT EXISTS idx_map_requests_guild_status
        ON map_requests(guild_id, status)
    ''')
    await db.commit()

async def close_db():
    """Close the database connection pool."""
    global _db_pool
    if _db_pool is not None:
        await _db_pool.close()
        _db_pool = None

async def log_stolen_detection(
    guild_id: int,
    user_id: int,
    message_id: int,
    kind: str,
    match_type: str = None,
    original_user_id: int = None,
    original_guild_id: int = None,
    original_message_id: int = None,
    original_submitted_at: float = None,
    original_filename: str = None,
    distance: int = None,
    mirror: int = 0,
    detail: str = None,
) -> None:
    """Write rich stolen-proof detection details to the forensic log table."""
    db = await get_db()
    await db.execute(
        '''
        INSERT INTO stolen_detections (
            guild_id, user_id, message_id, kind, match_type,
            original_user_id, original_guild_id, original_message_id,
            original_submitted_at, original_filename, distance, mirror,
            detail, detected_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            guild_id, user_id, message_id, kind, match_type,
            original_user_id, original_guild_id, original_message_id,
            original_submitted_at, original_filename, distance, int(bool(mirror)),
            detail, datetime.now(timezone.utc).timestamp()
        ),
    )
    await db.commit()

# ── HITL claim state helpers ─────────────────────────────────────────────────

async def register_hitl_review(
    message_id: int,
    guild_id: int,
    channel_id: int,
    start_node: str,
    valid_classes: list,
    hitl_filenames: list,
    original_user_id: int,
    original_channel_id: int,
    original_message_id: int,
) -> None:
    db = await get_db()
    await db.execute(
        '''
        INSERT OR IGNORE INTO hitl_claim_state
            (message_id, guild_id, channel_id, start_node, valid_classes_json,
             hitl_filenames_json, original_user_id, original_channel_id, original_message_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            message_id, guild_id, channel_id, start_node,
            json.dumps(valid_classes), json.dumps(hitl_filenames),
            original_user_id, original_channel_id, original_message_id,
        ),
    )
    await db.commit()


async def claim_hitl(message_id: int, user_id: int) -> bool:
    """Atomically claim a review. Returns True if this caller got the claim."""
    import time
    db = await get_db()
    cursor = await db.execute(
        '''
        UPDATE hitl_claim_state
        SET claimed_by = ?, claimed_at = ?
        WHERE message_id = ? AND claimed_by IS NULL AND resolved = 0
        ''',
        (user_id, time.time(), message_id),
    )
    await db.commit()
    return cursor.rowcount > 0


async def release_hitl_claim(message_id: int) -> None:
    db = await get_db()
    await db.execute(
        'UPDATE hitl_claim_state SET claimed_by = NULL, claimed_at = NULL WHERE message_id = ?',
        (message_id,),
    )
    await db.commit()


async def update_hitl_node(message_id: int, new_start_node: str, new_valid_classes: List[str]) -> None:
    """Update an existing claimed HITL review so the UI can proceed to the next step seamlessly."""
    db = await get_db()
    import json
    await db.execute(
        '''
        UPDATE hitl_claim_state 
        SET start_node = ?, valid_classes_json = ? 
        WHERE message_id = ?
        ''',
        (new_start_node, json.dumps(new_valid_classes), message_id),
    )
    await db.commit()


async def resolve_hitl(message_id: int) -> None:
    db = await get_db()
    await db.execute(
        'UPDATE hitl_claim_state SET resolved = 1 WHERE message_id = ?',
        (message_id,),
    )
    await db.commit()


async def get_hitl_claim(message_id: int) -> Optional[Dict[str, Any]]:
    """Return claim state for a single HITL review, or None if it doesn't exist."""
    db = await get_db()
    async with db.execute(
        '''
        SELECT message_id, claimed_by, claimed_at, resolved
        FROM hitl_claim_state
        WHERE message_id = ?
        ''',
        (message_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            'message_id': row[0],
            'claimed_by_id': row[1],
            'claimed_at': row[2],
            'resolved': bool(row[3]),
        }


async def get_pending_hitl(guild_id: int) -> List[Dict[str, Any]]:
    """Unresolved HITL reviews for a guild, oldest first."""
    db = await get_db()
    async with db.execute(
        '''
        SELECT message_id, guild_id, channel_id, claimed_by, claimed_at,
               original_user_id, original_channel_id, original_message_id
        FROM hitl_claim_state
        WHERE guild_id = ? AND resolved = 0
        ORDER BY message_id ASC
        ''',
        (guild_id,),
    ) as cursor:
        rows = await cursor.fetchall()
        return [
            {
                'message_id': r[0], 'guild_id': r[1], 'channel_id': r[2],
                'claimed_by': r[3], 'claimed_at': r[4],
                'original_user_id': r[5], 'original_channel_id': r[6],
                'original_message_id': r[7],
            }
            for r in rows
        ]


async def get_all_unresolved_hitl() -> List[Dict[str, Any]]:
    """All unresolved HITL reviews across all guilds (for startup view re-registration)."""
    db = await get_db()
    async with db.execute(
        '''
        SELECT message_id, guild_id, channel_id, start_node,
               valid_classes_json, hitl_filenames_json,
               original_user_id, original_channel_id, original_message_id
        FROM hitl_claim_state
        WHERE resolved = 0
        ORDER BY message_id ASC
        ''',
    ) as cursor:
        rows = await cursor.fetchall()
        return [
            {
                'message_id': r[0], 'guild_id': r[1], 'channel_id': r[2],
                'start_node': r[3],
                'valid_classes': json.loads(r[4]) if r[4] else [],
                'hitl_filenames': json.loads(r[5]) if r[5] else [],
                'original_user_id': r[6], 'original_channel_id': r[7],
                'original_message_id': r[8],
            }
            for r in rows
        ]


async def get_hitl_sticky(guild_id: int) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        'SELECT channel_id, message_id FROM hitl_sticky WHERE guild_id = ?',
        (guild_id,),
    ) as cursor:
        row = await cursor.fetchone()
        return {'channel_id': row[0], 'message_id': row[1]} if row else None


async def set_hitl_sticky(guild_id: int, channel_id: int, message_id: int) -> None:
    db = await get_db()
    await db.execute(
        '''
        INSERT INTO hitl_sticky (guild_id, channel_id, message_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id, message_id=excluded.message_id
        ''',
        (guild_id, channel_id, message_id),
    )
    await db.commit()


# Core database operations with connection pooling
async def increment_streak(guild_id: int, user_id: int, role_type: str):
    """Record a new streak entry each time a user receives a tracked role."""
    db = await get_db()
    await db.execute('''
        INSERT INTO role_streaks (guild_id, user_id, role_type, assigned_at)
        VALUES (?, ?, ?, ?)
    ''', (guild_id, user_id, role_type, datetime.now(timezone.utc).isoformat()))
    await db.commit()
    # Wave-Logging dashboard event
    await _wave_log_event(
        category="streaks",
        action="streak_incremented",
        target={"id": str(user_id)},
        guild=guild_id,
        details={"role_type": role_type},
    )

async def get_streak(guild_id: int, user_id: int, role_type: str) -> List[str]:
    """Return all streak entries for a user/role in a guild, oldest first."""
    db = await get_db()
    async with db.execute('''
        SELECT assigned_at FROM role_streaks
        WHERE guild_id = ? AND user_id = ? AND role_type = ?
        ORDER BY assigned_at ASC
    ''', (guild_id, user_id, role_type)) as cursor:
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def add_tracked_role(guild_id, user_id, role_type, *, member=None, guild_obj=None):
    db = await get_db()
    now = datetime.now(timezone.utc)
    await db.execute('''
        INSERT INTO tracked_roles (guild_id, user_id, role_type, assigned_at, warned)
        VALUES (?, ?, ?, ?, 0)
    ''', (guild_id, user_id, role_type, now.isoformat()))
    await db.commit()
    # Wave-Logging dashboard event — routes by role_type so priority and
    # contributor each land in their own tab on the website.
    _category = "priority_tracking" if role_type == "priority" else "contributor_tracking"
    await _wave_log_event(
        category=_category,
        action="role_assigned",
        target=member or {"id": str(user_id)},
        guild=guild_obj or guild_id,
        details={
            "role_type": role_type,
            "username": getattr(member, 'display_name', None) or getattr(member, 'name', None),
            "guild_name": getattr(guild_obj, 'name', None),
            "assigned_at": now.isoformat(),
            "expires_at": (now + timedelta(days=30)).isoformat(),
        },
    )

async def add_tracked_role_with_time(guild_id, user_id, role_type, assigned_at: datetime, *, member=None, guild_obj=None):
    db = await get_db()
    await db.execute('''
        INSERT INTO tracked_roles (guild_id, user_id, role_type, assigned_at, warned)
        VALUES (?, ?, ?, ?, 0)
    ''', (guild_id, user_id, role_type, assigned_at.isoformat()))
    await db.commit()
    _category = "priority_tracking" if role_type == "priority" else "contributor_tracking"
    expires_at = (assigned_at + timedelta(days=30)).isoformat()
    await _wave_log_event(
        category=_category,
        action="role_assigned_backfill",
        target=member or {"id": str(user_id)},
        guild=guild_obj or guild_id,
        details={
            "role_type": role_type,
            "username": getattr(member, 'display_name', None) or getattr(member, 'name', None),
            "guild_name": getattr(guild_obj, 'name', None),
            "assigned_at": assigned_at.isoformat(),
            "expires_at": expires_at,
        },
    )

async def remove_tracked_role(guild_id, user_id, role_type, *, member=None, guild_obj=None, removal_reason=None, days_elapsed=None):
    db = await get_db()
    await db.execute('''
        DELETE FROM tracked_roles
        WHERE guild_id = ? AND user_id = ? AND role_type = ?
    ''', (guild_id, user_id, role_type))
    await db.commit()
    _category = "priority_tracking" if role_type == "priority" else "contributor_tracking"
    await _wave_log_event(
        category=_category,
        action="role_removed",
        target=member or {"id": str(user_id)},
        guild=guild_obj or guild_id,
        details={
            "role_type": role_type,
            "username": getattr(member, 'display_name', None) or getattr(member, 'name', None),
            "guild_name": getattr(guild_obj, 'name', None),
            "removal_reason": removal_reason,
            "days_elapsed": days_elapsed,
        },
    )

async def get_tracked_role(guild_id, user_id, role_type) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute('''
        SELECT * FROM tracked_roles
        WHERE guild_id = ? AND user_id = ? AND role_type = ?
    ''', (guild_id, user_id, role_type)) as cursor:
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "guild_id": row[1],
                "user_id": row[2],
                "role_type": row[3],
                "assigned_at": row[4],
                "warned": row[5]
            }
        return None

async def get_all_tracked_roles():
    db = await get_db()
    async with db.execute('SELECT * FROM tracked_roles') as cursor:
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "guild_id": row[1],
                "user_id": row[2],
                "role_type": row[3],
                "assigned_at": row[4],
                "warned": row[5]
            }
            for row in rows
        ]

async def get_expiring_roles(days_before: int = 3):
    """Get roles that will expire within the specified number of days."""
    db = await get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=30 - days_before)
    async with db.execute('''
        SELECT * FROM tracked_roles
        WHERE datetime(assigned_at) < datetime(?)
        AND warned = 0
    ''', (cutoff.isoformat(),)) as cursor:
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "guild_id": row[1],
                "user_id": row[2],
                "role_type": row[3],
                "assigned_at": row[4],
                "warned": row[5]
            }
            for row in rows
        ]

async def mark_warned(role_id: int):
    """Mark a tracked role as warned."""
    db = await get_db()
    await db.execute('''
        UPDATE tracked_roles SET warned = 1 WHERE id = ?
    ''', (role_id,))
    await db.commit()

# Backward compatibility
async def init_db():
    """Initialize database (for backward compatibility)."""
    await get_db()

# Context manager for transactions
class Transaction:
    """Context manager for database transactions."""
    
    def __init__(self):
        self.db = None
        
    async def __aenter__(self) -> aiosqlite.Connection:
        self.db = await get_db()
        await self.db.execute("BEGIN")
        return self.db
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.db:
            if exc_type is None:
                await self.db.commit()
            else:
                await self.db.rollback()

# Map Request Queue Functions
async def get_server_queue_config(guild_id: int) -> Optional[Dict[str, Any]]:
    """Get server queue configuration."""
    db = await get_db()
    async with db.execute('''
        SELECT * FROM server_queue_config
        WHERE guild_id = ?
    ''', (guild_id,)) as cursor:
        row = await cursor.fetchone()
        if row:
            # Handle both old schema (4 columns) and new schema (5+ columns)
            config = {
                "guild_id": row[0],
                "queue_channel_id": row[1],
                "server_mode": row[2],
                "last_queue_number": row[3]
            }
            # Add enable_queue_notifications if column exists (index 4)
            if len(row) > 4:
                config["enable_queue_notifications"] = row[4]
            else:
                config["enable_queue_notifications"] = 0  # Default to disabled
            # Add sticky_message_id if column exists (index 5)
            if len(row) > 5:
                config["sticky_message_id"] = row[5]
            else:
                config["sticky_message_id"] = None  # Default to None
            return config
        return None

async def set_server_queue_config(guild_id: int, queue_channel_id: Optional[int] = None,
                                  server_mode: Optional[str] = None,
                                  enable_queue_notifications: Optional[int] = None) -> bool:
    """Set server queue configuration."""
    db = await get_db()
    
    # Check if config exists
    config = await get_server_queue_config(guild_id)
    
    if config:
        # Update existing config
        updates = []
        params = []
        
        if queue_channel_id is not None:
            updates.append("queue_channel_id = ?")
            params.append(queue_channel_id)
        
        if server_mode is not None:
            updates.append("server_mode = ?")
            params.append(server_mode)
        
        if enable_queue_notifications is not None:
            updates.append("enable_queue_notifications = ?")
            params.append(enable_queue_notifications)
        
        if updates:
            params.append(guild_id)
            query = f"UPDATE server_queue_config SET {', '.join(updates)} WHERE guild_id = ?"
            await db.execute(query, params)
            await db.commit()
            return True
        return False
    else:
        # Insert new config - use default 0 for enable_queue_notifications if not specified
        enable_notifications = enable_queue_notifications if enable_queue_notifications is not None else 0
        await db.execute('''
            INSERT INTO server_queue_config (guild_id, queue_channel_id, server_mode, enable_queue_notifications)
            VALUES (?, ?, ?, ?)
        ''', (guild_id, queue_channel_id, server_mode or 'drop_map', enable_notifications))
        await db.commit()
        return True

async def get_sticky_message_id(guild_id: int) -> Optional[int]:
    """Get the sticky message ID for a guild's queue channel."""
    db = await get_db()
    async with db.execute(
        "SELECT sticky_message_id FROM server_queue_config WHERE guild_id = ?",
        (guild_id,)
    ) as cursor:
        row = await cursor.fetchone()
        return row[0] if row and row[0] else None

async def set_sticky_message_id(guild_id: int, message_id: Optional[int]):
    """Update the sticky message ID for a guild's queue channel."""
    db = await get_db()
    await db.execute(
        "UPDATE server_queue_config SET sticky_message_id = ? WHERE guild_id = ?",
        (message_id, guild_id)
    )
    await db.commit()

async def get_next_queue_number(guild_id: int) -> str:
    """Get the next queue code for a guild (alphabetical: a, b, c, ..., aa, ab, etc.)."""
    db = await get_db()
    
    # First, get all existing queue numbers for this guild.
    # Count ALL rows regardless of status (not just 'active'): removals are now a
    # soft delete (status='removed', see remove_map_request), and those rows linger
    # in the table. Filtering to 'active' here would let a removed code be handed out
    # again and collide with the lingering row on UNIQUE(guild_id, queue_number).
    existing_codes = []
    async with db.execute('''
        SELECT queue_number FROM map_requests
        WHERE guild_id = ?
    ''', (guild_id,)) as cursor:
        rows = await cursor.fetchall()
        for row in rows:
            existing_codes.append(row[0])
    
    # Convert existing codes to numbers and find the maximum
    max_number = 0
    for code in existing_codes:
        try:
            if isinstance(code, str):
                num = alpha_to_number(code)
            elif isinstance(code, int):
                num = code
            else:
                continue
            if num > max_number:
                max_number = num
        except Exception:
            # Skip invalid codes
            continue
    
    # Get current last queue number from config
    async with db.execute('''
        SELECT last_queue_number FROM server_queue_config
        WHERE guild_id = ?
    ''', (guild_id,)) as cursor:
        row = await cursor.fetchone()
        if row:
            config_last = row[0]
            # Use the maximum of (max_number + 1) and (config_last + 1)
            next_number = max(max_number + 1, config_last + 1 if config_last is not None else 1)
        else:
            # No config exists, use max_number + 1 or start from 1
            next_number = max_number + 1 if max_number > 0 else 1
    
    # Update the last queue number in config
    # First ensure config exists
    config = await get_server_queue_config(guild_id)
    if not config:
        await set_server_queue_config(guild_id)
    
    await db.execute('''
        UPDATE server_queue_config
        SET last_queue_number = ?
        WHERE guild_id = ?
    ''', (next_number, guild_id))
    await db.commit()
    
    # Convert to alphabetical code
    return number_to_alpha(next_number)

async def add_map_request(guild_id: int, queue_number: str, image_url: str,
                         user_ids: List[int], description: str = None,
                         map_type: str = None, route_type: str = 'loot_route') -> bool:
    """Add a map request to the queue with alphabetical queue code."""
    db = await get_db()

    # Convert user_ids list to JSON string
    user_ids_json = json.dumps(user_ids)

    try:
        await db.execute('''
            INSERT INTO map_requests
            (guild_id, queue_number, image_url, user_ids, description, map_type, route_type,
             created_at, updated_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
        ''', (guild_id, queue_number, image_url, user_ids_json, description, map_type, route_type,
              datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()))
        await db.commit()
        # Wave-Logging dashboard event
        await _wave_log_event(
            category="map_queue",
            action="map_request_added",
            guild=guild_id,
            details={
                "queue_code": queue_number,
                "user_ids": user_ids,
                "description": description,
                "map_type": map_type,
                "route_type": route_type,
                "has_image": bool(image_url),
            },
        )
        return True
    except aiosqlite.IntegrityError:
        # Queue number already exists for this guild
        return False

async def update_map_request_message(guild_id: int, queue_number: str,
                                    message_id: str, backup_message_id: str = None) -> bool:
    """Update message IDs for a map request."""
    db = await get_db()
    
    updates = ["message_id = ?", "updated_at = ?"]
    params = [message_id, datetime.now(timezone.utc).isoformat()]
    
    if backup_message_id:
        updates.append("backup_message_id = ?")
        params.append(backup_message_id)
    
    params.extend([guild_id, queue_number])
    
    query = f'''
        UPDATE map_requests
        SET {', '.join(updates)}
        WHERE guild_id = ? AND queue_number = ?
    '''

    # cursor.rowcount = rows matched by THIS statement; db.total_changes is
    # cumulative for the pooled connection and is always > 0 after any write.
    cursor = await db.execute(query, params)
    await db.commit()
    return cursor.rowcount > 0

async def get_map_request(guild_id: int, queue_number: str) -> Optional[Dict[str, Any]]:
    """Get a map request by queue code (alphabetical)."""
    db = await get_db()
    async with db.execute('''
        SELECT id, guild_id, queue_number, image_url, user_ids, description, map_type,
               route_type, message_id, backup_message_id, created_at, updated_at, status
        FROM map_requests
        WHERE guild_id = ? AND queue_number = ?
    ''', (guild_id, queue_number)) as cursor:
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "guild_id": row[1],
                "queue_number": row[2],
                "image_url": row[3],
                "user_ids": json.loads(row[4]) if row[4] else [],
                "description": row[5],
                "map_type": row[6],
                "route_type": row[7] if row[7] else "loot_route",
                "message_id": row[8],
                "backup_message_id": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "status": row[12],
            }
        return None

async def get_all_map_requests(guild_id: int, status: str = 'active') -> List[Dict[str, Any]]:
    """Get all map requests for a guild."""
    db = await get_db()
    async with db.execute('''
        SELECT id, guild_id, queue_number, image_url, user_ids, description, map_type,
               route_type, message_id, backup_message_id, created_at, updated_at, status
        FROM map_requests
        WHERE guild_id = ? AND status = ?
        ORDER BY queue_number ASC
    ''', (guild_id, status)) as cursor:
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "guild_id": row[1],
                "queue_number": row[2],
                "image_url": row[3],
                "user_ids": json.loads(row[4]) if row[4] else [],
                "description": row[5],
                "map_type": row[6],
                "route_type": row[7] if row[7] else "loot_route",
                "message_id": row[8],
                "backup_message_id": row[9],
                "created_at": row[10],
                "updated_at": row[11],
                "status": row[12],
            }
            for row in rows
        ]

async def get_undispatched_surge_requests(guild_id: int) -> List[Dict[str, Any]]:
    """Active surge_route entries not yet dispatched to the Management surge-maps channel."""
    db = await get_db()
    async with db.execute('''
        SELECT queue_number, image_url, user_ids, description, map_type
        FROM map_requests
        WHERE guild_id = ? AND status = 'active' AND route_type = 'surge_route'
          AND (dispatched_at IS NULL OR dispatched_at = '')
        ORDER BY queue_number ASC
    ''', (guild_id,)) as cursor:
        rows = await cursor.fetchall()
        return [
            {"queue_number": r[0], "image_url": r[1],
             "user_ids": json.loads(r[2]) if r[2] else [], "description": r[3],
             "map_type": r[4]}
            for r in rows
        ]


async def mark_surge_dispatched(guild_id: int, queue_number: str) -> None:
    """Stamp dispatched_at so the reconciliation sweep never double-posts this surge entry."""
    db = await get_db()
    await db.execute(
        "UPDATE map_requests SET dispatched_at = ? WHERE guild_id = ? AND queue_number = ?",
        (datetime.now(timezone.utc).isoformat(), guild_id, queue_number),
    )
    await db.commit()


async def get_undispatched_loot_requests(guild_id: int, hours_lookback: int = 1) -> List[Dict[str, Any]]:
    """Active loot_route entries not yet dispatched to the Management maps-not-taken channel.

    Args:
        hours_lookback: Only consider requests created within this many hours (default 1h).
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours_lookback)).isoformat()
    db = await get_db()
    async with db.execute('''
        SELECT queue_number, image_url, user_ids, description, map_type
        FROM map_requests
        WHERE guild_id = ? AND status = 'active' AND route_type = 'loot_route'
          AND (dispatched_at IS NULL OR dispatched_at = '')
          AND created_at > ?
        ORDER BY queue_number ASC
    ''', (guild_id, cutoff)) as cursor:
        rows = await cursor.fetchall()
        return [
            {"queue_number": r[0], "image_url": r[1],
             "user_ids": json.loads(r[2]) if r[2] else [], "description": r[3],
             "map_type": r[4]}
            for r in rows
        ]


async def mark_loot_dispatched(guild_id: int, queue_number: str) -> None:
    """Stamp dispatched_at so the loot bridge sweep never double-posts this loot entry."""
    db = await get_db()
    await db.execute(
        "UPDATE map_requests SET dispatched_at = ? WHERE guild_id = ? AND queue_number = ?",
        (datetime.now(timezone.utc).isoformat(), guild_id, queue_number),
    )
    await db.commit()


async def remove_map_request(guild_id: int, queue_number: str) -> bool:
    """Remove a map request from the queue by alphabetical code.

    Soft delete: marks the row status='removed' instead of deleting it, so an
    accidental or duplicate removal — e.g. a mis-fired cross-bot `-z removequeue`
    from the Management bot — is recoverable. The queue display
    (get_all_map_requests / get_sorted_map_requests) and surge dispatch
    (get_undispatched_surge_requests) both filter status='active', so removed
    entries vanish from the queue exactly as a hard delete did. Code generation
    (get_next_queue_number) counts ALL rows, so a removed code is never reused.
    Idempotent: re-removing an already-removed row just refreshes updated_at.
    """
    db = await get_db()
    cursor = await db.execute('''
        UPDATE map_requests
        SET status = 'removed', updated_at = ?
        WHERE guild_id = ? AND queue_number = ?
    ''', (datetime.now(timezone.utc).isoformat(), guild_id, queue_number))
    await db.commit()
    removed = cursor.rowcount > 0
    if removed:
        await _wave_log_event(
            category="map_queue",
            action="map_request_removed",
            guild=guild_id,
            details={"queue_code": queue_number},
        )
    return removed

async def update_map_request_status(guild_id: int, queue_number: str, status: str) -> bool:
    """Update the status of a map request by alphabetical code."""
    db = await get_db()
    cursor = await db.execute('''
        UPDATE map_requests
        SET status = ?, updated_at = ?
        WHERE guild_id = ? AND queue_number = ?
    ''', (status, datetime.now(timezone.utc).isoformat(), guild_id, queue_number))
    await db.commit()
    changed = cursor.rowcount > 0
    if changed:
        await _wave_log_event(
            category="map_queue",
            action="map_request_status_changed",
            guild=guild_id,
            details={"queue_code": queue_number, "new_status": status},
        )
    return changed

async def update_map_request(guild_id: int, queue_number: str, user_ids: List[int] = None,
                           description: str = None, image_url: str = None,
                           map_type: str = None) -> bool:
    """Update a map request's fields without deleting it (uses alphabetical codes)."""
    db = await get_db()
    
    updates = []
    params = []
    
    if user_ids is not None:
        user_ids_json = json.dumps(user_ids)
        updates.append("user_ids = ?")
        params.append(user_ids_json)
    
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    
    if image_url is not None:
        updates.append("image_url = ?")
        params.append(image_url)
    
    if map_type is not None:
        updates.append("map_type = ?")
        params.append(map_type)
    
    # Always update the updated_at timestamp
    updates.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    
    if not updates:
        return False  # Nothing to update
    
    # Add WHERE clause parameters
    params.extend([guild_id, queue_number])
    
    query = f'''
        UPDATE map_requests
        SET {', '.join(updates)}
        WHERE guild_id = ? AND queue_number = ?
    '''

    cursor = await db.execute(query, params)
    await db.commit()
    return cursor.rowcount > 0

async def get_queue_display(guild_id: int) -> str:
    """Generate a formatted queue display string."""
    requests = await get_all_map_requests(guild_id)
    
    if not requests:
        return "The map request queue is currently empty."
    
    config = await get_server_queue_config(guild_id)
    server_mode = config["server_mode"] if config else "drop_map"
    
    lines = []
    lines.append(f"**Map Request Queue** | Mode: {server_mode.replace('_', ' ').title()}")
    lines.append("─" * 40)
    
    for req in requests:
        user_count = len(req["user_ids"])
        user_text = f"{user_count} user{'s' if user_count != 1 else ''}"
        
        # Truncate description if too long
        desc = req["description"] or "No description"
        if len(desc) > 50:
            desc = desc[:47] + "..."
        
        lines.append(f"**#{req['queue_number']}** - {desc} ({user_text})")
    
    return "\n".join(lines)

# Allowed channels management functions
async def add_allowed_channel(guild_id: int, channel_id: int, added_by: int) -> bool:
    """Add a channel to the allowed list for a guild."""
    db = await get_db()
    try:
        cursor = await db.execute('''
            INSERT OR IGNORE INTO allowed_channels (guild_id, channel_id, added_by, added_at)
            VALUES (?, ?, ?, ?)
        ''', (guild_id, channel_id, added_by, datetime.now(timezone.utc).isoformat()))
        await db.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"Error adding allowed channel: {e}")
        return False

async def remove_allowed_channel(guild_id: int, channel_id: int) -> bool:
    """Remove a channel from the allowed list for a guild."""
    db = await get_db()
    cursor = await db.execute('''
        DELETE FROM allowed_channels
        WHERE guild_id = ? AND channel_id = ?
    ''', (guild_id, channel_id))
    await db.commit()
    return cursor.rowcount > 0

async def get_allowed_channels(guild_id: int) -> List[Dict[str, Any]]:
    """Get all allowed channels for a guild."""
    db = await get_db()
    async with db.execute('''
        SELECT channel_id, added_by, added_at
        FROM allowed_channels
        WHERE guild_id = ?
        ORDER BY added_at DESC
    ''', (guild_id,)) as cursor:
        rows = await cursor.fetchall()
        return [
            {
                "channel_id": row[0],
                "added_by": row[1],
                "added_at": row[2]
            }
            for row in rows
        ]

async def is_channel_allowed(guild_id: int, channel_id: int) -> bool:
    """Check if a channel is allowed for the bot to respond in."""
    db = await get_db()
    async with db.execute('''
        SELECT 1 FROM allowed_channels
        WHERE guild_id = ? AND channel_id = ?
        LIMIT 1
    ''', (guild_id, channel_id)) as cursor:
        row = await cursor.fetchone()
        return row is not None

async def clear_allowed_channels(guild_id: int) -> bool:
    """Clear all allowed channels for a guild."""
    db = await get_db()
    await db.execute('''
        DELETE FROM allowed_channels
        WHERE guild_id = ?
    ''', (guild_id,))
    await db.commit()
    # DELETE-all is idempotent: clearing zero rows is still a successful clear.
    return True


# DM Configuration Functions
async def get_dm_config(guild_id: int) -> Optional[Dict[str, Any]]:
    """Get DM configuration for a guild.

    Columns are selected by NAME: migrated DBs have `enabled` before
    `dm_template_surge_route` while a fresh CREATE TABLE puts it after, so
    positional `SELECT *` indexing reads the wrong column on one of them.

    NOTE: the dm_template_* columns are currently UNUSED by the senders —
    Tasks/dm_processor.py and Commands/dm_commands.py use their own hardcoded
    templates. Editing these DB values has no effect on outgoing DMs.
    """
    db = await get_db()
    async with db.execute('''
        SELECT guild_id, dm_channel_id, dm_log_channel_id,
               dm_template_drop_map, dm_template_loot_route,
               dm_template_surge_route, enabled
        FROM dm_config
        WHERE guild_id = ?
    ''', (guild_id,)) as cursor:
        row = await cursor.fetchone()
        if row:
            return {
                "guild_id": row[0],
                "dm_channel_id": row[1],
                "dm_log_channel_id": row[2],
                "dm_template_drop_map": row[3],
                "dm_template_loot_route": row[4],
                "dm_template_surge_route": row[5],
                "enabled": row[6] if row[6] is not None else 1  # Default to enabled
            }
        return None


async def set_dm_config(
    guild_id: int,
    dm_channel_id: Optional[int] = None,
    dm_log_channel_id: Optional[int] = None,
    dm_template_drop_map: Optional[str] = None,
    dm_template_loot_route: Optional[str] = None,
    enabled: Optional[int] = None
) -> bool:
    """Set DM configuration for a guild."""
    db = await get_db()
    
    # Check if config exists
    config = await get_dm_config(guild_id)
    
    if config:
        # Update existing config
        updates = []
        params = []
        
        if dm_channel_id is not None:
            updates.append("dm_channel_id = ?")
            params.append(dm_channel_id)
        
        if dm_log_channel_id is not None:
            updates.append("dm_log_channel_id = ?")
            params.append(dm_log_channel_id)
        
        if dm_template_drop_map is not None:
            updates.append("dm_template_drop_map = ?")
            params.append(dm_template_drop_map)
        
        if dm_template_loot_route is not None:
            updates.append("dm_template_loot_route = ?")
            params.append(dm_template_loot_route)
        
        if enabled is not None:
            updates.append("enabled = ?")
            params.append(enabled)
        
        if not updates:
            return True  # Nothing to update
        
        params.append(guild_id)
        query = f'''
            UPDATE dm_config
            SET {', '.join(updates)}
            WHERE guild_id = ?
        '''
        await db.execute(query, params)
    else:
        # Insert new config
        # Use enabled=1 as default if not specified
        enabled_value = enabled if enabled is not None else 1
        await db.execute('''
            INSERT INTO dm_config (
                guild_id, dm_channel_id, dm_log_channel_id,
                dm_template_drop_map, dm_template_loot_route, enabled
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            guild_id,
            dm_channel_id,
            dm_log_channel_id,
            dm_template_drop_map,
            dm_template_loot_route,
            enabled_value
        ))
    
    await db.commit()
    return True


async def delete_dm_config(guild_id: int) -> bool:
    """Delete DM configuration for a guild."""
    db = await get_db()
    cursor = await db.execute('DELETE FROM dm_config WHERE guild_id = ?', (guild_id,))
    await db.commit()
    return cursor.rowcount > 0


# Backward compatibility functions for database.py migration
async def set_warned(record_id: int) -> None:
    """Mark a tracked role as warned (for backward compatibility)."""
    db = await get_db()
    await db.execute('UPDATE tracked_roles SET warned = 1 WHERE id = ?', (record_id,))
    await db.commit()


async def reset_warned(record_id: int) -> None:
    """Reset warned flag for a tracked role (for backward compatibility)."""
    db = await get_db()
    await db.execute('UPDATE tracked_roles SET warned = 0 WHERE id = ?', (record_id,))
    await db.commit()


async def update_assigned_at(record_id: int, new_assigned_at: datetime) -> None:
    """Update assigned_at timestamp for a tracked role (for backward compatibility)."""
    db = await get_db()
    await db.execute(
        'UPDATE tracked_roles SET assigned_at = ? WHERE id = ?',
        (new_assigned_at.isoformat(), record_id)
    )
    await db.commit()
