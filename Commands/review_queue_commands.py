"""
HITL Review Queue — Admin Clear Commands
========================================
Tools to inspect and clear stale HITL (human-in-the-loop) proof reviews from the
review queue. Use these when a review card is bugged, abandoned, or no longer
needed and should be removed without counting as a completed review.

  -z reviewqueue                — list pending (unresolved) reviews in this guild
  -z clearreview <message_id>   — clear ONE stale review card by its message id
  -z clearreviewqueue confirm   — clear ALL pending reviews in this guild

Clearing marks the review resolved, deletes its card message, and refreshes the
queue sticky. It deliberately does NOT emit a `review_completed` event, so a
cleared stale review is never counted toward staff "Reviews Completed" stats on
the Management website. An audit-only `review_cleared` event is logged instead.

Requires Administrator or a Management role.
"""

import logging

import discord
from discord.ext import commands

from Database.database_improved import (
    get_pending_hitl,
    get_hitl_claim,
    resolve_hitl,
)

logger = logging.getLogger('discord')

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


def _age_str(message_id: int) -> str:
    """Human-readable age of a review derived from its Discord snowflake."""
    import time
    created_ms = (message_id >> 22) + 1420070400000
    age_secs = int(time.time() - created_ms / 1000)
    if age_secs < 60:
        return f"{age_secs}s ago"
    if age_secs < 3600:
        return f"{age_secs // 60}m ago"
    if age_secs < 86400:
        return f"{age_secs // 3600}h ago"
    return f"{age_secs // 86400}d ago"


class ReviewQueueCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── internals ────────────────────────────────────────────────────────────

    async def _clear_one(self, review: dict) -> bool:
        """Resolve + delete the card for one pending review. Returns True if the
        card message was deleted (best-effort; DB is always resolved)."""
        message_id = review['message_id']
        channel_id = review['channel_id']

        # 1) Mark resolved so it leaves the pending queue and any background loop
        #    stops touching it.
        await resolve_hitl(message_id)

        # 2) Delete the review card message (best-effort — it may already be gone).
        deleted = False
        channel = self.bot.get_channel(channel_id)
        if channel:
            try:
                msg = await channel.fetch_message(message_id)
                await msg.delete()
                deleted = True
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        # 3) Audit-only log. NOT 'review_completed' — a cleared stale review must
        #    never count toward staff review stats.
        try:
            from utils.global_logger import log_event
            guild = self.bot.get_guild(review['guild_id'])
            await log_event(
                bot=guild.me if guild else None,
                category="hitl_review",
                action="review_cleared",
                guild=guild,
                details={
                    "message_id": str(message_id),
                    "original_user_id": str(review.get('original_user_id')),
                },
            )
        except Exception as e:
            logger.warning(f"[ReviewQueue] failed to log review_cleared: {e}")

        return deleted

    async def _refresh_sticky(self, guild_id: int, channel_id: int):
        """Refresh the queue sticky via the proof-automation cog if available."""
        cog = self.bot.get_cog('ProofAutomationTask')
        if cog and hasattr(cog, '_update_sticky'):
            try:
                await cog._update_sticky(guild_id, channel_id)
            except Exception as e:
                logger.warning(f"[ReviewQueue] failed to refresh sticky: {e}")

    # ── commands ─────────────────────────────────────────────────────────────

    @commands.command(name='reviewqueue', aliases=['reviewpending', 'pendingreviews'])
    @is_authorized()
    async def review_queue(self, ctx):
        """List pending (unresolved) HITL reviews in this guild."""
        pending = await get_pending_hitl(ctx.guild.id)
        if not pending:
            await ctx.send(embed=discord.Embed(
                title="✅ Review Queue Empty",
                description="There are no pending reviews in this server.",
                color=discord.Color.green(),
            ))
            return

        lines = []
        for r in pending:
            link = (
                f"https://discord.com/channels/"
                f"{r['guild_id']}/{r['channel_id']}/{r['message_id']}"
            )
            claimed = f" • claimed by <@{r['claimed_by']}>" if r['claimed_by'] else ""
            lines.append(
                f"`{r['message_id']}` • <@{r['original_user_id']}> • "
                f"{_age_str(r['message_id'])}{claimed} • [jump]({link})"
            )

        embed = discord.Embed(
            title=f"📋 Pending Reviews — {len(pending)} waiting",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        embed.set_footer(
            text="Clear one: -z clearreview <message_id>  •  Clear all: -z clearreviewqueue confirm"
        )
        await ctx.send(embed=embed)

    @commands.command(name='clearreview', aliases=['reviewclear'])
    @is_authorized()
    async def clear_review(self, ctx, message_id: int = None):
        """Clear ONE stale review card by its message id."""
        if message_id is None:
            await ctx.send(embed=discord.Embed(
                title="❌ Missing Message ID",
                description="Usage: `-z clearreview <message_id>`\n"
                            "Find ids with `-z reviewqueue`.",
                color=discord.Color.red(),
            ))
            return

        # Only clear reviews that belong to this guild and are still pending.
        pending = await get_pending_hitl(ctx.guild.id)
        review = next((r for r in pending if r['message_id'] == message_id), None)
        if review is None:
            claim = await get_hitl_claim(message_id)
            if claim and claim['resolved']:
                desc = "That review is already resolved — nothing to clear."
            elif claim:
                desc = "That review belongs to a different server."
            else:
                desc = "No review with that message id exists."
            await ctx.send(embed=discord.Embed(
                title="❌ Review Not Cleared", description=desc, color=discord.Color.red(),
            ))
            return

        deleted = await self._clear_one(review)
        await self._refresh_sticky(ctx.guild.id, review['channel_id'])

        logger.info(
            f"[ReviewQueue] {ctx.author} ({ctx.author.id}) cleared stale review "
            f"{message_id} in guild {ctx.guild.id}"
        )
        await ctx.send(embed=discord.Embed(
            title="🧹 Review Cleared",
            description=(
                f"Cleared review `{message_id}`"
                + ("." if deleted else " (card message was already gone).")
                + "\nIt was **not** counted as a completed review."
            ),
            color=discord.Color.green(),
        ))

    @commands.command(name='clearreviewqueue', aliases=['reviewqueueclear'])
    @is_authorized()
    async def clear_review_queue(self, ctx, confirm: str = None):
        """Clear ALL pending reviews in this guild (requires the word 'confirm')."""
        pending = await get_pending_hitl(ctx.guild.id)
        if not pending:
            await ctx.send(embed=discord.Embed(
                title="✅ Review Queue Empty",
                description="There are no pending reviews to clear.",
                color=discord.Color.green(),
            ))
            return

        if confirm != 'confirm':
            await ctx.send(embed=discord.Embed(
                title="⚠️ Confirm Queue Clear",
                description=(
                    f"This will clear **{len(pending)}** pending review(s) in this "
                    f"server and delete their cards.\n\n"
                    f"Run `-z clearreviewqueue confirm` to proceed."
                ),
                color=discord.Color.orange(),
            ))
            return

        deleted = 0
        channel_ids = set()
        for review in pending:
            if await self._clear_one(review):
                deleted += 1
            channel_ids.add(review['channel_id'])

        for ch_id in channel_ids:
            await self._refresh_sticky(ctx.guild.id, ch_id)

        logger.info(
            f"[ReviewQueue] {ctx.author} ({ctx.author.id}) cleared the whole review "
            f"queue ({len(pending)} reviews) in guild {ctx.guild.id}"
        )
        await ctx.send(embed=discord.Embed(
            title="🧹 Review Queue Cleared",
            description=(
                f"Cleared **{len(pending)}** review(s) "
                f"({deleted} card message(s) deleted).\n"
                f"None were counted as completed reviews."
            ),
            color=discord.Color.green(),
        ))


async def setup(bot):
    await bot.add_cog(ReviewQueueCommands(bot))
    logger.info("✅ ReviewQueueCommands cog loaded")
