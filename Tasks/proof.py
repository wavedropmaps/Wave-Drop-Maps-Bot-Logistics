"""
Proof Archival — Background Listener
=====================================
Listens for messages in each guild's configured proof channel. When a member
with administrator permissions OR a role named "Staff" (case-insensitive)
REPLIES to a message in that channel, the bot downloads every image attachment
from the ORIGINAL (replied-to) message to:

    proof_assets/<guild_id>/<YYYY-MM-DD>/<author_user_id>/<msg_id>_<idx>_<filename>

The folder is named after the original poster (the user whose proof is being
saved), not the staff member who triggered the save.

Duplicate processing of the same message is skipped (tracked in the
`proof_saved_messages` table).

Safety: only files that pass an IMAGE whitelist (extension + Discord
content-type + size cap) are even downloaded, and after download each file's
magic bytes are verified to be a real image — anything that isn't is deleted
immediately. Non-image attachments (.exe, .zip, documents, ...) are never
saved, so no executable/malware can land in proof_assets/.

Config is read from Database/roles.db (table `proof_config`), populated
by Commands/proof_commands.py.
"""

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone

import aiosqlite
import discord
from discord.ext import commands

logger = logging.getLogger('discord')

LOCAL_DB = "Database/roles.db"
PROOF_ROOT = "proof_assets"
TRIGGER_ROLE_NAME = "staff"  # case-insensitive; admins always qualify too

# Cap filename length to keep paths safe on Windows
MAX_FILENAME_LEN = 120
_FILENAME_SAFE_RE = re.compile(r'[^A-Za-z0-9._\- ]+')

# ── Safety whitelist ────────────────────────────────────────────────────────
# Only real images get downloaded. This is what keeps executables / malware /
# documents out of proof_assets/.
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB — Discord's normal upload cap

# Magic-byte signatures: (offset, expected bytes). After download we read the
# file header and reject anything whose bytes don't actually match an image,
# so a file renamed "screenshot.png" that's really an .exe is caught + deleted.
_IMAGE_SIGNATURES = (
    (0, b'\xff\xd8\xff'),            # JPEG
    (0, b'\x89PNG\r\n\x1a\n'),       # PNG
    (0, b'GIF87a'),                  # GIF
    (0, b'GIF89a'),                  # GIF
    (0, b'BM'),                      # BMP
    (0, b'II*\x00'),                 # TIFF (little-endian)
    (0, b'MM\x00*'),                 # TIFF (big-endian)
)


def _is_safe_image(att: discord.Attachment) -> bool:
    """Cheap pre-download gate: extension + Discord content-type + size."""
    ext = os.path.splitext(att.filename)[1].lower()
    if ext not in IMAGE_EXTENSIONS:
        return False
    if att.content_type and not att.content_type.lower().startswith("image/"):
        return False
    if att.size and att.size > MAX_ATTACHMENT_BYTES:
        return False
    return True


def _verify_image_bytes(path: str) -> bool:
    """Post-download gate: the file's real header must match a known image
    signature (WEBP handled separately via the RIFF....WEBP container)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return True
    return any(head[off:off + len(sig)] == sig for off, sig in _IMAGE_SIGNATURES)


async def _ensure_schema(db):
    """Idempotent schema creation — mirrored in Commands/proof_commands.py."""
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


async def _get_proof_channel_id(guild_id: int) -> int | None:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT channel_id, enabled FROM proof_config WHERE guild_id=?",
            (guild_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    channel_id, enabled = row
    if not enabled:
        return None
    return channel_id


async def _already_saved(guild_id: int, message_id: int) -> bool:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT 1 FROM proof_saved_messages WHERE guild_id=? AND message_id=?",
            (guild_id, message_id)
        ) as cursor:
            return await cursor.fetchone() is not None


async def _record_save(guild_id: int, message_id: int, file_count: int):
    now = time.time()
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            """INSERT OR IGNORE INTO proof_saved_messages
               (guild_id, message_id, saved_at, file_count)
               VALUES (?, ?, ?, ?)""",
            (guild_id, message_id, now, file_count)
        )
        await db.execute(
            """UPDATE proof_config
               SET total_saved = total_saved + ?, last_saved_at = ?
               WHERE guild_id = ?""",
            (file_count, now, guild_id)
        )
        await db.commit()


def _sanitize_filename(name: str) -> str:
    """Strip path separators and other unsafe characters; cap length."""
    name = os.path.basename(name) or "file"
    name = _FILENAME_SAFE_RE.sub('_', name)
    if len(name) > MAX_FILENAME_LEN:
        stem, dot, ext = name.rpartition('.')
        if dot and len(ext) <= 10:
            keep = MAX_FILENAME_LEN - len(ext) - 1
            name = stem[:keep] + '.' + ext
        else:
            name = name[:MAX_FILENAME_LEN]
    return name


class ProofTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def _is_authorized(self, member: discord.Member) -> bool:
        """True if member has admin perms or a role named 'Staff' (case-insensitive)."""
        if member.guild_permissions.administrator:
            return True
        return any(r.name.lower() == TRIGGER_ROLE_NAME for r in member.roles)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Must be in a guild and be a reply
        if not message.guild:
            return
        if not message.reference or not message.reference.message_id:
            return

        # Must be from a bot OR an authorized member (admin or Staff role)
        if not message.author.bot:
            if not isinstance(message.author, discord.Member):
                return
            if not self._is_authorized(message.author):
                return

        # Channel must be the configured proof channel for this guild
        configured_channel_id = await _get_proof_channel_id(message.guild.id)
        if configured_channel_id is None:
            return
        if message.channel.id != configured_channel_id:
            return

        # Fetch the original (replied-to) message
        try:
            original = await message.channel.fetch_message(message.reference.message_id)
        except (discord.NotFound, discord.HTTPException):
            logger.warning(
                f"[proof] could not fetch original message {message.reference.message_id} "
                f"in guild {message.guild.id}"
            )
            return

        # Only real images survive the whitelist
        images = [a for a in original.attachments if _is_safe_image(a)]
        if not images:
            if original.attachments:
                logger.info(
                    f"[proof] original message {original.id} in guild {message.guild.id} "
                    f"had {len(original.attachments)} attachment(s), none passed "
                    f"the image whitelist — skipping"
                )
            return

        # Skip if we've already archived this message
        if await _already_saved(message.guild.id, original.id):
            logger.info(
                f"[proof] skipping duplicate save for message {original.id} "
                f"in guild {message.guild.id}"
            )
            return

        # Build destination directory named after the original poster
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        author_id = original.author.id
        dest_dir = os.path.join(
            PROOF_ROOT,
            str(message.guild.id),
            date_str,
            str(author_id),
        )
        os.makedirs(dest_dir, exist_ok=True)

        # Download every whitelisted image in parallel
        async def _save_one(idx: int, att: discord.Attachment) -> tuple[str, bool, str]:
            safe = _sanitize_filename(att.filename)
            target = os.path.join(dest_dir, f"{original.id}_{idx}_{safe}")
            try:
                await att.save(target, use_cached=False)
            except (discord.HTTPException, discord.NotFound, OSError) as e:
                return target, False, str(e)
            if not _verify_image_bytes(target):
                try:
                    os.remove(target)
                except OSError:
                    pass
                return target, False, "failed image signature check (deleted)"
            return target, True, ""

        results = await asyncio.gather(
            *(_save_one(i, a) for i, a in enumerate(images)),
            return_exceptions=False
        )

        saved = [t for t, ok, _ in results if ok]
        failed = [(t, err) for t, ok, err in results if not ok]

        if saved:
            await _record_save(message.guild.id, original.id, len(saved))
            logger.info(
                f"[proof] saved {len(saved)} image(s) from message "
                f"{original.id} (author={author_id}) triggered by "
                f"{message.author.id} in guild {message.guild.id} -> {dest_dir}"
            )
        if failed:
            for path, err in failed:
                logger.warning(
                    f"[proof] failed to save {path} from message {original.id} "
                    f"in guild {message.guild.id}: {err}"
                )


async def setup(bot):
    os.makedirs(PROOF_ROOT, exist_ok=True)
    await bot.add_cog(ProofTask(bot))
    logger.info("✅ ProofTask cog loaded")
