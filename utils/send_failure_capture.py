"""
Send-Failure Capture — Wave Logistics Bot

Monkey-patches discord.py's send-style methods at startup so EVERY
HTTP failure (Forbidden, NotFound, rate-limit, etc.) automatically
lands in wave_logging under category="errors", showing up in the
dashboard's Errors tab without any per-cog code change.

Wrapped methods:
  - discord.abc.Messageable.send       (TextChannel/Thread/User/Member/DMChannel.send)
  - discord.Webhook.send               (webhook + interaction.followup.send)
  - discord.InteractionResponse.send_message   (slash-command first response)
  - discord.Client.fetch_channel       (cross-context "channel not found")

Each wrapper:
  1. calls the original method
  2. on discord.HTTPException, awaits log_event(...) with rich context
  3. re-raises so all existing try/except logic in callers still runs

Idempotent — calling install_send_failure_capture() twice does nothing
the second time.
"""

import logging
from typing import Any

import discord
from discord.abc import Messageable

from utils.global_logger import log_event

logger = logging.getLogger("discord")

_installed = False

# Capture originals at import time (BEFORE any patching)
_orig_messageable_send       = Messageable.send
_orig_webhook_send           = discord.Webhook.send
_orig_response_send_message  = discord.InteractionResponse.send_message
_orig_fetch_channel          = discord.Client.fetch_channel


def _content_preview(args: tuple, kwargs: dict) -> str:
    content = args[0] if args and isinstance(args[0], str) else kwargs.get("content")
    if not content:
        return ""
    return str(content)[:200]


def _describe_target(target: Any) -> tuple[dict, Any]:
    info: dict[str, Any] = {"target_type": type(target).__name__}
    tid = getattr(target, "id", None)
    if tid is not None:
        info["target_id"] = str(tid)
    tname = getattr(target, "name", None)
    if tname:
        info["target_name"] = tname
    guild = getattr(target, "guild", None)
    if guild is None and hasattr(target, "channel"):
        guild = getattr(target.channel, "guild", None)
    return info, guild


def _classify_action(error: Exception) -> str:
    if isinstance(error, discord.Forbidden):
        return "send_forbidden"
    if isinstance(error, discord.NotFound):
        return "send_not_found"
    if isinstance(error, discord.HTTPException):
        status = getattr(error, "status", 0) or 0
        if status == 429:
            return "send_rate_limited"
        if 500 <= status < 600:
            return "send_server_error"
        return "send_http_error"
    return "send_other_error"


async def _log_send_failure(
    target: Any,
    error: Exception,
    args: tuple,
    kwargs: dict,
    method: str,
) -> None:
    try:
        info, guild = _describe_target(target)
        details: dict[str, Any] = {
            **info,
            "method": method,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "content_preview": _content_preview(args, kwargs),
        }
        if isinstance(error, discord.HTTPException):
            details["status"] = getattr(error, "status", None)
            details["error_code"] = getattr(error, "code", None)
        await log_event(
            category="errors",
            action=_classify_action(error),
            guild=guild,
            details=details,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# Wrappers
# ──────────────────────────────────────────────────────────────────

async def _wrapped_messageable_send(self, *args, **kwargs):
    try:
        return await _orig_messageable_send(self, *args, **kwargs)
    except discord.HTTPException as e:
        await _log_send_failure(self, e, args, kwargs, method="Messageable.send")
        raise


async def _wrapped_webhook_send(self, *args, **kwargs):
    try:
        return await _orig_webhook_send(self, *args, **kwargs)
    except discord.HTTPException as e:
        await _log_send_failure(self, e, args, kwargs, method="Webhook.send")
        raise


async def _wrapped_response_send_message(self, *args, **kwargs):
    try:
        return await _orig_response_send_message(self, *args, **kwargs)
    except discord.HTTPException as e:
        await _log_send_failure(self, e, args, kwargs, method="InteractionResponse.send_message")
        raise


async def _wrapped_fetch_channel(self, channel_id):
    try:
        return await _orig_fetch_channel(self, channel_id)
    except discord.HTTPException as e:
        try:
            action = ("channel_not_found" if isinstance(e, discord.NotFound)
                      else "channel_forbidden" if isinstance(e, discord.Forbidden)
                      else "channel_fetch_error")
            await log_event(
                category="errors",
                action=action,
                details={
                    "channel_id":    str(channel_id),
                    "method":        "Client.fetch_channel",
                    "error_type":    type(e).__name__,
                    "error_message": str(e),
                    "status":        getattr(e, "status", None),
                    "error_code":    getattr(e, "code", None),
                },
            )
        except Exception:
            pass
        raise


# ──────────────────────────────────────────────────────────────────
# Install
# ──────────────────────────────────────────────────────────────────

def install_send_failure_capture() -> None:
    """Patch discord.py send methods. Idempotent — no-op on re-call."""
    global _installed
    if _installed:
        return
    Messageable.send                       = _wrapped_messageable_send
    discord.Webhook.send                   = _wrapped_webhook_send
    discord.InteractionResponse.send_message = _wrapped_response_send_message
    discord.Client.fetch_channel           = _wrapped_fetch_channel
    _installed = True
    logger.info(
        "[send_failure_capture] Installed wrappers on "
        "Messageable.send / Webhook.send / InteractionResponse.send_message / Client.fetch_channel"
    )
