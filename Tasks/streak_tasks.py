import discord
from discord.ext import commands
from datetime import datetime, timezone
import json
import os
import logging
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import Database.database_improved as database

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


def save_config(config):
    # Atomic write: a crash mid-dump must not truncate server_config.json
    # (every cog reads it; a corrupt file would break their listeners).
    tmp_path = CONFIG_PATH + ".tmp"
    with open(tmp_path, 'w') as f:
        json.dump(config, f, indent=2)
    os.replace(tmp_path, CONFIG_PATH)


# ── Per-server-type perk strings ─────────────────────────────────────────────

CONTRIBUTOR_PERKS = {
    'drop_maps': (
        "🔥 **3x streak** — 2 free premium drop maps\n"
        "💎 **6x streak** — 1 month free\n"
        "👑 **12x streak** — 1 pro drop map free"
    ),
    'loot_routes': (
        "🔥 **3x streak** — 2 free premium loot routes\n"
        "💎 **6x streak** — 1 month free\n"
        "👑 **12x streak** — 1 pro loot route free"
    ),
}

PRIORITY_PERKS = {
    'drop_maps': (
        "🔥 **3x streak** — 50% off your 4th month\n"
        "💎 **6x streak** — 1 premium drop map free\n"
        "👑 **12x streak** — 1 month free"
    ),
    'loot_routes': (
        "🔥 **3x streak** — 50% off your 4th month\n"
        "💎 **6x streak** — 1 premium loot route free\n"
        "👑 **12x streak** — 1 month free"
    ),
}


def get_server_type(guild_id: int) -> str:
    config = load_config()
    return config.get(str(guild_id), {}).get('server_type', 'drop_maps')


def build_streak_info_embed(server_type: str = 'drop_maps') -> discord.Embed:
    is_loot = server_type == 'loot_routes'
    server_label = "Wave Loot Routes" if is_loot else "Wave Free Drop Maps"

    embed = discord.Embed(
        title="🏅 How Streaks Work",
        description=(
            "A **streak** tracks how many months in a row you\u2019ve been a paying supporter.\n"
            "Each time your role renews for another 30 days, your streak goes **+1**.\n\n"
            "The longer your streak, the better the rewards.\n"
            "➡️ Use **/streak** to see your count · **/status** to check when your role expires."
        ),
        color=discord.Color.blurple()
    )

    if is_loot:
        role_text = "🤝 **Contributor** — You support the server each month and get access to premium content."
    else:
        role_text = (
            "🤝 **Contributor** — You support the server each month and get access to premium content.\n"
            "⚡ **Priority** — You\u2019re a paying priority queue member with faster drop access."
        )
    embed.add_field(name="🎭 What Are the Roles?", value=role_text, inline=False)

    embed.add_field(
        name="🎖️ Streak Milestones",
        value=(
            "✨ **1x** — First-Time Supporter\n"
            "⭐ **2x** — Returning Supporter\n"
            "🔥 **3x** — Dedicated Supporter\n"
            "💎 **6x** — Diamond Supporter\n"
            "👑 **12x** — Legendary Supporter"
        ),
        inline=False
    )

    embed.add_field(
        name="🤝 Contributor Perks",
        value=CONTRIBUTOR_PERKS.get(server_type, CONTRIBUTOR_PERKS['drop_maps']),
        inline=True
    )

    if not is_loot:
        embed.add_field(
            name="⚡ Priority Perks",
            value=PRIORITY_PERKS.get(server_type, PRIORITY_PERKS['drop_maps']),
            inline=True
        )

    embed.add_field(
        name="\u200b",
        value=(
            "📌 Perks are tracked automatically.\n"
            "When you hit a milestone, **DM a staff member** to claim your reward."
        ),
        inline=False
    )

    embed.set_footer(text=f"{server_label} — use /streak to check your progress")
    return embed


async def _send_or_edit(channel, embed, message_id, label):
    """
    Helper: tries to edit an existing message by ID.
    If not found or no ID given, sends a new one.
    Returns the message ID that is now live (existing or new), or None on failure.
    """
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embed=embed)
            logger.info(f"[Streak Task] Edited existing {label} message in {channel.guild.name}")
            return message_id
        except discord.NotFound:
            logger.info(f"[Streak Task] {label} message was deleted in {channel.guild.name} — sending a new one")
        except discord.Forbidden:
            logger.warning(f"[Streak Task] No permission to edit {label} message in {channel.guild.name}")
            return None

    try:
        message = await channel.send(embed=embed)
        logger.info(f"[Streak Task] Posted new {label} message in {channel.guild.name} (ID: {message.id})")
        return message.id
    except discord.Forbidden:
        logger.warning(f"[Streak Task] No permission to send {label} message in {channel.guild.name}")
        return None


async def post_streak_info(guild, config):
    """
    On startup: posts or edits both the streak info overview and the leaderboard
    in the configured channel. Each message has its own saved ID in config.
    - Info message  → streak_info_message_id
    - Leaderboard   → streak_leaderboard_message_id
    """
    guild_id = str(guild.id)
    guild_config = config.get(guild_id, {})
    channel_id = guild_config.get('streak_log_channel_id')

    if not channel_id:
        logger.info(f"[Streak Task] No streak channel configured for {guild.name} — skipping")
        return

    channel = guild.get_channel(channel_id)
    if not channel:
        logger.warning(f"[Streak Task] Streak channel {channel_id} not found in {guild.name} — skipping")
        return

    changed = False

    # ── 1. Streak info overview ───────────────────────────────────────────────
    server_type = guild_config.get('server_type', 'drop_maps')
    info_id = await _send_or_edit(
        channel,
        build_streak_info_embed(server_type),
        guild_config.get('streak_info_message_id'),
        "streak info"
    )
    if info_id and info_id != guild_config.get('streak_info_message_id'):
        guild_config['streak_info_message_id'] = info_id
        changed = True

    # ── 2. Live leaderboard ───────────────────────────────────────────────────
    leaderboard_id = await _send_or_edit(
        channel,
        await build_streak_embed(guild),
        guild_config.get('streak_leaderboard_message_id'),
        "streak leaderboard"
    )
    if leaderboard_id and leaderboard_id != guild_config.get('streak_leaderboard_message_id'):
        guild_config['streak_leaderboard_message_id'] = leaderboard_id
        changed = True

    if changed:
        config[guild_id] = guild_config
        save_config(config)


def _badge(count: int) -> str:
    if count >= 12:
        return "👑"
    if count >= 6:
        return "💎"
    if count >= 3:
        return "🔥"
    if count >= 2:
        return "⭐"
    return "✨"


async def build_streak_embed(guild) -> discord.Embed:
    """Build a full streak leaderboard embed for all tracked members in a guild."""
    all_roles = await database.get_all_tracked_roles()
    guild_roles = [r for r in all_roles if r['guild_id'] == guild.id]
    server_type = get_server_type(guild.id)

    embed = discord.Embed(
        title="📊 Streak Leaderboard",
        description=f"Support streak overview for **{guild.name}**",
        color=discord.Color.gold()
    )

    # Collect streak counts per user, per role type (deduplicated)
    seen = set()
    user_data: dict[int, dict] = {}
    for r in guild_roles:
        key = (r['user_id'], r['role_type'])
        if key in seen:
            continue
        seen.add(key)
        streaks = await database.get_streak(guild.id, r['user_id'], r['role_type'])
        count = len(streaks)
        if count == 0:
            continue
        uid = r['user_id']
        if uid not in user_data:
            member = guild.get_member(uid)
            user_data[uid] = {
                'name': member.display_name if member else f"User {uid}",
                'priority': 0,
                'contributor': 0,
            }
        user_data[uid][r['role_type']] = count

    if not user_data:
        embed.description = "No streak data yet — be the first supporter!"
        return embed

    RANK_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

    sorted_users = sorted(
        user_data.values(),
        key=lambda u: max(u['priority'], u['contributor']),
        reverse=True
    )

    lines = []
    for rank, u in enumerate(sorted_users, start=1):
        p, c = u['priority'], u['contributor']
        best = max(p, c)
        badge = _badge(best)
        rank_str = RANK_MEDALS.get(rank, f"`{rank}.`")

        role_parts = []
        if p:
            role_parts.append(f"⚡ **{p}x**")
        if c:
            role_parts.append(f"🤝 **{c}x**")

        lines.append(f"{rank_str} **{u['name']}** {badge} — {' · '.join(role_parts)}")

    # Top 3 separated from the rest by a blank line
    top = lines[:3]
    rest = lines[3:]
    board = "\n".join(top)
    if rest:
        board += "\n\n" + "\n".join(rest)

    embed.add_field(name="​", value=board, inline=False)
    embed.set_footer(text=f"Last updated: {datetime.now(timezone.utc).strftime('%d/%m/%Y at %H:%M')} UTC")
    return embed


class StreakTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def refresh_all_guilds(self):
        """Refresh streak info for all guilds. Called on startup."""
        config = load_config()
        for guild in self.bot.guilds:
            await post_streak_info(guild, config)


async def setup(bot):
    await bot.add_cog(StreakTask(bot))
    logger.info("✅ StreakTask cog loaded")