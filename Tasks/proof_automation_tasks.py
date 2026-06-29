"""
Proof Automation — YOLO classifier + duplicate/stolen detection
===============================================================
Watches configured channels across multiple guilds.
For every image posted (all images in a message are checked):
  1. Stolen-proof detection (cross-guild, strongest signal first):
       a. SHA-256 exact file match     — byte-identical re-upload
       b. Discord attachment reuse     — same CDN attachment id
       c. pHash near-duplicate         — survives re-encode/resize; the
          horizontally-MIRRORED image is also checked (flip evasion)
       d. OCR username match           — one account farming proof
  2. YOLO classification     — replies with the appropriate message
  3. Stores submission       — pHash + sha256 + attachment_id + filename in DB

The fingerprint collector ALWAYS runs — even when per-guild automation is
toggled off (-z prooftoggle) or when a thief is warned and processing stops —
so every image posted in a watch channel stays matchable later.

Every stolen/copied flag is also recorded per user (stolen_flags table) and the
review embed shows how many times that user was flagged before. Count only —
no extra action is ever taken from the count.

Stolen matches are reported to the staff review channel with the match type,
original submitter/server (cross-server flagged), a time-window suspicion
rating, and any EXIF metadata.

No commands — all config is hardcoded below.
"""

import asyncio
import io
import logging
import os
import re
import tempfile
import time
import uuid
import shutil
from datetime import datetime, timezone

import aiosqlite

from utils.automation_tree import AutomationTree, Decision
from utils.model_nodes import (
    Model1Gatekeeper, Model2TwitterRouter, Model3aMobileCheck1,
    Model3bMobileCheck2, Model4DesktopCheck, Model5UIRouter,
    Model6PhonePhoto, Model7Screenshot
)
from utils.automation_handlers import DynamicHandlers
from Database.database_improved import (
    log_stolen_detection,
    register_hitl_review, claim_hitl, release_hitl_claim, resolve_hitl,
    get_pending_hitl, get_hitl_sticky, set_hitl_sticky,
)

import discord
from discord.ext import commands, tasks

logger = logging.getLogger('discord')

LOCAL_DB               = "Database/roles.db"

# Perceptual-hash size + match threshold. We use a 256-bit pHash (hash_size=16).
# Validated on 51 real proofs (tests/eval_harness.py): re-encode/resize copies
# land at 0–2, the closest genuinely-different user at 46 → a 20 cutoff catches
# copies with a huge (~26-bit) safety margin and zero false positives on the set.
# (Crops are NOT caught — they exceed the different-user distance; accepted gap.)
# 2026-06-25: tightened 20 → 8 per real-data review of proof_assets/. NOTE: this
# narrows coverage — distance 8–14 scam variants (same template, fake domain name
# letters swapped e.g. hasobin→wasobin→hesobia) will NO LONGER be caught.
PHASH_HASH_SIZE        = 16
PHASH_DUPE_THRESHOLD   = 8

# ── Stolen-proof detection switches ──────────────────────────────────────────
# Master switch. When False: no detection at all (submissions still stored).
STOLEN_CHECKS_ENABLED = True

# EXACT signals (SHA-256 + attachment id): a literal re-upload, zero false
# positives.
EXACT_STOLEN_ENABLED = True

# EXACT matches (SHA-256/attachment) are zero-false-positive, so they ENFORCE:
# warn the user (STOLEN_MSG) + stop processing. Perceptual (pHash) is separate
# (see below) and is ALWAYS log-only regardless of this flag.
STOLEN_WARN_USER = True

# PERCEPTUAL signal (256-bit pHash): catches re-encoded/resized copies. Validated
# safe on real data, but to be cautious it is STAFF-HEADS-UP ONLY — it posts a
# review for staff and NEVER warns the user or blocks them. Catches re-encode/
# resize theft; does NOT catch crops.
PERCEPTUAL_STOLEN_ENABLED = True

# OCR username signal: DISABLED. Useless on the creator-code screen (the code is
# the same for everyone, no per-user @handle) and EasyOCR is the big RAM hog.
# Off ⇒ EasyOCR never loads. (Worth re-enabling only for Server 1 Twitter proofs.)
OCR_USERNAME_ENABLED = False

# Also compare the horizontally-MIRRORED incoming image against stored hashes —
# closes the "flip the stolen image" evasion. Costs one extra pHash (~ms) per
# image at query time only; nothing extra is stored. Log-only like all
# perceptual signals.
PHASH_CHECK_MIRROR = True

# Render a single labeled side-by-side image (OLD original left, NEW submission
# right) inside the review embed, so staff compare at a glance instead of
# scrolling two stacked attachments. Full-size files stay attached either way.
COMPARISON_IMAGE_ENABLED = True

# Filenames too generic to mean anything when two uploads share them (Discord
# paste defaults etc.). A filename match is only ever shown as a SUPPORTING
# line inside an embed that something else already triggered — never a signal
# on its own, and never for these names.
GENERIC_FILENAMES = {
    "image.png", "image.jpg", "image.jpeg", "image.webp",
    "image0.png", "image1.png", "image2.png", "image3.png",
    "image0.jpg", "image1.jpg", "image2.jpg", "image3.jpg",
    "unknown.png", "unknown.jpg", "untitled.png", "untitled.jpg",
    "screenshot.png", "screenshot.jpg", "img.png", "img.jpg",
}


# Reply sent to a user when their submission is flagged as possible stolen proof.
STOLEN_MSG = (
    "🚨 This proof has already been submitted by someone else. Submitting "
    "**stolen or copied proof is not allowed and can get you banned.** "
    "Please only submit your own original proof."
)

# ── Per-server config ─────────────────────────────────────────────────────────
GUILD_CONFIG = {

    # ── Server 1 ──────────────────────────────────────────────────────────────
    988564962802810961: {
        "name": "Server 1 (Wave Drop Maps)",
        "watch_channel_id": 1210798761329295440,
        "twitter_enabled": True,
        "invite_support_role_name": "Support",
        "hitl_review_channel_id": 1516685472720621608,
        "creator_code_role_ids": (1305277560086593546, 1055713830988157039),
        "following_only_msg": (
            "Please show **__proof__** of __**liking**__ the** pinned tweet **"
            "https://x.com/Wavedropmaps/status/1896931137722982898 and following "
            "https://x.com/Wavedropmaps, send proof in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "press_search_msg": (
            "You need to **press __search__** on the code "
            "`WAVEDROPMAPS` in the **support a creator panel** so it shows as "
            "applied, then send **proof** in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "zoom_out_msg": (
            "Please show **proof** of using **code** `WAVEDROPMAPS` in the "
            "**support a creator panel** while the image is **__ZOOMED OUT__**  "
            "and send **proof** in "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "wrong_code_msg": (
            "It looks like you've entered the wrong creator code. Please make "
            "sure you are using the code **`WAVEDROPMAPS`**, then post your proof here: "
            "https://discord.com/channels/988564962802810961/1210798761329295440"
        ),
        "level_1_messages": [
            (
                "✅ Access granted for Named Locations!\n"
                "- Check out <#1210768729395437568> **for access to the landmark  + Reload + OG Drop Maps.**"
            ),
            (
                "Named Locations ✅\n"
                "**<#1210768729395437568> → landmark + Reload + OG Drop Maps.**"
            ),
            (
                "🔓 Named Locations unlocked — **check out <#1210768729395437568> for landmark  + Reload + OG Drop Maps.**"
            ),
        ],
        "level_2_messages": [
            (
                "🔓 Full access unlocked\n"
                "**- Request custom drop maps for ANY spot with first-priority, "
                "skipping the queue → <#1364454494665969664> | <#1210810131634454638> **"
            ),
            (
                "Full access ✅\n"
                "**- Custom drop maps 👀 skip the drop map queue + first priority\n"
                "- <#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "You're in 🔥\n"
                "- **Get your spot mapped first, skip the wait → "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "🔓 Full access unlocked — **request custom drop maps for any spot "
                "with first-priority, skipping the drop map queue in "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
            (
                "🔓 Full access\n"
                "- **Custom drop maps first-priority → "
                "⁠<#1364454494665969664> | <#1210810131634454638>**"
            ),
        ],
    },

    # ── Server 2 ──────────────────────────────────────────────────────────────
    971731167621574666: {
        "name": "Server 2 (Loot Routes)",
        "watch_channel_id": 1188088624345002035,
        "twitter_enabled": False,
        "invite_support_role_name": "Support",
        "hitl_review_channel_id": 1516673791844155392,
        "creator_code_role_ids": (1105873031450075227,),
        "following_only_msg": None,
        "press_search_msg": (
            "You need to **press __search__** on the code "
            "`WAVEDROPMAPS` in the **support a creator pane**l so it shows as "
            "applied, then send **proof** in "
            "https://discord.com/channels/971731167621574666/1188088624345002035"
        ),
        "zoom_out_msg": (
            "Please show **proof** of using **code** `WAVEDROPMAPS` in the "
            "**support a creator pane**l while the image is **__ZOOMED OUT__**  "
            "and send **proof** in "
            "https://discord.com/channels/971731167621574666/1188088624345002035"
        ),
        "wrong_code_msg": (
            "It looks like you've entered the wrong creator code. Please make "
            "sure you are using the code **`WAVEDROPMAPS`**, then post your proof here: "
            "https://discord.com/channels/971731167621574666/1188088624345002035"
        ),
        "level_2_messages": [
            (
                "🔓 Full access unlocked\n"
                "- **Get Pro Loot Routes + skip the loot route queue → "
                "<#1364463385709903893> | ⁠<#1131551886563082314>**"
            ),
            (
                "Full access ✅\n"
                "- **Pro Loot Routes 👀 skip the queue**\n"
                "- ⁠<#1364463385709903893> | ⁠<#1131551886563082314>"
            ),
            (
                "You're in 🔥\n"
                "**Pro Loot Routes + skip the queue → "
                "⁠<#1364463385709903893> | ⁠<#1131551886563082314>**"
            ),
            (
                "🔓 Full access\n"
                "**Pro Loot Routes + skip queue → "
                "<#1364463385709903893> | <#1131551886563082314>**"
            ),
            (
                "🔓 Full access unlocked —** grab Pro Loot Routes & skip the loot "
                "route queue in <#1364463385709903893> | <#1131551886563082314>**"
            ),
        ],
    },
}

# ── DB helpers ────────────────────────────────────────────────────────────────
class HITLClaimView(discord.ui.View):
    """Shown on a new HITL review — only a Claim button until someone takes it."""

    def __init__(self, message_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        btn = discord.ui.Button(
            label="Claim",
            style=discord.ButtonStyle.primary,
            emoji="🙋",
            custom_id=f"hitl_claim_{message_id}",
            row=0,
        )
        btn.callback = self._on_claim
        self.add_item(btn)

    async def _on_claim(self, interaction: discord.Interaction):
        import json
        from Database.database_improved import get_db

        success = await claim_hitl(self.message_id, interaction.user.id)
        if not success:
            return await interaction.response.send_message(
                "⚠️ This review was just claimed by someone else.", ephemeral=True
            )

        db = await get_db()
        async with db.execute(
            '''
            SELECT start_node, valid_classes_json, hitl_filenames_json,
                   original_user_id, original_channel_id, original_message_id, guild_id
            FROM hitl_claim_state WHERE message_id = ?
            ''',
            (self.message_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await release_hitl_claim(self.message_id)
            return await interaction.response.send_message(
                "⚠️ Review data not found. Claim released.", ephemeral=True
            )

        start_node       = row[0]
        valid_classes    = json.loads(row[1]) if row[1] else ['Garbage']
        hitl_filenames   = json.loads(row[2]) if row[2] else []
        original_user_id = row[3]
        orig_channel_id  = row[4]
        orig_message_id  = row[5]
        guild_id         = row[6]

        # Fetch the original proof message so callbacks can assign roles / reply
        original_message = None
        try:
            proof_channel = interaction.client.get_channel(orig_channel_id)
            if proof_channel:
                original_message = await proof_channel.fetch_message(orig_message_id)
        except Exception:
            pass

        if original_message is None:
            # The original message was likely deleted (e.g. by auto-purge)
            # Create a mock message so we can still review and grant/reject
            guild = interaction.client.get_guild(guild_id)
            if not guild:
                await release_hitl_claim(self.message_id)
                return await interaction.response.send_message("⚠️ Guild not found. Claim released.", ephemeral=True)
            member = guild.get_member(original_user_id)
            if not member:
                await release_hitl_claim(self.message_id)
                return await interaction.response.send_message("⚠️ Original user not found (may have left server). Claim released.", ephemeral=True)
            
            class MockMessage:
                def __init__(self, author, guild, channel, message_id):
                    self.author = author
                    self.guild = guild
                    self.channel = channel
                    self.id = message_id
                
                async def reply(self, *args, **kwargs):
                    raise AttributeError("Mock message cannot be replied to.")
            
            original_message = MockMessage(member, guild, proof_channel, orig_message_id)

        cfg = GUILD_CONFIG.get(guild_id, {})
        cog = interaction.client.get_cog('ProofAutomationTask')

        action_view = HITLActionView(
            cog=cog,
            message_id=self.message_id,
            hitl_filenames=hitl_filenames,
            start_node=start_node,
            valid_classes=valid_classes,
            original_message=original_message,
            cfg=cfg,
        )

        await interaction.response.edit_message(
            content=f"🔒 **Claimed by {interaction.user.mention}**",
            view=action_view,
        )

        if cog:
            await cog._update_sticky(guild_id, interaction.channel_id)


def _hitl_style_and_emoji(cls_name: str) -> tuple:
    cls_lower = cls_name.lower()
    if cls_lower in ['creator code', 'following and liking', 'online fort website', 'iphone shop', 'using code correctly', 'correctly using code']:
        return discord.ButtonStyle.success, '🔓'
    if cls_lower in ['garbage', 'invite', 'scam', 'wrong code', 'following only', 'liking only', 'press search', 'need to press search', 'zoom out']:
        return discord.ButtonStyle.secondary, '📝'
    return discord.ButtonStyle.primary, '🔄'


class _HITLCardProxy:
    """Thin wrapper around a confirm-button interaction that redirects
    edit_original_response() to the HITL card message (not the ephemeral)."""

    def __init__(self, confirm_interaction: discord.Interaction, hitl_card: discord.Message):
        self._inner = confirm_interaction
        self.message = hitl_card
        self.user = confirm_interaction.user

    async def edit_original_response(self, **kwargs):
        await self.message.edit(**kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class HITLConfirmView(discord.ui.View):
    """Ephemeral one-shot confirmation shown before any HITL decision executes."""

    def __init__(self, prompt: str, on_confirm):
        super().__init__(timeout=30)
        self._on_confirm = on_confirm

        confirm_btn = discord.ui.Button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
        confirm_btn.callback = self._confirm
        self.add_item(confirm_btn)

        cancel_btn = discord.ui.Button(label="Go Back", style=discord.ButtonStyle.secondary, emoji="↩️")
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

        self._prompt = prompt

    async def _confirm(self, interaction: discord.Interaction):
        self.stop()
        await self._on_confirm(interaction)

    async def _cancel(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(content="↩️ **Cancelled** — no action taken.", view=None)


class HITLActionView(discord.ui.View):
    """Shown after a staff member claims the review — all decision buttons."""

    def __init__(self, cog, message_id: int, hitl_filenames, start_node: str,
                 valid_classes: list, original_message: discord.Message, cfg: dict):
        super().__init__(timeout=None)
        self.cog             = cog
        self.message_id      = message_id
        self.hitl_filenames  = hitl_filenames if isinstance(hitl_filenames, list) else [hitl_filenames]
        self.start_node      = start_node
        self.original_message = original_message
        self.cfg             = cfg

        if self.start_node == "Video_Review_Node":
            if self.original_message.guild and self.original_message.guild.id == 988564962802810961:
                btn_l1 = discord.ui.Button(label="Level 1 (Twitter)", style=discord.ButtonStyle.success, emoji="✅", row=0, custom_id=f"hitl_vid_l1_{message_id}")
                btn_l1.callback = self.global_override_callback("GRANT_LEVEL_1", "Granted Level 1")
                self.add_item(btn_l1)

                btn_l2 = discord.ui.Button(label="Level 2 (Code)", style=discord.ButtonStyle.success, emoji="✅", row=0, custom_id=f"hitl_vid_l2_{message_id}")
                btn_l2.callback = self.global_override_callback("GRANT_LEVEL_2", "Granted Level 2")
                self.add_item(btn_l2)
            else:
                btn_l2 = discord.ui.Button(label="Grant Full Access (Level 2)", style=discord.ButtonStyle.success, emoji="✅", row=0, custom_id=f"hitl_vid_l2_{message_id}")
                btn_l2.callback = self.global_override_callback("GRANT_LEVEL_2", "Granted Level 2")
                self.add_item(btn_l2)

            btn_wc = discord.ui.Button(label="Wrong Code", style=discord.ButtonStyle.danger, emoji="❌", row=1, custom_id=f"hitl_vid_wc_{message_id}")
            btn_wc.callback = self.global_override_callback("REJECT_WRONG_CODE", "Wrong Code")
            self.add_item(btn_wc)

            btn_zo = discord.ui.Button(label="Zoom Out", style=discord.ButtonStyle.danger, emoji="❌", row=1, custom_id=f"hitl_vid_zo_{message_id}")
            btn_zo.callback = self.global_override_callback("REJECT_ZOOM_OUT", "Zoom Out")
            self.add_item(btn_zo)

            btn_ps = discord.ui.Button(label="Press Search", style=discord.ButtonStyle.danger, emoji="❌", row=1, custom_id=f"hitl_vid_ps_{message_id}")
            btn_ps.callback = self.global_override_callback("REJECT_PRESS_SEARCH", "Press Search")
            self.add_item(btn_ps)

            btn_discard = discord.ui.Button(label="Spam / Discard", style=discord.ButtonStyle.danger, emoji="🗑️", row=2, custom_id=f"hitl_vid_discard_{message_id}")
            btn_discard.callback = self.discard_callback
            self.add_item(btn_discard)
            return

        # Row 0: AI predicted classes
        for i, cls_name in enumerate(valid_classes):
            style, emoji = _hitl_style_and_emoji(cls_name)
            btn = discord.ui.Button(
                label=cls_name, style=style, emoji=emoji, row=0,
                custom_id=f"hitl_act_{message_id}_{i}",
            )
            btn.callback = self.make_callback(cls_name)
            self.add_item(btn)

        # Row 1: Global Overrides
        garbage_btn = discord.ui.Button(
            label="Garbage", style=discord.ButtonStyle.danger, emoji="🗑️", row=1,
            custom_id=f"hitl_garbage_{message_id}",
        )
        garbage_btn.callback = self.global_override_callback("REJECT_DYNAMIC", "Garbage")
        self.add_item(garbage_btn)

        if self.start_node in ["Model6_PhonePhoto", "Model7_Screenshot"]:
            wrong_code_btn = discord.ui.Button(
                label="Wrong Code", style=discord.ButtonStyle.danger, emoji="⌨️", row=1,
                custom_id=f"hitl_wrongcode_{message_id}",
            )
            wrong_code_btn.callback = self.global_override_callback("REJECT_WRONG_CODE", "Wrong Code")
            self.add_item(wrong_code_btn)

        discard_btn = discord.ui.Button(
            label="Discard", style=discord.ButtonStyle.danger, emoji="🛑", row=1,
            custom_id=f"hitl_discard_{message_id}",
        )
        discard_btn.callback = self.discard_callback
        self.add_item(discard_btn)

        # Row 2: Help buttons (only visible after claiming)
        help_general_btn = discord.ui.Button(
            label="Workflow Guide", style=discord.ButtonStyle.secondary, emoji="🗺️", row=2,
            custom_id=f"hitl_guide_{message_id}",
        )
        help_general_btn.callback = self.help_general_callback
        self.add_item(help_general_btn)

        help_context_btn = discord.ui.Button(
            label="What do I press?", style=discord.ButtonStyle.primary, emoji="💡", row=2,
            custom_id=f"hitl_context_{message_id}",
        )
        help_context_btn.callback = self.help_context_callback
        self.add_item(help_context_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        from Database.database_improved import get_hitl_claim
        claim = await get_hitl_claim(self.message_id)

        if claim and claim.get("resolved"):
            try:
                await interaction.response.edit_message(
                    content="✅ **Review already completed.**", view=None
                )
            except Exception:
                await interaction.response.send_message("⚠️ This review has already been completed.", ephemeral=True)
            return False

        if claim and claim.get("claimed_by_id"):
            if interaction.user.id != claim["claimed_by_id"]:
                await interaction.response.send_message("❌ This review is claimed by another staff member.", ephemeral=True)
                return False
        return True

    async def help_general_callback(self, interaction: discord.Interaction):
        help_text = (
            "# 🛡️ Staff Review Guide\n\n"
            "When you click a button below an image, you are overriding the AI and forcing the system to take that action. Here is what each button color means:\n\n"
            "### 🔓 Green Buttons (Instant Approval)\n"
            "*Grants the user full access roles and sends them the success drop map messages.*\n"
            "- **Examples**: `Creator Code`, `Following and liking`, `Using code correctly`, `Online fort website`\n\n"
            "### 📝 Grey Buttons (Instant Rejection)\n"
            "*Rejects the user and sends them a specific DM telling them exactly what they did wrong.*\n"
            "- **Examples**:\n"
            "  - `Garbage` / `Scam` → *\"You provided an invalid image...\"*\n"
            "  - `Zoom out` → *\"Please show proof while the image is ZOOMED OUT...\"*\n"
            "  - `Following only` → *\"Please show proof of liking the pinned tweet...\"*\n\n"
            "### 🔄 Blue Buttons (Send to Next AI)\n"
            "*Does not send a message. This forwards the image to the next specialized AI model to continue automatic checking.*\n"
            "- **Examples**: `Twitter`, `Mobile`, `Taken via phone`, `ScreenShot`\n\n"
            "### 🛑 Red Discard Button\n"
            "*Silently deletes the submission. The user will NOT be notified and will NOT get access. Use this for duplicate spam.*"
        )
        await interaction.response.send_message(help_text, ephemeral=True)

    async def help_context_callback(self, interaction: discord.Interaction):
        if self.start_node == "Model6_PhonePhoto":
            help_text = (
                "# 💡 Context Guide: Phone Photo Check\n\n"
                "**Situation:** The AI recognized this as a Phone Photo of the Fortnite shop, but got confused about the Creator Code.\n\n"
                "- **`using code correctly`** 🔓: Click if our code is actively supporting.\n"
                "- **`Press search`** 📝: Click if the code is typed in, but they haven't pressed search to apply it.\n"
                "- **`zoom out`** 📝: Click if the image is cropped and you can't see the whole screen.\n"
                "- **`Wrong Code`** 🗑️ (Global Override): Click if they typed a random/wrong code.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't even the Fortnite Item Shop."
            )
        elif self.start_node == "Model7_Screenshot":
            help_text = (
                "# 💡 Context Guide: Screenshot Check\n\n"
                "**Situation:** The AI recognized this as a direct Screenshot of the Fortnite shop, but got confused about the Creator Code.\n\n"
                "- **`Correctly using code`** 🔓: Click if our code is actively supporting.\n"
                "- **`Need to press search`** 📝: Click if the code is typed in, but they haven't pressed search to apply it.\n"
                "- **`Zoom out`** 📝: Click if the image is cropped and you can't see the whole screen.\n"
                "- **`Wrong Code`** 🗑️ (Global Override): Click if they typed a random/wrong code.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't even the Fortnite Item Shop."
            )
        elif self.start_node == "Model5_UIRouter":
            help_text = (
                "# 💡 Context Guide: UI Router\n\n"
                "**Situation:** The AI is trying to figure out what type of device/UI this Fortnite image is from.\n\n"
                "- **`Online fort website`** 🔓: Click if it's the Epic Games website.\n"
                "- **`Iphone Shop`** 🔓: Click if it's Fortnite Mobile.\n"
                "- **`Taken via phone`** 🔄: Click if they took a picture of their monitor with their phone.\n"
                "- **`ScreenShot`** 🔄: Click if it's a direct console/PC screenshot.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't even the Fortnite Item Shop."
            )
        elif self.start_node == "Model1_Gatekeeper":
            help_text = (
                "# 💡 Context Guide: Gatekeeper Check\n\n"
                "**Situation:** The AI is doing its very first check to see what kind of image was uploaded.\n\n"
                "- **`Creator Code`** 🔓: Click if they are correctly supporting us.\n"
                "- **`Garbage`** 📝: Click if it's a random irrelevant image.\n"
                "- **`Twitter`** 🔄: Click if it's a screenshot of Twitter (for wave drop maps).\n"
                "- **`invite`** 📝: Click if it's an invite link/screenshot."
            )
        elif self.start_node == "Model2_TwitterRouter":
            help_text = (
                "# 💡 Context Guide: Twitter Router\n\n"
                "**Situation:** The AI knows this is a Twitter screenshot, but is trying to figure out if it's from a Desktop or a Mobile device.\n\n"
                "- **`Desktop`** 🔄: Click if the screenshot is from a computer browser.\n"
                "- **`Mobile`** 🔄: Click if the screenshot is from a phone app.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't Twitter at all."
            )
        elif self.start_node == "Model3a_MobileCheck1":
            help_text = (
                "# 💡 Context Guide: Mobile Twitter Check 1\n\n"
                "**Situation:** The AI is analyzing a Mobile Twitter screenshot to see if they followed the rules.\n\n"
                "- **`Following only`** 📝: Click if you can tell they ONLY followed, but didn't like the pinned tweet.\n"
                "- **`either`** 🔄: Click if they are **liking**, or **liking + following**. (The next AI checks which.)\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't Twitter at all."
            )
        elif self.start_node == "Model3b_MobileCheck2":
            help_text = (
                "# 💡 Context Guide: Mobile Twitter Check 2\n\n"
                "**Situation:** The AI is performing a deep scan on a Mobile Twitter screenshot.\n\n"
                "- **`Following and liking`** 🔓: Click if you see clear proof they followed AND liked the tweet.\n"
                "- **`Following only`** 📝: Click if they followed, but forgot to like the pinned tweet.\n"
                "- **`Liking only`** 📝: Click if they liked the tweet, but forgot to follow the account.\n"
                "- **`scam`** 📝: Click if the proof is fake or invalid.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't Twitter at all."
            )
        elif self.start_node == "Model4_DesktopCheck":
            help_text = (
                "# 💡 Context Guide: Desktop Twitter Check\n\n"
                "**Situation:** The AI is analyzing a Desktop Twitter screenshot.\n\n"
                "- **`Following and liking`** 🔓: Click if you see clear proof they followed AND liked the tweet.\n"
                "- **`Following only`** 📝: Click if they followed, but forgot to like the pinned tweet.\n"
                "- **`Liking only`** 📝: Click if they liked the tweet, but forgot to follow the account.\n"
                "- **`scam`** 📝: Click if the proof is fake or invalid.\n"
                "- **`Garbage`** 🗑️ (Global Override): Click if this isn't Twitter at all."
            )
        else:
            help_text = (
                f"# 💡 Context Guide: {self.start_node}\n\n"
                "**Situation:** The AI got confused at this step. Please pick the class that best matches the image, or use the **Garbage** override if the image is entirely invalid."
            )
        await interaction.response.send_message(help_text, ephemeral=True)

    def make_callback(self, cls_name):
        async def callback(interaction: discord.Interaction):
            hitl_card = interaction.message
            staff_user = interaction.user

            async def on_confirm(confirm_interaction: discord.Interaction):
                # A proof with 2+ images can NEVER be judged by the model — each
                # model only ever looks at ONE image. So on a multi-image batch the
                # staff member walks the tree by hand: a routing class advances the
                # card to the next step's buttons (no inference, no image deleted),
                # and a terminal class resolves the proof. Single-image reviews keep
                # the old behavior of handing the corrected node back to the next AI.
                cog = self.cog or confirm_interaction.client.get_cog('ProofAutomationTask')
                cur_node = cog.tree.nodes.get(self.start_node) if cog else None
                if len(self.hitl_filenames) > 1 and cur_node is not None:
                    await self._manual_walk_step(
                        confirm_interaction, hitl_card, staff_user, cls_name, cog, cur_node
                    )
                    return

                await confirm_interaction.response.edit_message(
                    content=f"⚙️ Processing as `{cls_name}`...", view=None
                )
                await hitl_card.edit(
                    content=f"⚙️ **Processing as `{cls_name}`...** (Started by {staff_user.mention})",
                    view=None,
                )

                from utils.global_logger import log_event
                await log_event(
                    category="hitl_review",
                    action="review_step_completed",
                    actor=staff_user,
                    target=self.original_message.author,
                    details={"verdict": cls_name, "start_node": self.start_node},
                )

                proxy = _HITLCardProxy(confirm_interaction, hitl_card)
                try:
                    await self.cog.resume_processing(
                        self.hitl_filenames[0], self.start_node, cls_name, self.original_message, self.cfg,
                        interaction=proxy, hitl_message_id=self.message_id, hitl_filenames=self.hitl_filenames
                    )
                except Exception as e:
                    logger.error(f"[ProofAuto] resume_processing failed for HITL {self.message_id}: {e}")
                    from Database.database_improved import resolve_hitl
                    await resolve_hitl(self.message_id)
                    try:
                        await hitl_card.edit(
                            content=(
                                f"❌ **Review processing failed.**\n"
                                f"**Staff:** {staff_user.mention}\n"
                                f"**Error:** `{type(e).__name__}`\n"
                                "The review has been marked resolved. Please re-submit or use `-z clearreview`."
                            ),
                            view=None,
                        )
                    except Exception:
                        pass
                    return

                try:
                    for hitl_filename in self.hitl_filenames[1:]:
                        os.unlink(hitl_filename)
                except Exception:
                    pass

                if self.cog:
                    await self.cog._update_sticky(self.original_message.guild.id, confirm_interaction.channel_id)

            await interaction.response.send_message(
                content=f"⚠️ **Confirm:** Mark as `{cls_name}`?",
                view=HITLConfirmView(cls_name, on_confirm),
                ephemeral=True,
            )
        return callback

    async def _manual_walk_step(self, confirm_interaction, hitl_card, staff_user, cls_name, cog, cur_node):
        """Multi-image manual review. The model stays OUT of the decision (it can
        only see one image): node.route() is plain class->Decision logic with no
        inference. A routing class advances the card to the next node's buttons
        and keeps every image; a terminal class resolves the proof and cleans up."""
        from utils.global_logger import log_event

        decision = cur_node.route(cls_name, 1.0, self.cfg)
        decision.failed_node = self.start_node

        # ── Routing class → advance one step, run NOTHING, delete NOTHING ──
        if decision.action == 'ROUTE' and decision.next_node:
            next_node = decision.next_node
            # Acknowledge inside the 3s interaction window before any (lazy) model
            # weights load — loading labels is not inference, but it can be slow.
            await confirm_interaction.response.edit_message(
                content=f"⚙️ Advancing to `{next_node}`…", view=None
            )

            next_classes = await cog._classes_for_node(next_node)

            from Database.database_improved import update_hitl_node
            await update_hitl_node(self.message_id, next_node, next_classes)

            new_view = HITLActionView(
                cog=cog,
                message_id=self.message_id,
                hitl_filenames=self.hitl_filenames,
                start_node=next_node,
                valid_classes=next_classes,
                original_message=self.original_message,
                cfg=self.cfg,
            )
            cog.bot.add_view(new_view, message_id=self.message_id)

            edit_kwargs = {
                "content": (
                    f"🔒 **Claimed by {staff_user.mention}** · manual review (multi-image)\n"
                    f"➡️ Step: `{next_node}` — pick what BOTH images show together."
                ),
                "view": new_view,
            }
            if hitl_card.embeds:
                embed = hitl_card.embeds[0]
                try:
                    embed.set_field_at(1, name="Current Step", value=next_node)
                except IndexError:
                    pass
                edit_kwargs["embed"] = embed
            await hitl_card.edit(**edit_kwargs)

            await log_event(
                category="hitl_review",
                action="review_step_completed",
                actor=staff_user,
                target=self.original_message.author,
                details={"verdict": cls_name, "advanced_to": next_node, "manual_multi_image": True},
            )
            return

        # ── Terminal class → resolve the proof (parity with global overrides) ──
        from Database.database_improved import resolve_hitl
        await confirm_interaction.response.edit_message(
            content=f"⚙️ Applying `{cls_name}`...", view=None
        )
        await resolve_hitl(self.message_id)

        await log_event(
            category="hitl_review",
            action="review_completed",
            actor=staff_user,
            target=self.original_message.author,
            details={"verdict": cls_name, "start_node": self.start_node, "manual_multi_image": True},
        )

        action = decision.action
        outcome_text = action
        if action == 'GRANT_LEVEL_1':
            outcome_text = "Granted Level 1"
        elif action == 'GRANT_LEVEL_2':
            outcome_text = "Granted Level 2"
        elif action.startswith('REJECT_'):
            reason = action.replace('REJECT_', '').replace('_', ' ').title()
            outcome_text = f"Rejected ({reason})"

        await hitl_card.edit(
            content=(
                f"✅ **Review Completed**\n"
                f"**Staff:** {staff_user.mention}\n"
                f"**User:** {self.original_message.author.mention}\n"
                f"**Outcome:** `{outcome_text}`"
            ),
            view=None,
        )

        # No interaction/hitl args → _execute_decision just applies the action
        # (assign roles / reply); we already resolved + logged above, so it is
        # not double-counted.
        await cog._execute_decision(
            decision, self.original_message, self.cfg,
            {"tmp_path": self.hitl_filenames[0] if self.hitl_filenames else None},
        )

        for fn in self.hitl_filenames:
            try:
                os.unlink(fn)
            except Exception:
                pass

        if cog:
            await cog._update_sticky(self.original_message.guild.id, confirm_interaction.channel_id)

    def global_override_callback(self, action_type: str, btn_label: str):
        async def callback(interaction: discord.Interaction):
            hitl_card = interaction.message
            staff_user = interaction.user

            async def on_confirm(confirm_interaction: discord.Interaction):
                await confirm_interaction.response.edit_message(
                    content=f"⚙️ Applying `{btn_label}`...", view=None
                )
                await resolve_hitl(self.message_id)
                new_content = (
                    f"✅ **Review Completed**\n"
                    f"**Staff:** {staff_user.mention}\n"
                    f"**User:** {self.original_message.author.mention}\n"
                    f"**Outcome:** `{'Rejected (' + btn_label + ')' if action_type not in ['GRANT_LEVEL_1', 'GRANT_LEVEL_2'] else btn_label}`"
                )
                await hitl_card.edit(content=new_content, view=None)

                from utils.global_logger import log_event
                await log_event(
                    category="hitl_review",
                    action="review_completed",
                    actor=staff_user,
                    target=self.original_message.author,
                    details={"verdict": btn_label, "start_node": self.start_node},
                )

                from utils.automation_tree import Decision
                decision = Decision(action=action_type, confidence=1.0)
                await self.cog._execute_decision(
                    decision, self.original_message, self.cfg, {"tmp_path": self.hitl_filenames[0] if self.hitl_filenames else None}
                )
                try:
                    for hitl_filename in self.hitl_filenames:
                        os.unlink(hitl_filename)
                except Exception:
                    pass

                if self.cog:
                    await self.cog._update_sticky(self.original_message.guild.id, confirm_interaction.channel_id)

            await interaction.response.send_message(
                content=f"⚠️ **Confirm:** Apply `{btn_label}` to this proof?",
                view=HITLConfirmView(btn_label, on_confirm),
                ephemeral=True,
            )
        return callback

    async def discard_callback(self, interaction: discord.Interaction):
        hitl_card = interaction.message
        staff_user = interaction.user

        async def on_confirm(confirm_interaction: discord.Interaction):
            await confirm_interaction.response.edit_message(content="⚙️ Discarding...", view=None)
            await resolve_hitl(self.message_id)
            new_content = (
                f"✅ **Review Completed**\n"
                f"**Staff:** {staff_user.mention}\n"
                f"**User:** {self.original_message.author.mention}\n"
                f"**Outcome:** `Discarded`"
            )
            await hitl_card.edit(content=new_content, view=None)

            from utils.global_logger import log_event
            await log_event(
                category="hitl_review",
                action="review_completed",
                actor=staff_user,
                target=self.original_message.author,
                details={"verdict": "Discard", "start_node": self.start_node},
            )

            try:
                for hitl_filename in self.hitl_filenames:
                    os.unlink(hitl_filename)
            except Exception:
                pass

            if self.cog:
                await self.cog._update_sticky(self.original_message.guild.id, confirm_interaction.channel_id)

        await interaction.response.send_message(
            content="⚠️ **Confirm:** Silently discard this proof? The user will NOT be notified.",
            view=HITLConfirmView("Discard", on_confirm),
            ephemeral=True,
        )

async def _ensure_schema(db):
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_submissions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id         INTEGER NOT NULL,
            user_id          INTEGER NOT NULL,
            phash            TEXT    NOT NULL,
            twitter_username TEXT,
            message_id       INTEGER NOT NULL,
            submitted_at     REAL    NOT NULL,
            sha256           TEXT,
            attachment_id    INTEGER
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proof_automation_state (
            guild_id             INTEGER PRIMARY KEY,
            creator_code_index   INTEGER DEFAULT 0,
            enabled              INTEGER DEFAULT 1
        )
    """)
    # Per-user record of every stolen/copied flag. COUNT ONLY — shown in the
    # review embed so staff can spot repeat offenders; never drives any action.


    await db.execute("""
        CREATE TABLE IF NOT EXISTS stolen_flags (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id   INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            kind       TEXT    NOT NULL,   -- 'exact' (confirmed) | 'perceptual' (look-alike)
            match_type TEXT,
            message_id INTEGER,
            flagged_at REAL    NOT NULL
        )
    """)
    # Short-term buffer for compound proofs split across separate messages.
    # A user who proves only "following" (or only "liking") has the proven
    # component remembered here for PARTIAL_PROOF_TTL; when the complementary
    # half arrives within the window, the two combine into a grant. One row per
    # (guild, user, component) so re-sending the same half just refreshes the
    # timestamp instead of piling up. Stale rows are ignored at read time and
    # swept by the 24h cleanup loop — the table never holds more than a handful.
    await db.execute("""
        CREATE TABLE IF NOT EXISTS partial_proofs (
            guild_id     INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            component    TEXT    NOT NULL,   -- 'following' | 'liking'
            submitted_at REAL    NOT NULL,
            message_id   INTEGER,
            PRIMARY KEY (guild_id, user_id, component)
        )
    """)
    # Migrate: add columns if upgrading from an older schema
    for table, col, ddl in (
        ("proof_automation_state", "enabled",       "INTEGER DEFAULT 1"),
        ("proof_submissions",      "sha256",        "TEXT"),
        ("proof_submissions",      "attachment_id", "INTEGER"),
        ("proof_submissions",      "filename",      "TEXT"),
    ):
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        except Exception:
            pass  # Column already exists
    # Indexes for fast exact-match lookups
    await db.execute("CREATE INDEX IF NOT EXISTS idx_proof_sha256 ON proof_submissions(sha256)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_proof_attach ON proof_submissions(attachment_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_stolen_flags_user ON stolen_flags(user_id)")


# How long a single proven half (following / liking) is remembered while we wait
# for the complementary half to arrive in a later message. 12 hours.
PARTIAL_PROOF_TTL = 12 * 3600
# The set of components that together satisfy the Twitter "follow AND like" reward.
TWITTER_REQUIRED_COMPONENTS = frozenset({"following", "liking"})


async def _get_buffered_components(guild_id: int, user_id: int) -> set:
    """Return the set of components this user has already proven within the TTL
    window. Stale rows (older than PARTIAL_PROOF_TTL) are filtered out here, so
    correctness never depends on the cleanup loop having run."""
    cutoff = time.time() - PARTIAL_PROOF_TTL
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT component FROM partial_proofs WHERE guild_id=? AND user_id=? AND submitted_at > ?",
            (guild_id, user_id, cutoff),
        ) as cur:
            rows = await cur.fetchall()
    return {r[0] for r in rows}


async def _store_buffered_components(guild_id: int, user_id: int, components: set, message_id: int):
    """Remember each freshly-proven component for this user, refreshing the
    timestamp if it was already buffered (UPSERT on the composite PK)."""
    now = time.time()
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        for comp in components:
            await db.execute(
                """INSERT INTO partial_proofs (guild_id, user_id, component, submitted_at, message_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(guild_id, user_id, component)
                   DO UPDATE SET submitted_at=excluded.submitted_at, message_id=excluded.message_id""",
                (guild_id, user_id, comp, now, message_id),
            )
        await db.commit()


async def _clear_buffered_components(guild_id: int, user_id: int):
    """Drop all buffered components for this user — called once they're granted."""
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            "DELETE FROM partial_proofs WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        )
        await db.commit()


async def _cleanup_expired_partial_proofs():
    """Housekeeping: delete buffered halves older than the TTL. Lazy expiry at
    read time already guarantees correctness; this just keeps the table tiny."""
    cutoff = time.time() - PARTIAL_PROOF_TTL
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute("DELETE FROM partial_proofs WHERE submitted_at < ?", (cutoff,))
        await db.commit()


async def _is_enabled(guild_id: int) -> bool:
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        async with db.execute(
            "SELECT enabled FROM proof_automation_state WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return bool(row[0]) if row else True  # default enabled if no row yet


async def _get_next_success_message(guild_id: int, level: str) -> str:
    key = "level_1_messages" if level == "Level 1" else "level_2_messages"
    messages = GUILD_CONFIG[guild_id].get(key, ["Success!"])
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            "INSERT OR IGNORE INTO proof_automation_state (guild_id, creator_code_index) VALUES (?, 0)",
            (guild_id,)
        )
        await db.commit()
        async with db.execute(
            "SELECT creator_code_index FROM proof_automation_state WHERE guild_id=?",
            (guild_id,)
        ) as cur:
            row = await cur.fetchone()
        idx = row[0] if row else 0
        safe_idx = idx % len(messages)
        next_idx = (idx + 1) % 1000
        await db.execute(
            "UPDATE proof_automation_state SET creator_code_index=? WHERE guild_id=?",
            (next_idx, guild_id)
        )
        await db.commit()
    return messages[safe_idx]


async def _find_exact_stolen(guild_id: int, user_id: int, analysis: dict) -> dict | None:
    """Instant exact-match stolen checks (SHA-256 + Discord attachment reuse)."""
    sha    = analysis.get("sha256")
    att_id = analysis.get("attachment_id")
    if not sha and not att_id:
        return None

    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)

        # 1. Exact file (SHA-256)
        if sha:
            async with db.execute(
                """SELECT user_id, guild_id, submitted_at, message_id, filename FROM proof_submissions
                   WHERE sha256=? AND user_id!=? ORDER BY submitted_at LIMIT 1""",
                (sha, user_id)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return {
                    "match_type": "Exact file match (SHA-256)",
                    "original_user_id": row[0], "original_guild_id": row[1],
                    "original_submitted_at": row[2], "original_message_id": row[3],
                    "original_filename": row[4],
                }

        # 2. Discord attachment reuse
        if att_id:
            async with db.execute(
                """SELECT user_id, guild_id, submitted_at, message_id, filename FROM proof_submissions
                   WHERE attachment_id=? AND user_id!=? ORDER BY submitted_at LIMIT 1""",
                (att_id, user_id)
            ) as cur:
                row = await cur.fetchone()
            if row:
                return {
                    "match_type": "Discord attachment reused",
                    "original_user_id": row[0], "original_guild_id": row[1],
                    "original_submitted_at": row[2], "original_message_id": row[3],
                    "original_filename": row[4],
                }
    return None


async def _find_fuzzy_stolen(guild_id: int, user_id: int, analysis: dict) -> dict | None:
    """Fuzzy pHash stolen checks (cross-guild)."""
    phash_str = analysis.get("phash")
    if not phash_str:
        return None

    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        import imagehash
        incoming = [(imagehash.hex_to_hash(phash_str), False)]
        if PHASH_CHECK_MIRROR and analysis.get("phash_flip"):
            try:
                incoming.append((imagehash.hex_to_hash(analysis["phash_flip"]), True))
            except Exception:
                pass
        async with db.execute(
            """SELECT phash, user_id, guild_id, submitted_at, message_id, filename
               FROM proof_submissions WHERE user_id!=? AND phash!=''""",
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
        best_dist, best, best_flip = 999, None, False
        for ph, uid, gid, ts, mid, fname in rows:
            try:
                stored = imagehash.hex_to_hash(ph)
            except Exception:
                continue
            for inc, flipped in incoming:
                try:
                    d = inc - stored
                except Exception:
                    continue
                if d < best_dist:
                    best_dist, best, best_flip = d, (uid, gid, ts, mid, fname), flipped
        if best and best_dist <= PHASH_DUPE_THRESHOLD:
            return {
                "match_type": "pHash image match (mirrored)" if best_flip else "pHash image match",
                "original_user_id": best[0], "original_guild_id": best[1],
                "original_submitted_at": best[2], "original_message_id": best[3],
                "original_filename": best[4],
                "distance": best_dist,
                "mirror": best_flip
            }
    return None


async def _store_submission(guild_id: int, user_id: int, phash_str: str | None,
                            twitter_username: str | None, message_id: int,
                            sha256: str | None = None,
                            attachment_id: int | None = None,
                            filename: str | None = None):
    async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
        await _ensure_schema(db)
        await db.execute(
            """INSERT INTO proof_submissions
               (guild_id, user_id, phash, twitter_username, message_id,
                submitted_at, sha256, attachment_id, filename)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (guild_id, user_id, phash_str or "", twitter_username, message_id,
             time.time(), sha256, attachment_id, filename)
        )
        await db.commit()


# ── Inference helpers (run in thread pool) ───────────────────────────────────
def _compute_phash(path: str) -> str:
    import imagehash
    from PIL import Image
    return str(imagehash.phash(Image.open(path), hash_size=PHASH_HASH_SIZE))


def _compute_phash_flip(path: str) -> str:
    import imagehash
    from PIL import Image, ImageOps
    return str(imagehash.phash(ImageOps.mirror(Image.open(path).convert("RGB")),
                               hash_size=PHASH_HASH_SIZE))


def _compute_sha256(path: str) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _extract_exif(path: str) -> str | None:
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS
        exif = getattr(Image.open(path), "_getexif", lambda: None)()
        if not exif:
            return None
        found = {TAGS.get(t, t): str(v) for t, v in exif.items()
                 if TAGS.get(t, t) in ("DateTimeOriginal", "DateTime", "Make", "Model", "Software")}
        return ", ".join(f"{k}: {v}" for k, v in found.items()) or None
    except Exception:
        return None


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.webm'}

def _is_image(att: discord.Attachment) -> bool:
    if att.content_type:
        if att.content_type.startswith("image/"): return True
        if att.content_type.startswith("video/"): return False
    ext = os.path.splitext(att.filename)[1].lower()
    return ext in IMAGE_EXTENSIONS

def _is_video(att: discord.Attachment) -> bool:
    if att.content_type:
        if att.content_type.startswith("video/"): return True
        if att.content_type.startswith("image/"): return False
    ext = os.path.splitext(att.filename)[1].lower()
    return ext in VIDEO_EXTENSIONS


def _safe_unlink(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


# ── Cog ───────────────────────────────────────────────────────────────────────
class ProofAutomationTask(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if not os.path.exists("hitl_pending"):
            os.makedirs("hitl_pending")
        self.tree = self._build_tree()
        self.cleanup_hitl_pending.start()
        self._hitl_claim_timeout_check.start()

    async def cog_load(self):
        """Runs once when the cog is loaded (on bot startup). Re-registers persistent views."""
        from Database.database_improved import get_db
        db = await get_db()

        # Release any active claims that got interrupted by the bot restarting
        await db.execute("UPDATE hitl_claim_state SET claimed_by = NULL, claimed_at = NULL WHERE resolved = 0")
        await db.commit()

        # Re-register the Claim view for all pending reviews so the buttons work after a restart.
        # Also edit each Discord message so the UI matches (messages may still show HITLActionView
        # buttons from before the restart, which now have no handler).
        async with db.execute(
            "SELECT message_id, channel_id FROM hitl_claim_state WHERE resolved = 0"
        ) as cursor:
            rows = await cursor.fetchall()

        for message_id, channel_id in rows:
            claim_view = HITLClaimView(message_id)
            self.bot.add_view(claim_view, message_id=message_id)

        # Editing the Discord messages requires the bot cache to be ready. Defer it.
        asyncio.ensure_future(self._restore_hitl_message_views(rows))

    async def _restore_hitl_message_views(self, rows: list):
        """After startup, edit each pending HITL message so it shows HITLClaimView.
        Messages mid-claim at shutdown still visually show HITLActionView buttons
        (which now have no handler), so we overwrite them with the Claim button."""
        await self.bot.wait_until_ready()
        for message_id, channel_id in rows:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            try:
                msg = await channel.fetch_message(message_id)
                claim_view = HITLClaimView(message_id)
                await msg.edit(
                    content="📋 **Pending review** *(bot restarted — click Claim to continue)*",
                    view=claim_view,
                )
            except Exception:
                pass

    def _build_tree(self):
        tree = AutomationTree()
        tree.add_node(Model1Gatekeeper('Models/model 1.pt', 'Model1_Gatekeeper'))
        tree.add_node(Model2TwitterRouter('Models/Model 2Desktop or mobile.pt', 'Model2_TwitterRouter'))
        tree.add_node(Model3aMobileCheck1('Models/Model 3 following or either set cof .pt', 'Model3a_MobileCheck1'))
        tree.add_node(Model3bMobileCheck2('Models/model 3.safetensors', 'Model3b_MobileCheck2', ['Following and liking', 'Following only', 'Liking only', 'scam']))
        tree.add_node(Model4DesktopCheck('Models/Model 4 desktop twitter.pt', 'Model4_DesktopCheck'))
        tree.add_node(Model5UIRouter('Models/Model 5 gatekeeper of proofs.pt', 'Model5_UIRouter'))
        tree.add_node(Model6PhonePhoto('Models/phone photo code proof.pt', 'Model6_PhonePhoto'))
        tree.add_node(Model7Screenshot('Models/Screenshot code proof.pt', 'Model7_Screenshot'))
        return tree

    # ── Listener ──────────────────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        cfg = GUILD_CONFIG.get(message.guild.id)
        if cfg is None:
            return

        if message.channel.id == cfg.get("hitl_review_channel_id"):
            await self._update_sticky(message.guild.id, message.channel.id)
            return

        if message.channel.id != cfg.get("watch_channel_id"):
            return

        images = [a for a in message.attachments if _is_image(a)]
        videos = [a for a in message.attachments if _is_video(a)]

        if not images and not videos:
            return

        if not await _is_enabled(message.guild.id):
            if images:
                await self._collect_fingerprints(message, images)
            return

        if videos:
            await self._process_videos(message, videos, cfg)
            return

        if images:
            await self._process_images(message, images, cfg)

    async def _store_fingerprints_only(self, guild_id: int, user_id: int,
                                       message_id: int, analyses: list[dict]):
        loop = asyncio.get_running_loop()
        for a in analyses:
            if a["phash"] is None:
                try:
                    a["phash"] = await loop.run_in_executor(None, _compute_phash, a["tmp_path"])
                except Exception:
                    pass
            await _store_submission(
                guild_id, user_id, a["phash"], None, message_id,
                sha256=a.get("sha256"), attachment_id=a.get("attachment_id"),
                filename=a.get("filename")
            )

    async def _collect_fingerprints(self, message: discord.Message,
                                    attachments: list[discord.Attachment]):
        analyses = await asyncio.gather(*(self._download_and_fingerprint(a) for a in attachments))
        analyses = [a for a in analyses if a is not None]
        if not analyses:
            return
        try:
            await self._store_fingerprints_only(
                message.guild.id, message.author.id, message.id, analyses
            )
        finally:
            for a in analyses:
                _safe_unlink(a["tmp_path"])

    async def _download_and_fingerprint(self, att: discord.Attachment) -> dict | None:
        suffix = os.path.splitext(att.filename)[1].lower() or ".jpg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            await att.save(tmp_path, use_cached=False)
        except (discord.HTTPException, discord.NotFound, OSError):
            _safe_unlink(tmp_path)
            return None

        loop = asyncio.get_running_loop()
        sha256, exif, phash, phash_flip = await asyncio.gather(
            loop.run_in_executor(None, _compute_sha256, tmp_path),
            loop.run_in_executor(None, _extract_exif, tmp_path),
            loop.run_in_executor(None, _compute_phash, tmp_path),
            loop.run_in_executor(None, _compute_phash_flip, tmp_path),
            return_exceptions=True
        )
        if isinstance(sha256, Exception):
            logger.warning(f"[ProofAuto] sha256 failed: {sha256}")
            sha256 = None
        if isinstance(exif, Exception):
            exif = None
        if isinstance(phash, Exception):
            phash = None
        if isinstance(phash_flip, Exception):
            phash_flip = None

        return {
            "filename": att.filename,
            "tmp_path": tmp_path,
            "attachment_id": att.id,
            "sha256": sha256,
            "exif": exif,
            "phash": phash,
            "phash_flip": phash_flip,
            "probs": None,
            "username": None,
        }

    async def _update_sticky(self, guild_id: int, channel_id: int):
        """Edit (or create + pin) the sticky queue summary in the HITL review channel."""
        pending = await get_pending_hitl(guild_id)
        unclaimed = [r for r in pending if r['claimed_by'] is None]

        now = time.time()
        if unclaimed:
            lines = [f"📋 **Pending Reviews — {len(unclaimed)} waiting**"]
            for row in unclaimed:
                msg_id   = row['message_id']
                ch_id    = row['channel_id']
                g_id     = row['guild_id']
                link     = f"https://discord.com/channels/{g_id}/{ch_id}/{msg_id}"
                # Derive age from Discord snowflake
                created_ms = (msg_id >> 22) + 1420070400000
                age_secs   = int(now - created_ms / 1000)
                if age_secs < 60:
                    age_str = f"{age_secs}s ago"
                elif age_secs < 3600:
                    age_str = f"{age_secs // 60}m ago"
                else:
                    age_str = f"{age_secs // 3600}h ago"
                urgency = " 🔴" if age_secs > 1800 else (" ⚠️" if age_secs > 600 else "")
                user_mention = f"<@{row['original_user_id']}>"
                lines.append(f"→ [jump]({link}) • {user_mention} • {age_str}{urgency}")
            content = "\n".join(lines)
        else:
            content = "✅ **All reviews done — nothing pending**"

        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        sticky = await get_hitl_sticky(guild_id)
        if sticky:
            try:
                msg = await channel.fetch_message(sticky['message_id'])
                await msg.delete()
            except Exception:
                pass  # Message deleted already

        msg = await channel.send(content)
        await set_hitl_sticky(guild_id, channel_id, msg.id)

    @tasks.loop(seconds=30)
    async def _hitl_claim_timeout_check(self):
        """Release claims older than 5 minutes and revert the embed to claim-only."""
        from Database.database_improved import get_db
        db = await get_db()
        cutoff = time.time() - 300
        async with db.execute(
            '''
            SELECT message_id, guild_id, channel_id
            FROM hitl_claim_state
            WHERE claimed_by IS NOT NULL AND resolved = 0 AND claimed_at < ?
            ''',
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            message_id, guild_id, channel_id = row[0], row[1], row[2]
            await release_hitl_claim(message_id)

            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(message_id)
                    claim_view = HITLClaimView(message_id)
                    self.bot.add_view(claim_view, message_id=message_id)
                    await msg.edit(
                        content="⏰ **Claim expired — available for review again**",
                        view=claim_view,
                    )
                except Exception:
                    pass

            await self._update_sticky(guild_id, channel_id)

    @_hitl_claim_timeout_check.before_loop
    async def before_timeout_check(self):
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    async def cleanup_hitl_pending(self):
        """Deletes staging files older than 24 hours."""
        hitl_dir = "hitl_pending"
        if not os.path.exists(hitl_dir):
            return
        cutoff = time.time() - 86400
        for f in os.listdir(hitl_dir):
            path = os.path.join(hitl_dir, f)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.unlink(path)
            except OSError:
                pass
        # Also sweep expired compound-proof halves so the buffer stays tiny.
        try:
            await _cleanup_expired_partial_proofs()
        except Exception as e:
            logger.warning(f"[ProofAuto] partial_proofs cleanup failed: {e}")

    @cleanup_hitl_pending.before_loop
    async def before_cleanup(self):
        await self.bot.wait_until_ready()

    async def resume_processing(self, filename: str, start_node: str, force_class: str, original_message: discord.Message, cfg: dict, interaction: discord.Interaction = None, hitl_message_id: int = None, hitl_filenames: list = None):
        """Re-injects a HITL image back into the tree, forcing a specific class output for the start_node."""
        logger.info(f"[ProofAuto] Resuming {filename} at {start_node} as {force_class}")
        decision = await self.tree.process_image(filename, start_node, cfg, force_class=force_class)
        await self._execute_decision(
            decision, original_message, cfg, {"tmp_path": filename},
            interaction=interaction, hitl_message_id=hitl_message_id, hitl_filenames=hitl_filenames
        )



    async def _assign_creator_roles(self, message: discord.Message, cfg: dict, level: str):
        member = message.author
        if not isinstance(member, discord.Member):
            return
            
        role_ids = cfg["creator_code_role_ids"]
        if level == "Level 1" and role_ids:
            role_ids = [role_ids[0]]
            
        roles_to_add = [
            message.guild.get_role(rid)
            for rid in role_ids
            if message.guild.get_role(rid) is not None
            and message.guild.get_role(rid) not in member.roles
        ]
        if not roles_to_add:
            return
        try:
            await member.add_roles(*roles_to_add, reason="Creator code proof verified")
            logger.info(
                f"[ProofAuto] Assigned roles {[r.id for r in roles_to_add]} "
                f"to {member.id} in guild {message.guild.id}"
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"[ProofAuto] failed to assign roles to {member.id}: {e}")

    async def _record_flag_and_count(self, guild_id: int, user_id: int, kind: str,
                                     match_type: str | None, message_id: int) -> tuple[int, int]:
        """Record one stolen/copied flag and return the user's PRIOR flag counts
        (exact, perceptual) across all guilds. Count only — never drives action."""
        async with aiosqlite.connect(LOCAL_DB, timeout=10.0) as db:
            # We don't need _ensure_schema here since it's an old table that already exists
            async with db.execute(
                "SELECT kind, COUNT(*) FROM stolen_flags WHERE user_id=? GROUP BY kind",
                (user_id,)
            ) as cur:
                counts = dict(await cur.fetchall())
            await db.execute(
                """INSERT INTO stolen_flags (guild_id, user_id, kind, match_type, message_id, flagged_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, user_id, kind, match_type, message_id, time.time())
            )
            await db.commit()
        return counts.get("exact", 0), counts.get("perceptual", 0)

    async def _safe_reply(self, message: discord.Message, content: str):
        try:
            await message.reply(content, mention_author=False)
        except (discord.Forbidden, discord.HTTPException, AttributeError) as e:
            logger.warning(f"[ProofAuto] failed to reply: {e}")
            if hasattr(message, 'channel') and getattr(message, 'channel', None):
                try:
                    await message.channel.send(f"{message.author.mention} {content}")
                except Exception as ex:
                    logger.warning(f"[ProofAuto] failed to fallback send: {ex}")

    async def _process_videos(self, message: discord.Message, attachments: list[discord.Attachment], cfg: dict):
        hitl_channel = self.bot.get_channel(cfg.get('hitl_review_channel_id', 1516673791844155392))
        if not hitl_channel:
            return

        for att in attachments:
            ext = os.path.splitext(att.filename)[1].lower() or ".mp4"
            hitl_filename = f"hitl_pending/hitl_{message.id}_{uuid.uuid4().hex[:8]}{ext}"
            os.makedirs(os.path.dirname(hitl_filename), exist_ok=True)
            
            downloaded = False
            try:
                await att.save(hitl_filename, use_cached=False)
                downloaded = True
            except Exception as e:
                logger.warning(f"[ProofAuto] Failed to download video {att.filename}: {e}")

            embed = discord.Embed(
                title="⚠️ HITL Review Required (Video)", 
                description=f"🔗 **[Jump to Original Message]({message.jump_url})**\n\n" if hasattr(message, 'jump_url') else "",
                color=discord.Color.purple()
            )
            embed.add_field(name="User", value=message.author.mention)
            embed.add_field(name="Failed Node", value="Video_Review_Node")
            embed.add_field(name="Confidence", value="1.00")
            embed.add_field(name="Predicted Class", value="Video")

            file_obj = None
            if downloaded:
                if os.path.getsize(hitl_filename) < 25 * 1024 * 1024:
                    file_obj = discord.File(hitl_filename)
                else:
                    embed.description += f"**Video too large to upload directly.**\n[Watch original video here]({att.url})"
            else:
                embed.description += f"**Failed to download video.**\n[Watch original video here]({att.url})"

            try:
                if file_obj:
                    sent_msg = await hitl_channel.send(embed=embed, file=file_obj)
                else:
                    sent_msg = await hitl_channel.send(embed=embed)
            except discord.HTTPException:
                embed.description = (f"🔗 **[Jump to Original Message]({message.jump_url})**\n\n" if hasattr(message, 'jump_url') else "") + f"**Video upload failed (API Error/File Limit).**\n[Watch original video here]({att.url})"
                try:
                    sent_msg = await hitl_channel.send(embed=embed)
                except Exception as ex:
                    logger.error(f"[ProofAuto] Fallback video send failed: {ex}")
                    continue
            
            claim_view = HITLClaimView(sent_msg.id)
            self.bot.add_view(claim_view, message_id=sent_msg.id)
            await sent_msg.edit(view=claim_view)

            await register_hitl_review(
                message_id=sent_msg.id,
                guild_id=message.guild.id,
                channel_id=hitl_channel.id,
                start_node="Video_Review_Node",
                valid_classes=[],
                hitl_filenames=[hitl_filename] if downloaded else [],
                original_user_id=message.author.id,
                original_channel_id=message.channel.id,
                original_message_id=message.id,
            )
            await self._update_sticky(message.guild.id, hitl_channel.id)

    async def _process_images(self, message: discord.Message, attachments: list[discord.Attachment], cfg: dict):
        """
        The new Clean Architecture image processor.
        """
        analyses = await asyncio.gather(*(self._download_and_fingerprint(a) for a in attachments))
        analyses = [a for a in analyses if a is not None]
        if not analyses:
            return

        # 1. Exact Match Check (Phase 2)
        for a in analyses:
            stolen = await _find_exact_stolen(message.guild.id, message.author.id, a)
            if stolen:
                await log_stolen_detection(
                    message.guild.id, message.author.id, message.id, 'exact', stolen['match_type'],
                    stolen.get('original_user_id'), stolen.get('original_guild_id'), stolen.get('original_message_id'),
                    stolen.get('original_submitted_at'), stolen.get('original_filename'),
                    0, 0, stolen.get('detail')
                )
                await self._record_flag_and_count(message.guild.id, message.author.id, 'exact', stolen['match_type'], message.id)
                await self._store_fingerprints_only(message.guild.id, message.author.id, message.id, analyses)
                await self._safe_reply(message, STOLEN_MSG)
                for a_cleanup in analyses:
                    _safe_unlink(a_cleanup["tmp_path"])
                return

        # 2. Perceptual Match Check (Phase 4)
        loop = asyncio.get_running_loop()
        for a in analyses:
            try:
                a["phash"] = await loop.run_in_executor(None, _compute_phash, a["tmp_path"])
                a["phash_flip"] = await loop.run_in_executor(None, _compute_phash_flip, a["tmp_path"])
            except Exception as e:
                logger.warning(f"pHash failed for {a['filename']}: {e}")
                
            stolen = await _find_fuzzy_stolen(message.guild.id, message.author.id, a)
            if stolen:
                await log_stolen_detection(
                    message.guild.id, message.author.id, message.id, 'perceptual', stolen['match_type'],
                    stolen.get('original_user_id'), stolen.get('original_guild_id'), stolen.get('original_message_id'),
                    stolen.get('original_submitted_at'), stolen.get('original_filename'),
                    stolen.get('distance'), stolen.get('mirror'), stolen.get('detail')
                )
                await self._record_flag_and_count(message.guild.id, message.author.id, 'perceptual', stolen['match_type'], message.id)
                await self._store_fingerprints_only(message.guild.id, message.author.id, message.id, analyses)
                await self._safe_reply(message, STOLEN_MSG)
                for a_cleanup in analyses:
                    _safe_unlink(a_cleanup["tmp_path"])
                return
                
        # 3. Store Submissions
        await self._store_fingerprints_only(message.guild.id, message.author.id, message.id, analyses)

        # 4. Classification Phase (Cascading Tree - Batch Processing)
        decisions = []
        for a in analyses:
            decision = await self.tree.process_image(a["tmp_path"], 'Model1_Gatekeeper', cfg)
            decisions.append((decision, a))
            
            # Instant Approval: If any image passes, grant access immediately and stop.
            if decision.action in ['GRANT_LEVEL_1', 'GRANT_LEVEL_2']:
                await self._execute_decision(decision, message, cfg, a)
                for a_cleanup in analyses:
                    _safe_unlink(a_cleanup["tmp_path"])
                return

        # No single image granted on its own. Before the plain reject/HITL
        # fallback, try to satisfy the compound "follow AND like" requirement by
        # ADDING UP the confident components — both across the images in THIS
        # message (Phase 1) and against the components this user proved in an
        # earlier message still inside the 12h window (Phase 2).
        components_now = set()
        for d, _a in decisions:
            if d.action == 'REJECT_FOLLOWING_ONLY':
                components_now.add('following')
            elif d.action == 'REJECT_LIKING_ONLY':
                components_now.add('liking')
            elif d.action == 'GRANT_LEVEL_1' and (d.class_name or '').lower().startswith('following and liking'):
                components_now.update(('following', 'liking'))

        has_hitl = any(d[0].action == 'HITL' for d in decisions)

        if components_now:
            buffered = await _get_buffered_components(message.guild.id, message.author.id)
            combined = components_now | buffered

            if TWITTER_REQUIRED_COMPONENTS <= combined:
                # follow + like both proven (within one message or across the
                # window). Each half was classified at >=99%, so this is exactly
                # as trustworthy as a single "Following and liking" grant.
                await _clear_buffered_components(message.guild.id, message.author.id)
                grant = Decision('GRANT_LEVEL_1', confidence=1.0,
                                 class_name='Following and liking (combined)')
                await self._execute_decision(grant, message, cfg, decisions[0][1])
                for a in analyses:
                    _safe_unlink(a["tmp_path"])
                return

            if not has_hitl:
                # We have a confident half but not the full set, and nothing here
                # needs a human. Remember the half and tell the user what's left,
                # instead of a flat "this isn't enough" rejection.
                await _store_buffered_components(message.guild.id, message.author.id,
                                                 components_now, message.id)
                missing = TWITTER_REQUIRED_COMPONENTS - combined
                await self._safe_reply(message,
                                       self._compound_progress_msg(message, cfg, combined, missing))
                for a in analyses:
                    _safe_unlink(a["tmp_path"])
                return
            # else: a required piece is uncertain — fall through to human review
            # below, which sees the whole batch.

        # Fallback logic:
        hitl_decisions = [d for d in decisions if d[0].action == 'HITL']
        if hitl_decisions:
            # If any image needs review, send the entire batch to staff. Pass
            # along any components the bot already recognized so staff only has
            # to verify the uncertain half.
            best_decision, best_analysis = hitl_decisions[0]
            await self._execute_hitl_batch(best_decision, message, cfg, analyses,
                                           recognized=components_now)
        else:
            # All images were explicitly rejected. Just send the rejection message for the first one.
            if decisions:
                await self._execute_decision(decisions[0][0], message, cfg, decisions[0][1])

        # Cleanup original temporary files
        for a in analyses:
            _safe_unlink(a["tmp_path"])

    def _compound_progress_msg(self, message: discord.Message, cfg: dict,
                               proven: set, missing: set) -> str:
        """Friendly 'one half down, send the other' reply used when a user has
        proven part of the follow+like requirement and we're holding it for 12h."""
        label = {"following": "**following**", "liking": "**liking the pinned tweet**"}
        proven_txt = " and ".join(label[c] for c in ("following", "liking") if c in proven)
        missing_txt = " and ".join(label[c] for c in ("following", "liking") if c in missing)
        return (
            f"✅ Got your proof of {proven_txt}! You're almost there — now send "
            f"proof of {missing_txt} within **12 hours** and you'll be granted access automatically."
        )

    async def _classes_for_node(self, node_name: str) -> list:
        """The class labels a node can output — i.e. the buttons staff should see
        for that step of a manual walk. ViT nodes carry their labels statically;
        YOLO nodes keep them on the loaded model, so we make sure the weights are
        loaded first (loading != classifying — NO inference runs here) before
        reading `model.names`."""
        node = self.tree.nodes.get(node_name) if node_name else None
        if node is None:
            return ['Garbage']
        if getattr(node, 'class_names', None):
            return list(node.class_names)
        try:
            await node._ensure_model_loaded()
        except Exception as e:
            logger.warning(f"[ProofAuto] couldn't load {node_name} to read its classes: {e}")
        model = getattr(node, 'model', None)
        if model is not None:
            try:
                return list(model.names.values())
            except Exception:
                pass
        return ['Garbage']

    async def _execute_hitl_batch(self, decision, message: discord.Message, cfg: dict, analyses: list[dict], recognized: set = None):
        hitl_channel = self.bot.get_channel(cfg.get('hitl_review_channel_id', 1516673791844155392))
        if not hitl_channel:
            return

        files = []
        hitl_filenames = []

        for i, a in enumerate(analyses[:10]):
            ext = os.path.splitext(a["tmp_path"])[1] or ".jpg"
            hitl_filename = f"hitl_pending/hitl_{message.id}_{uuid.uuid4().hex[:8]}_{i}{ext}"
            os.makedirs(os.path.dirname(hitl_filename), exist_ok=True)
            shutil.copy(a["tmp_path"], hitl_filename)
            hitl_filenames.append(hitl_filename)
            files.append(discord.File(hitl_filename, filename=f"review_{i}{ext}"))

        failed_node_name = decision.failed_node

        # 2+ images → staff reviews from the very top so they see the full proof
        if len(analyses) > 1:
            failed_node_name = "Model1_Gatekeeper"

        valid_classes = []
        if failed_node_name and failed_node_name in self.tree.nodes:
            node = self.tree.nodes[failed_node_name]
            if hasattr(node, 'class_names'):
                valid_classes = node.class_names
            elif hasattr(node, 'model') and node.model:
                valid_classes = list(node.model.names.values())
        if not valid_classes:
            valid_classes = ['Garbage']

        embed = discord.Embed(
            title="⚠️ HITL Review Required",
            description=f"🔗 **[Jump to Original Message]({message.jump_url})**" if hasattr(message, 'jump_url') else "",
            color=discord.Color.orange()
        )
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)")
        embed.add_field(name="Failed Node", value=failed_node_name or "Unknown")
        embed.add_field(name="Confidence", value=f"{decision.confidence:.2%}")
        if recognized:
            label = {"following": "Following ✅", "liking": "Liking ✅"}
            recognized_txt = ", ".join(label[c] for c in ("following", "liking") if c in recognized)
            embed.add_field(
                name="Bot already recognized",
                value=f"{recognized_txt} — just verify the rest.",
                inline=False,
            )
        embed.set_footer(text=f"Batch of {len(files)}")

        try:
            sent_msg = await hitl_channel.send(embed=embed, files=files)
        except discord.HTTPException as e:
            logger.error(f"[ProofAuto] Failed to send HITL batch message: {e}")
            return
            
        claim_view = HITLClaimView(sent_msg.id)
        self.bot.add_view(claim_view, message_id=sent_msg.id)
        await sent_msg.edit(view=claim_view)

        await register_hitl_review(
            message_id=sent_msg.id,
            guild_id=message.guild.id,
            channel_id=hitl_channel.id,
            start_node=failed_node_name,
            valid_classes=valid_classes,
            hitl_filenames=hitl_filenames,
            original_user_id=message.author.id,
            original_channel_id=message.channel.id,
            original_message_id=message.id,
        )
        await self._update_sticky(message.guild.id, hitl_channel.id)

    async def _execute_decision(self, decision, message: discord.Message, cfg: dict, analysis: dict, interaction: discord.Interaction = None, hitl_message_id: int = None, hitl_filenames: list = None):
        if interaction and hitl_message_id and decision.action != 'HITL':
            from Database.database_improved import resolve_hitl
            await resolve_hitl(hitl_message_id)

            # This path is only reached from resume_processing (a staff class-pick
            # in make_callback) AND the decision is terminal — i.e. the class-pick
            # RESOLVED the proof rather than handing off to another HITL card.
            # Log review_completed so the Management bot counts it as one finished
            # review (it filters on action='review_completed'). Global override /
            # discard callbacks log their own review_completed and do NOT pass an
            # interaction here, so there is no double-count.
            try:
                from utils.global_logger import log_event
                await log_event(
                    category="hitl_review",
                    action="review_completed",
                    actor=interaction.user,
                    target=message.author,
                    details={"verdict": decision.action, "resolved_via": "class_pick"},
                )
            except Exception as e:
                logger.warning(f"[ProofAuto] failed to log review_completed: {e}")

            outcome_text = decision.action
            if decision.action == 'GRANT_LEVEL_1': outcome_text = "Granted Level 1"
            elif decision.action == 'GRANT_LEVEL_2': outcome_text = "Granted Level 2"
            elif decision.action.startswith('REJECT_'):
                reason = decision.action.replace('REJECT_', '').replace('_', ' ').title()
                outcome_text = f"Rejected ({reason})"

            new_content = (
                f"✅ **Review Completed**\n"
                f"**Staff:** {interaction.user.mention}\n"
                f"**User:** {message.author.mention}\n"
                f"**Outcome:** `{outcome_text}`"
            )
            try:
                await interaction.edit_original_response(content=new_content, view=None)
            except Exception:
                pass

        if decision.action == 'GRANT_LEVEL_1':
            await self._assign_creator_roles(message, cfg, "Level 1")
            msg = await _get_next_success_message(message.guild.id, "Level 1")
            await self._safe_reply(message, msg)
        elif decision.action == 'GRANT_LEVEL_2':
            await self._assign_creator_roles(message, cfg, "Level 2")
            msg = await _get_next_success_message(message.guild.id, "Level 2")
            await self._safe_reply(message, msg)
        elif decision.action == 'REJECT_DYNAMIC':
            if message.guild.id == 988564962802810961:
                reply_msg = DynamicHandlers.get_dynamic_garbage_reply(message.author)
            else:
                reply_msg = DynamicHandlers.get_loot_routes_garbage_reply(message.author)
            await self._safe_reply(message, reply_msg)
        elif decision.action == 'REJECT_INVITE':
            reply_msg = DynamicHandlers.get_invite_rejection(message.guild, cfg, message.author)
            await self._safe_reply(message, reply_msg)
        elif decision.action == 'REJECT_FOLLOWING_ONLY':
            if cfg.get('following_only_msg'):
                await self._safe_reply(message, cfg['following_only_msg'])
        elif decision.action == 'REJECT_LIKING_ONLY':
            if message.guild.id == 988564962802810961:
                channel_id = 1210798761329295440  # proof-submission channel (same as the other rejection prompts)
                msg = f"Please show **__proof__** of __**following**__ the account https://x.com/Wavedropmaps and liking the **pinned tweet** https://x.com/Wavedropmaps/status/1896931137722982898, send proof in <#{channel_id}>"
                await self._safe_reply(message, msg)
        elif decision.action == 'REJECT_PRESS_SEARCH':
            await self._safe_reply(message, cfg['press_search_msg'])
        elif decision.action == 'REJECT_ZOOM_OUT':
            await self._safe_reply(message, cfg['zoom_out_msg'])
        elif decision.action == 'REJECT_WRONG_CODE':
            if cfg.get('wrong_code_msg'):
                await self._safe_reply(message, cfg['wrong_code_msg'])
        elif decision.action == 'HITL':
            hitl_channel = self.bot.get_channel(cfg.get('hitl_review_channel_id', 1516673791844155392))
            if hitl_channel:
                ext = os.path.splitext(analysis["tmp_path"])[1] or ".jpg"
                hitl_filename = f"hitl_pending/hitl_{message.id}_{uuid.uuid4().hex[:8]}{ext}"
                os.makedirs(os.path.dirname(hitl_filename), exist_ok=True)
                shutil.copy2(analysis["tmp_path"], hitl_filename)

                failed_node_name = decision.failed_node
                valid_classes = []
                if failed_node_name and failed_node_name in self.tree.nodes:
                    node = self.tree.nodes[failed_node_name]
                    if hasattr(node, 'class_names'):
                        valid_classes = node.class_names
                    elif hasattr(node, 'model') and node.model:
                        valid_classes = list(node.model.names.values())
                if not valid_classes:
                    valid_classes = ['Garbage']

                if interaction and hitl_message_id:
                    from Database.database_improved import update_hitl_node
                    await update_hitl_node(hitl_message_id, failed_node_name, valid_classes)

                    new_view = HITLActionView(
                        cog=self,
                        message_id=hitl_message_id,
                        hitl_filenames=hitl_filenames or [analysis["tmp_path"]],
                        start_node=failed_node_name,
                        valid_classes=valid_classes,
                        original_message=message,
                        cfg=cfg
                    )
                    self.bot.add_view(new_view, message_id=hitl_message_id)

                    if interaction.message.embeds:
                        embed = interaction.message.embeds[0]
                        try:
                            embed.set_field_at(1, name="Failed Node", value=failed_node_name or "Unknown")
                            embed.set_field_at(2, name="Confidence", value=f"{decision.confidence:.2f}")
                            embed.set_field_at(3, name="Predicted Class", value=decision.class_name or "Unknown")
                        except IndexError:
                            pass
                    else:
                        embed = discord.Embed(title="⚠️ HITL Review Required", color=discord.Color.orange())
                        embed.add_field(name="User", value=message.author.mention if hasattr(message, 'author') and hasattr(message.author, 'mention') else "Unknown")
                        embed.add_field(name="Failed Node", value=failed_node_name or "Unknown")
                        embed.add_field(name="Confidence", value=f"{decision.confidence:.2f}")
                        embed.add_field(name="Predicted Class", value=decision.class_name or "Unknown")
                    
                    try:
                        await interaction.edit_original_response(
                            content=f"🔒 **Claimed by {interaction.user.mention}** (Moved to {failed_node_name})",
                            embed=embed,
                            view=new_view
                        )
                    except Exception as e:
                        logger.error(f"[ProofAuto] Error editing original response for HITL loop: {e}")
                    return

                embed = discord.Embed(title="⚠️ HITL Review Required", color=discord.Color.orange())
                embed.add_field(name="User", value=message.author.mention)
                embed.add_field(name="Failed Node", value=failed_node_name or "Unknown")
                embed.add_field(name="Confidence", value=f"{decision.confidence:.2f}")
                embed.add_field(name="Predicted Class", value=decision.class_name or "Unknown")

                # Send without view first to get the message ID
                sent_msg = await hitl_channel.send(
                    embed=embed, file=discord.File(hitl_filename)
                )
                # Now attach the claim view with the real message ID
                claim_view = HITLClaimView(sent_msg.id)
                self.bot.add_view(claim_view, message_id=sent_msg.id)
                await sent_msg.edit(view=claim_view)

                await register_hitl_review(
                    message_id=sent_msg.id,
                    guild_id=message.guild.id,
                    channel_id=hitl_channel.id,
                    start_node=failed_node_name,
                    valid_classes=valid_classes,
                    hitl_filenames=[hitl_filename],
                    original_user_id=message.author.id,
                    original_channel_id=message.channel.id,
                    original_message_id=message.id,
                )
                await self._update_sticky(message.guild.id, hitl_channel.id)


async def setup(bot):
    await bot.add_cog(ProofAutomationTask(bot))

    # NOTE: models are loaded lazily on the first proof image (not at startup),
    # so the bot's idle RAM stays low until then. Once loaded they stay resident.
    logger.info("✅ ProofAutomationTask cog loaded")
