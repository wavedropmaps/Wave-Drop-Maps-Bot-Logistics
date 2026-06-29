"""
Map Request Queue Commands for Wave Logistics Bot.

Commands:
- -z addmap - Add a map to the queue (map request helper role only)
- -z removequeue - Remove a map from queue by number
- -z setchannel - Set queue display channel
- -z setconfigqueue - Set server mode (drop_map vs loot_route)
- -z queueconfig - Show server configuration
"""

import discord
from discord.ext import commands
import json
import asyncio
from typing import Optional, List
from datetime import datetime
import Database.database_improved as database
import sys
import os

# Add utils to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils.queue_priority import get_sorted_map_requests
from utils.queue_encoding import number_to_alpha

import glob

# ---------------------------------------------------------------------------
# Durable local image storage
#
# Discord CDN image URLs are signed and expire ~24h, so storing only the URL
# means queue images go blank after a day. Instead we keep a permanent copy of
# every uploaded map image on the bot's own disk and RE-ATTACH that file to the
# queue message. The image then lives ON the message as an attachment, which
# never expires (Discord re-signs the attachment URL every time the bot fetches
# the message). One file per queue entry: queue_images/<guild_id>/<code>.<ext>.
# ---------------------------------------------------------------------------
QUEUE_IMAGE_DIR = "queue_images"
_ALLOWED_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def save_queue_image(guild_id: int, queue_code: str, image_data: bytes, filename: str = None):
    """Persist uploaded image bytes to local disk for a queue entry.

    Returns the saved path, or None if there were no bytes to save. Overwrites
    any previous file for the same code (e.g. when a map's image is replaced).
    """
    if not image_data:
        return None
    ext = (os.path.splitext(filename or "")[1] or "").lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        ext = ".png"
    folder = os.path.join(QUEUE_IMAGE_DIR, str(guild_id))
    os.makedirs(folder, exist_ok=True)
    # Remove any prior copy with a different extension so find_queue_image is unambiguous.
    for stale in glob.glob(os.path.join(folder, f"{queue_code}.*")):
        try:
            os.remove(stale)
        except OSError:
            pass
    path = os.path.join(folder, f"{queue_code}{ext}")
    try:
        with open(path, "wb") as fh:
            fh.write(image_data)
        return path
    except OSError as e:
        print(f"WARNING: could not save queue image for {guild_id}/{queue_code}: {e}")
        return None


def find_queue_image(guild_id: int, queue_code: str):
    """Return the local image path for a queue entry, or None if none saved.

    The literal '.' in the glob means code 'a' matches 'a.png' but never 'ab.png'.
    """
    matches = glob.glob(os.path.join(QUEUE_IMAGE_DIR, str(guild_id), f"{queue_code}.*"))
    return matches[0] if matches else None

# Wave Management Bot — allowlisted for bot-to-bot command automation
# (e.g. the drop-map voting winner auto-runs `-z addmap`). Must match the
# same constant in Main.py.
WAVE_MANAGEMENT_BOT_ID = 1269188273201352768

async def refresh_sticky_message(channel: discord.TextChannel, guild: discord.Guild):
    """
    Refresh the sticky message at the bottom of the queue channel.
    Sends placeholder first (no mentions) then edits to show role highlights without pinging.
    """
    try:
        # Get config to check server mode
        config = await database.get_server_queue_config(guild.id)
        if not config or config.get("server_mode") != "drop_map":
            return  # Only show sticky in drop_map mode

        # Delete old sticky message if it exists
        old_sticky_id = await database.get_sticky_message_id(guild.id)
        if old_sticky_id:
            try:
                old_msg = await channel.fetch_message(old_sticky_id)
                await old_msg.delete()
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass  # Message already deleted or no permission

        # Step 1: Send placeholder (no mentions = no pings)
        placeholder = await channel.send(".")

        # Step 2: Build content with resolved role and channel mentions
        # Resolve roles by name
        silver_role = discord.utils.get(guild.roles, name="Silver (LVL 10)")
        active_role = discord.utils.get(guild.roles, name="Active")
        contributor_role = discord.utils.get(guild.roles, name="Wave Contributor")
        priority_role = discord.utils.get(guild.roles, name="Paid Priority")

        # Resolve channels by partial name match
        level_channel = None
        purchase_channel = None

        for ch in guild.text_channels:
            if "level-invite-rewards" in ch.name.lower():
                level_channel = ch
            elif "purchase" in ch.name.lower():
                purchase_channel = ch

        # Build mention strings (fallback to plain text if role/channel not found)
        silver_mention = silver_role.mention if silver_role else "@Silver (LVL 10)"
        active_mention = active_role.mention if active_role else "@Active"
        contributor_mention = contributor_role.mention if contributor_role else "@Wave Contributor"
        priority_mention = priority_role.mention if priority_role else "@Paid Priority"

        level_mention = level_channel.mention if level_channel else "#🚀・level-invite-rewards"
        supporters_mention = "<#1364454494665969664>"
        purchase_mention = purchase_channel.mention if purchase_channel else "#💲・purchase"

        # Build the sticky message content
        sticky_content = (
            "# To Request A Drop Map That Wave Does Not Have!\n\n"
            "> You will need to have one of the following roles listed below, next to the role will be indicated how to get the role!\n\n"
            f"- {silver_mention} or {active_mention} **please view** {level_mention}\n\n"
            f"- <:new_1:1399375863946154094><:new_2:1399375888055009340> {contributor_mention} **please view** {supporters_mention}\n\n"
            f"- <:new_1:1399375863946154094><:new_2:1399375888055009340> {priority_mention} **make a ticket in** {purchase_mention}"
        )

        # Step 3: Edit placeholder to show real content with role mentions (no ping since it's an edit)
        await placeholder.edit(content=sticky_content)

        # Step 4: Store the new sticky message ID
        await database.set_sticky_message_id(guild.id, placeholder.id)

    except Exception as e:
        print(f"Error refreshing sticky message: {e}")
        # Don't crash the whole queue update if sticky fails

class MapRequestView(discord.ui.View):
    """View for the -z addmap command with dynamic dropdown editing."""
    
    def __init__(self, ctx, timeout=180):
        super().__init__(timeout=timeout)
        self.ctx = ctx
        self.step = 1  # Current step in the process
        self.selected_option = None
        self.queue_number = None
        self.image_url = None  # Keep for backward compatibility
        self.image_data = None  # Bytes of the image for attachment
        self.image_filename = None  # Original filename for attachment
        self.user_ids = []
        self.description = None
        self.description_addition = None  # For appending to existing descriptions
        self.map_type = None
        self.route_type = None  # 'loot_route' or 'surge_route' (loot_route mode only)
        self.message = None  # Store the message reference for editing
        self.server_mode = None  # Server mode: drop_map or loot_route

    @staticmethod
    def _request_route_type(req):
        """Route type of a stored request ('loot_route' when unset)."""
        return req.get("route_type") or "loot_route"

    async def _find_duplicate_users(self, route_type: str = None):
        """
        Users already tied to an active request, as (user_id, queue_code) pairs.
        On loot_route servers the rule is per route type — each person may have
        one loot route AND one surge route request at the same time — so pass
        the type being created/updated. Drop-map servers keep the global
        one-request-per-person rule (route_type ignored there).
        """
        active_requests = await database.get_all_map_requests(self.ctx.guild.id)
        if route_type and self.server_mode == "loot_route":
            active_requests = [r for r in active_requests
                               if self._request_route_type(r) == route_type]
        duplicates = []
        for user_id in self.user_ids:
            for req in active_requests:
                if user_id in req.get('user_ids', []):
                    duplicates.append((user_id, req['queue_number']))
                    break
        return duplicates

    async def _show_duplicate_users_error(self, step_label: str, duplicate_users, route_type: str = None):
        """Cancel the wizard with the users-already-queued error."""
        type_label = {"loot_route": "loot route", "surge_route": "surge route"}.get(route_type, "map")
        mention_list = "\n".join(
            [f"  • <@{uid}> — already tied to Queue Code `{qnum}`"
             for uid, qnum in duplicate_users]
        )
        note = ""
        if self.server_mode == "loot_route":
            note = "*(A person can still have one loot route and one surge route request at the same time.)*\n"
        if self.message:
            await self.message.edit(
                content=(
                    f"❌ **{step_label}: User(s) Already In Queue**\n\n"
                    f"The following user(s) already have an active {type_label} request and cannot be added again:\n\n"
                    f"{mention_list}\n\n"
                    f"**Each person can only have one active {type_label} request at a time.**\n"
                    f"{note}"
                    f"To add them, their existing request must be completed or removed first.\n\n"
                    f"*Command cancelled — no changes were made.*"
                ),
                view=None
            )

    async def calculate_priority(self, user_ids, server_mode=None):
        """Calculate the highest priority among users based on their roles."""
        if not user_ids:
            return None, None, None

        # Use provided server_mode or fall back to self.server_mode
        if server_mode is None:
            server_mode = self.server_mode

        # Priority mappings based on server mode
        if server_mode == "drop_map":
            # Drop map server priority order (1st is highest)
            priority_order = [
                (1, ["Paid Priority"]),
                (2, ["Wave Contributor"]),
                (3, ["Unreal (LVL 50)"]),
                (4, ["Elite (LVL 30)", "Active"]),
                (5, ["Silver (LVL 10)", "Staff", "Drop Map Tester", "Map Creator",
                      "Loot Route Map Creator", "Tips and Tricks Helper", "Promoters", "Drop Map Reviewer"])
            ]
        else:  # loot_route
            # Loot route server priority order (1st is highest)
            priority_order = [
                (1, ["Server Booster", "Wave Contributor"]),
                (2, ["Battle Pass Supporter"]),
                (3, ["Unreal (LVL 50)"]),
                (4, ["Elite (LVL 30)", "Active"]),
                (5, ["Bronze (LVL 5)", "Staff", "Drop Map Tester", "Map Creator",
                      "Loot Route Map Creator", "Tips and Tricks Helper", "Promoters", "Drop Map Reviewer"]),
                (6, ["Access", "Access Invite way"])
            ]

        highest_priority_level = None
        highest_priority_role = None
        highest_priority_user = None
        
        for user_id in user_ids:
            try:
                # Fetch the member from the guild
                member = self.ctx.guild.get_member(user_id)
                if not member:
                    # Try to fetch if not in cache
                    try:
                        member = await self.ctx.guild.fetch_member(user_id)
                    except:
                        continue
                
                # Check each role in priority order
                for level, roles in priority_order:
                    for role_name in roles:
                        # Case-sensitive role search
                        role = discord.utils.get(member.roles, name=role_name)
                        if role:
                            # Found a role at this priority level
                            if highest_priority_level is None or level < highest_priority_level:
                                highest_priority_level = level
                                highest_priority_role = role  # Store role object instead of name
                                highest_priority_user = user_id
                            break  # No need to check other roles at same level
                    
                    # If we found a role at this level, don't check lower levels for this user
                    if highest_priority_level == level:
                        break
            
            except Exception as e:
                print(f"Error checking priority for user {user_id}: {e}")
                continue
        
        return highest_priority_level, highest_priority_role, highest_priority_user
    
    async def update_message(self, interaction: discord.Interaction, content: str, options: list = None, placeholder: str = "Select an option..."):
        """Update the message with new content and dropdown options."""
        # Clear existing components
        self.clear_items()
        
        if options:
            # Create new dropdown with the provided options
            dropdown = discord.ui.Select(
                placeholder=placeholder,
                options=options,
                min_values=1,
                max_values=1
            )
            dropdown.callback = self.handle_dropdown
            self.add_item(dropdown)
        
        # Update the message
        if interaction.response.is_done():
            if self.message:
                await self.message.edit(content=content, view=self)
        else:
            await interaction.response.edit_message(content=content, view=self)
        
        # Store the message reference if not already set
        if not self.message:
            self.message = interaction.message
    
    async def handle_dropdown(self, interaction: discord.Interaction):
        """Handle dropdown selection and move to next step."""
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("This is not your command!", ephemeral=True)
            return
            
        value = interaction.data["values"][0]
        
        if self.step == 1:
            # Step 1: Existing or new request
            self.selected_option = value
            
            if value == "existing":
                self.step = 2
                # Get existing queue numbers
                map_requests = await database.get_all_map_requests(self.ctx.guild.id)
                if not map_requests:
                    await interaction.response.edit_message(
                        content="❌ No existing queue entries found. Please use 'new request' instead.",
                        view=None
                    )
                    return
                
                # Create dropdown with existing queue numbers, labelled by route type
                def _route_label(req):
                    rt = req.get("route_type", "loot_route")
                    if rt == "surge_route":
                        return "⚡ Surge"
                    else:
                        return "🗺️ Loot"

                options = [
                    discord.SelectOption(
                        label=f"{_route_label(req)} — Queue #{req['queue_number']}",
                        value=str(req['queue_number']),
                        description=f"{len(req['user_ids'])} user(s)" + (f" · {req.get('map_type')}" if req.get('map_type') else "")
                    )
                    for req in map_requests
                ]

                await self.update_message(
                    interaction,
                    "📋 **Step 2: Select existing queue number**\n"
                    "🔍 Choose which queue entry you want to update:",
                    options,
                    placeholder="Select queue number..."
                )
            else:  # new request
                self.queue_number = await database.get_next_queue_number(self.ctx.guild.id)
                # Check server config for mode
                config = await database.get_server_queue_config(self.ctx.guild.id)
                self.server_mode = config.get("server_mode", "drop_map") if config else "drop_map"
                
                # NEW FLOW: For both loot_route and drop_map servers, ask for image first
                self.step = 2
                if self.server_mode == "loot_route":
                    await self.update_message(
                        interaction,
                        f"📸 **Step 2: New request — Queue #{self.queue_number}**\n"
                        f"📤 Please upload your route image.",
                        None
                    )
                else:
                    # For drop_map servers, automatically set map_type to "drop_map"
                    self.map_type = "drop_map"
                    await self.update_message(
                        interaction,
                        f"📸 **Step 2: New request — Queue #{self.queue_number}**\n"
                        f"📤 Please upload your map image.",
                        None
                    )
                # Wait for user to send image only
                await self.wait_for_image(interaction)

        elif self.step == 2 and self.selected_option == "existing":
            # Step 2: Selected existing queue code (alphabetical)
            self.queue_number = value.lower()  # Convert to lowercase for consistency
            self.step = 3
            
            # Get existing request details
            existing_request = await database.get_map_request(self.ctx.guild.id, self.queue_number)
            if existing_request:
                # For existing requests, we don't need a new image
                # Use the existing image URL from the database
                self.image_url = existing_request.get("image_url")
                # Clear any existing image data since we're not uploading a new image
                self.image_data = None
                self.image_filename = None
                desc = existing_request.get("description") or "(no description)"
                users = len(existing_request.get("user_ids", []))
                
                # Get server mode from config
                config = await database.get_server_queue_config(self.ctx.guild.id)
                self.server_mode = config.get("server_mode", "drop_map") if config else "drop_map"
                
                # Get map_type and route_type from existing request
                self.route_type = existing_request.get("route_type") or ("loot_route" if self.server_mode == "loot_route" else "drop_map")
                self.map_type = existing_request.get("map_type") or existing_request.get("gamemode")

                # Check if we need to ask for game mode — only for loot routes here.
                # (Existing surge entries from before game-mode support have none;
                # this prompt path finishes the flow as a NEW request downstream,
                # so routing all of them through it would duplicate queue entries.)
                if self.server_mode == "loot_route" and self.route_type != "surge_route" and not self.map_type:
                    # This is a loot_route server but the existing request has no game mode
                    # Ask for game mode first
                    self.step = 3  # Game mode step
                    image_status = "✅ Uploaded" if self.image_url else "❌ Not provided"
                    await self.update_message(
                        interaction,
                        f"🎮 **Step 3: Update existing loot route #{self.queue_number}**\n"
                        f"📊 Current Status:\n"
                        f"  📸 Image: {image_status}\n"
                        f"  👥 Users: {users} user(s)\n\n"
                        f"**🎮 Please type the game mode (e.g., Duos, Solos etc):**\n"
                        f"*Note: You're updating an existing loot route. Description will be asked separately.*",
                        None
                    )
                    # Wait for game mode (which will then go to user IDs)
                    await self.wait_for_gamemode_after_user_ids(interaction)
                else:
                    # Show current status
                    image_status = "✅ Uploaded" if self.image_url else "❌ Not provided"
                    await self.update_message(
                        interaction,
                        f"📝 **Step 3: Update existing queue #{self.queue_number}**\n"
                        f"📊 Current Status:\n"
                        f"  📸 Image: {image_status}\n"
                        f"  👥 Users: {users} user(s)\n"
                        f"  📝 Description: {desc}\n\n"
                        f"**👥 Please mention users or provide their IDs to add:**\n"
                        f"*Format: comma-separated IDs or mentions, or type 'skip'*\n"
                        f"*Example: 12345, 67890, 54321*\n"
                        f"*Note: You're updating an existing queue entry.*",
                        None
                    )
                    # Go to user IDs step first, then description
                    await self.wait_for_user_ids(interaction, is_existing=True)

        # Note: The loot_route gamemode selection is now handled by wait_for_gamemode method
        # This elif block is no longer needed
    
    async def wait_for_gamemode(self, interaction: discord.Interaction):
        """Wait for user to type game mode as text - for loot_route mode."""
        def check(m):
            return m.author.id == self.ctx.author.id and m.channel.id == self.ctx.channel.id
        
        try:
            msg = await self.ctx.bot.wait_for('message', timeout=60.0, check=check)
            
            # Process the message - capture game mode text only (description will be asked separately)
            content = msg.content.strip()
            if content:
                # Parse gamemode only - ignore any description
                # Take only the first line as game mode
                lines = content.split('\n', 1)
                game_mode = lines[0].strip()
                
                # Strip any dash separator from the game mode
                # e.g., "Duos - stealth approach recommended" becomes just "Duos"
                if ' - ' in game_mode:
                    game_mode = game_mode.split(' - ', 1)[0].strip()
                
                self.map_type = game_mode
                # Do NOT set self.description here - description will be asked separately
            
            # DO NOT delete user message - keeping it ensures any attachment URLs remain valid
            # Move to image step - edit same message
            self.step = 3
            if self.message:
                desc_preview = f"\nDescription: {self.description[:50]}{'...' if self.description and len(self.description) > 50 else ''}" if self.description else ""
                await self.message.edit(
                    content=f"📸 **Step 3: {self.map_type} — Queue #{self.queue_number}**{desc_preview}\n"
                            f"Please upload your route image.",
                    view=None
                )
            
            # Wait for image
            await self.wait_for_image(interaction)
            
        except asyncio.TimeoutError:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for game mode.**\nThe command has been cancelled.",
                    view=None
                )
        except Exception as e:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Error: {str(e)}**\nThe command has been cancelled.",
                    view=None
                )
    
    async def wait_for_route_type(self, interaction: discord.Interaction):
        """Show a dropdown to select Loot Route or Surge Route."""

        class RouteTypeSelect(discord.ui.Select):
            def __init__(self_inner):
                options = [
                    discord.SelectOption(label="Loot Route", value="loot_route", emoji="🗺️",
                                         description="Standard loot route request"),
                    discord.SelectOption(label="Surge Route", value="surge_route", emoji="⚡",
                                         description="Surge route request"),
                ]
                super().__init__(placeholder="Select request type...", options=options, min_values=1, max_values=1)

            async def callback(self_inner, select_interaction: discord.Interaction):
                await select_interaction.response.defer()
                self.route_type = self_inner.values[0]
                self_inner.view.stop()

        class RouteTypeView(discord.ui.View):
            def __init__(self_inner):
                super().__init__(timeout=60)
                self_inner.add_item(RouteTypeSelect())

        self.step = 4  # Route type step
        view = RouteTypeView()
        if self.message:
            await self.message.edit(
                content=f"📌 **Step 4: Request Type**\n"
                        f"📊 Current Status:\n"
                        f"• Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                        f"• Users: {len(self.user_ids)} user(s)\n\n"
                        f"**Is this a Loot Route or Surge Route request?**",
                view=view
            )

        timed_out = await view.wait()
        if timed_out or not self.route_type:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for route type selection.**\nThe command has been cancelled.",
                    view=None
                )
            return

        # Deferred duplicate check — now that the route type is known, enforce
        # one active request per person per type (1 loot + 1 surge allowed).
        if self.user_ids:
            duplicate_users = await self._find_duplicate_users(self.route_type)
            if duplicate_users:
                await self._show_duplicate_users_error("Step 4", duplicate_users, self.route_type)
                return

        # Now branch: loot route → ask game mode, surge route → ask description
        if self.route_type == "loot_route" and not self.map_type:
            self.step = 5
            if self.message:
                await self.message.edit(
                    content=f"🎮 **Step 5: Game Mode**\n"
                            f"📊 Current Status:\n"
                            f"• Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                            f"• Users: {len(self.user_ids)} user(s)\n"
                            f"• Type: Loot Route\n\n"
                            f"**Please type the game mode (e.g., Duos, Solos, Trios etc):**",
                    view=None
                )
            await self.wait_for_gamemode_after_user_ids(interaction)
        else:
            # Surge route — ask game mode then description
            self.step = 5
            if self.message:
                await self.message.edit(
                    content=f"🎮 **Step 5: Game Mode**\n"
                            f"📊 Current Status:\n"
                            f"• Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                            f"• Users: {len(self.user_ids)} user(s)\n"
                            f"• Type: ⚡ Surge Route\n\n"
                            f"**Please type the game mode (e.g., Duos, Solos, Trios etc):**",
                    view=None
                )
            await self.wait_for_gamemode_after_user_ids(interaction)

    async def wait_for_gamemode_after_user_ids(self, interaction: discord.Interaction):
        """Wait for user to type game mode after user IDs have been provided."""
        def check(m):
            return m.author.id == self.ctx.author.id and m.channel.id == self.ctx.channel.id
        
        try:
            msg = await self.ctx.bot.wait_for('message', timeout=60.0, check=check)
            
            # Process the message - capture game mode text only (description will be asked separately)
            content = msg.content.strip()
            if content:
                # Parse gamemode only - ignore any description
                # Take only the first line as game mode
                lines = content.split('\n', 1)
                game_mode = lines[0].strip()
                
                # Strip any dash separator from the game mode
                # e.g., "Duos - stealth approach recommended" becomes just "Duos"
                if ' - ' in game_mode:
                    game_mode = game_mode.split(' - ', 1)[0].strip()
                
                self.map_type = game_mode
                # Do NOT set self.description here - description will be asked separately
            
            # DO NOT delete user message - keeping it ensures any attachment URLs remain valid
            # For loot_route servers, after getting gamemode, ask for description separately
            # Always ask for description (game mode and description are separate steps)
            self.step = 5  # Description step
            if self.message:
                await self.message.edit(
                    content=f"📝 **Step 5: Description**\n"
                            f"📊 Current Status:\n"
                            f"  📸 Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                            f"  👥 Users: {len(self.user_ids)} user(s)\n"
                            f"  🎮 Game Mode: {self.map_type}\n\n"
                            f"**📝 Please type a description for this route:**\n"
                            f"*Type your description or 'skip' to leave blank*",
                    view=None
                )
            
            # Wait for description
            await self.wait_for_description(interaction, is_existing=False)
            
        except asyncio.TimeoutError:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for game mode.**\nThe command has been cancelled.",
                    view=None
                )
        except Exception as e:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Error: {str(e)}**\nThe command has been cancelled.",
                    view=None
                )
    
    async def wait_for_image(self, interaction: discord.Interaction, is_existing=False):
        """Wait for user to send image only - first step after image request."""
        def check(m):
            return m.author.id == self.ctx.author.id and m.channel.id == self.ctx.channel.id
        
        try:
            msg = await self.ctx.bot.wait_for('message', timeout=120.0, check=check)
            
            # Process the message - capture image only
            if msg.attachments:
                attachment = msg.attachments[0]
                
                # Get the attachment URL - DO NOT strip query parameters as Discord embeds need them
                attachment_url = attachment.url
                # Keep the full URL with query parameters - Discord embeds need the full URL
                # Query parameters are required for proper embed display of Discord attachments
                self.image_url = attachment_url
                
                # Download the attachment data for later use in queue messages
                try:
                    self.image_data = await attachment.read()
                    self.image_filename = attachment.filename
                    print(f"DEBUG: Downloaded image '{self.image_filename}' ({len(self.image_data)} bytes)")
                except Exception as e:
                    print(f"WARNING: Failed to download image attachment: {e}")
                    # Continue with URL-only approach as fallback
            
            # DO NOT delete user message - keeping it ensures attachment URL remains valid
            # After image, go to user IDs for both drop_map and loot_route
            # For loot_route servers without map_type, we'll handle gamemode after user IDs
            self.step = 3  # User IDs step (consistent for all cases)
            if self.message:
                if self.server_mode == "loot_route" and self.description:
                    # Show description preview if already provided
                    await self.message.edit(
                        content=f"👥 **Step 3: User IDs**\n"
                                f"📊 Current Status:\n"
                                f"  📸 Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                                f"  📝 Description: {self.description[:50] if self.description else 'None'}{'...' if self.description and len(self.description) > 50 else ''}\n\n"
                                f"**👥 Please mention users or provide their IDs:**\n"
                                f"*Format: comma-separated IDs or mentions*\n"
                                f"*Example: 12345, 67890, 54321*",
                        view=None
                    )
                else:
                    await self.message.edit(
                        content=f"👥 **Step 3: User IDs**\n"
                                f"📊 Current Status:\n"
                                f"  📸 Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n\n"
                                f"**👥 Please mention users or provide their IDs:**\n"
                                f"*Format: comma-separated IDs or mentions*\n"
                                f"*Example: 12345, 67890, 54321*",
                        view=None
                    )
            
            # Wait for user IDs
            await self.wait_for_user_ids(interaction, is_existing)
            
        except asyncio.TimeoutError:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for image.**\nThe command has been cancelled.",
                    view=None
                )
        except Exception as e:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Error: {str(e)}**\nThe command has been cancelled.",
                    view=None
                )
    
    async def wait_for_description(self, interaction: discord.Interaction, is_existing=False):
        """Wait for user to provide description only - second step after image."""
        def check(m):
            return m.author.id == self.ctx.author.id and m.channel.id == self.ctx.channel.id
        
        try:
            msg = await self.ctx.bot.wait_for('message', timeout=60.0, check=check)
            
            # Parse description from message content
            content = msg.content.strip()
            
            # DO NOT delete user message - keeping it ensures any attachment URLs remain valid
            if is_existing:
                # When updating existing request, store addition separately
                if content.lower() == 'skip':
                    self.description_addition = None
                elif content:
                    self.description_addition = content
                else:
                    self.description_addition = None
            else:
                # When creating new request, store as full description
                if content.lower() == 'skip':
                    self.description = None
                elif content:
                    self.description = content
                else:
                    self.description = None

            # Show confirmation with summary before proceeding
            await self._show_confirmation(interaction, is_existing)
            
        except asyncio.TimeoutError:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for description.**\nThe command has been cancelled.",
                    view=None
                )
        except Exception as e:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Error: {str(e)}**\nThe command has been cancelled.",
                    view=None
                )

    async def wait_for_user_ids(self, interaction: discord.Interaction, is_existing=False):
        """Wait for user to provide user IDs - sequential flow."""
        def check(m):
            return m.author.id == self.ctx.author.id and m.channel.id == self.ctx.channel.id
        
        try:
            msg = await self.ctx.bot.wait_for('message', timeout=60.0, check=check)
            
            # Parse user IDs
            content = msg.content.strip()
            
            # DO NOT delete user message - keeping it ensures any attachment URLs remain valid
            if content.lower() == 'skip':
                self.user_ids = []
            else:
                # Extract user IDs from mentions or numbers
                user_ids = []
                for part in content.replace(',', ' ').split():
                    part = part.strip()
                    if part.isdigit():
                        user_ids.append(int(part))
                    elif part.startswith('<@') and part.endswith('>'):
                        # Extract ID from mention
                        user_id = part[2:-1]
                        if user_id.startswith('!'):  # Nickname mention
                            user_id = user_id[1:]
                        if user_id.startswith('&'):  # Role mention
                            continue
                        if user_id.isdigit():
                            user_ids.append(int(user_id))
                
                self.user_ids = user_ids

            # Check: each person can only have one active queue entry PER ROUTE TYPE
            # (one loot route + one surge route at the same time is allowed).
            # For new requests on loot_route servers the route type isn't chosen
            # yet at this step — that check runs after the route-type selection
            # in wait_for_route_type instead.
            if self.user_ids:
                if is_existing:
                    duplicate_users = await self._find_duplicate_users(self.route_type)
                    if duplicate_users:
                        await self._show_duplicate_users_error(
                            "Step 3 (Update)", duplicate_users,
                            self.route_type if self.server_mode == "loot_route" else None
                        )
                        return
                elif self.server_mode != "loot_route":
                    duplicate_users = await self._find_duplicate_users()
                    if duplicate_users:
                        await self._show_duplicate_users_error("Step 3", duplicate_users)
                        return

            # Handle next step based on server mode and request type
            if is_existing:
                # For existing requests, go to description step after user IDs
                self.step = 4  # Description step for existing
                if self.message:
                    await self.message.edit(
                        content=f"📝 **Step 4: Description Update**\n"
                                f"📊 Current Status:\n"
                                f"  📋 Queue: #{self.queue_number}\n"
                                f"  👥 Users to add: {len(self.user_ids)} user(s)\n\n"
                                f"**📝 Do you want to add to the description?**\n"
                                f"*Type additional text to append to current description, or 'skip' to keep as is:*",
                        view=None
                    )

                # Wait for description addition
                await self.wait_for_description(interaction, is_existing=True)
            else:
                # For new requests on loot_route servers, ask for route type first
                if self.server_mode == "loot_route" and not self.route_type:
                    await self.wait_for_route_type(interaction)
                elif self.server_mode == "loot_route" and not self.map_type:
                    # Ask for gamemode (loot + surge routes)
                    self.step = 5  # Gamemode step
                    type_label = "⚡ Surge Route" if self.route_type == "surge_route" else "Loot Route"
                    if self.message:
                        await self.message.edit(
                            content=f"🎮 **Step 5: Game Mode**\n"
                                    f"📊 Current Status:\n"
                                    f"• Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                                    f"• Users: {len(self.user_ids)} user(s)\n"
                                    f"• Type: {type_label}\n\n"
                                    f"**Please type the game mode (e.g., Duos, Solos, Trios etc):**",
                            view=None
                        )
                    await self.wait_for_gamemode_after_user_ids(interaction)
                else:
                    # drop_map (no game mode needed)
                    self.step = 4
                    if self.message:
                        await self.message.edit(
                            content=f"📝 **Step 4: Description**\n"
                                    f"📊 Current Status:\n"
                                    f"• Image: {'✅ Uploaded' if self.image_url or self.image_data else '❌ Not provided'}\n"
                                    f"• Users: {len(self.user_ids)} user(s)\n\n"
                                    f"**Please type a description for this map request (or type 'skip'):**",
                            view=None
                        )
                    await self.wait_for_description(interaction, is_existing=False)
                
        except asyncio.TimeoutError:
            if self.message:
                await self.message.edit(
                    content="⏰ **Timed out waiting for user IDs.**\nThe command has been cancelled.",
                    view=None
                )
        except Exception as e:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Error: {str(e)}**\nThe command has been cancelled.",
                    view=None
                )
    
    async def _show_confirmation(self, interaction: discord.Interaction, is_existing: bool):
        """Show confirmation view with summary of collected information."""
        # Build summary based on request type
        if is_existing:
            # For existing requests
            summary_lines = [
                f"📋 **Confirm Update for Queue #{self.queue_number}**",
                "",
                "📊 **Summary:**",
                f"  • **Queue:** #{self.queue_number}",
                f"  • **Users to add:** {len(self.user_ids)} user(s)",
                f"  • **Description addition:** {self.description_addition if self.description_addition is not None else 'None (keep existing)'}",
                f"  • **Image update:** {'✅ New image provided' if self.image_url else '❌ Keep existing image'}",
                "",
                "**Is all the information correct?**",
                "Click ✅ to confirm and update, or ❌ to cancel."
            ]
        else:
            # For new requests
            if self.route_type == "surge_route":
                summary_lines = [
                    f"📋 **Confirm New ⚡ Surge Route Request**",
                    "",
                    "📊 **Summary:**",
                    f"  • **Queue number:** #{self.queue_number}",
                    f"  • **Type:** ⚡ Surge Route",
                    f"  • **Game Mode:** {self.map_type or 'Not set'}",
                    f"  • **Users:** {len(self.user_ids)} user(s)",
                    f"  • **Description:** {self.description[:100] if self.description else 'None'}{'...' if self.description and len(self.description) > 100 else ''}",
                    f"  • **Image:** {'✅ Provided' if self.image_url or self.image_data else '❌ Not provided'}",
                    "",
                    "**Is all the information correct?**",
                    "Click ✅ to confirm and create, or ❌ to cancel."
                ]
            elif self.map_type == "drop_map" or self.server_mode == "drop_map":
                summary_lines = [
                    f"📋 **Confirm New Drop Map Request**",
                    "",
                    "📊 **Summary:**",
                    f"  • **Queue number:** #{self.queue_number}",
                    f"  • **Users:** {len(self.user_ids)} user(s)",
                    f"  • **Description:** {self.description[:100] if self.description else 'None'}{'...' if self.description and len(self.description) > 100 else ''}",
                    f"  • **Image:** {'✅ Provided' if self.image_url or self.image_data else '❌ Not provided'}",
                    "",
                    "**Is all the information correct?**",
                    "Click ✅ to confirm and create, or ❌ to cancel."
                ]
            else:  # loot_route
                summary_lines = [
                    f"📋 **Confirm New 🗺️ Loot Route Request**",
                    "",
                    "📊 **Summary:**",
                    f"  • **Queue number:** #{self.queue_number}",
                    f"  • **Type:** 🗺️ Loot Route",
                    f"  • **Game Mode:** {self.map_type or 'Not set'}",
                    f"  • **Users:** {len(self.user_ids)} user(s)",
                    f"  • **Description:** {self.description[:100] if self.description else 'None'}{'...' if self.description and len(self.description) > 100 else ''}",
                    f"  • **Image:** {'✅ Provided' if self.image_url or self.image_data else '❌ Not provided'}",
                    "",
                    "**Is all the information correct?**",
                    "Click ✅ to confirm and create, or ❌ to cancel."
                ]
        
        summary = "\n".join(summary_lines)
        
        if self.message:
            # Create confirmation view
            view = self.RequestConfirmationView(
                map_request_view=self,
                is_existing=is_existing
            )
            
            await self.message.edit(
                content=summary,
                view=view
            )

    async def _update_existing_request(self, interaction: discord.Interaction):
        """Update an existing map request."""
        existing_request = await database.get_map_request(self.ctx.guild.id, self.queue_number)
        
        if not existing_request:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Queue number #{self.queue_number} doesn't exist!**\nThe command has been cancelled.",
                    view=None
                )
            return
        
        # Merge user IDs
        merged_user_ids = list(set(existing_request.get("user_ids", []) + self.user_ids))
        
        # Calculate which users are new (not already in the existing request)
        existing_user_ids = existing_request.get("user_ids", [])
        new_user_ids = [uid for uid in self.user_ids if uid not in existing_user_ids]
        
        # Get image URL - DO NOT strip query parameters as Discord attachment URLs need them
        image_url = self.image_url or existing_request.get("image_url")
        if image_url and isinstance(image_url, str):
            # Keep the full URL with query parameters - Discord embeds need the full URL
            # Query parameters are required for proper embed display of Discord attachments
            pass
        
        # Build new description by appending addition if provided
        existing_description = existing_request.get("description", "")
        if self.description_addition is not None:
            # User provided additional text to append
            if existing_description:
                # Use "+" sign to separate as requested by user
                new_description = f"{existing_description} + {self.description_addition}"
            else:
                new_description = self.description_addition
        else:
            # No addition provided, keep existing description
            new_description = existing_description
        
        # Clear image data for existing requests only if no new image is being uploaded
        # This prevents the queue display from creating duplicate file attachments
        print(f"DEBUG _update_existing_request: self.image_url={self.image_url}, self.image_data={self.image_data is not None}, self.image_filename={self.image_filename}")
        
        # Only clear image_data and filename if no new image is being uploaded
        # Check if user provided a new image (either via URL or binary data)
        has_new_image = self.image_url or self.image_data
        if not has_new_image:
            # No new image provided, clear image data to prevent duplicate file creation
            self.image_data = None
            self.image_filename = None
            print(f"DEBUG _update_existing_request: Cleared image data for existing request (no new image uploaded)")
        else:
            print(f"DEBUG _update_existing_request: Keeping image data for new image upload")
        
        # Update the request in place (preserves message_id and other fields)
        success = await database.update_map_request(
            guild_id=self.ctx.guild.id,
            queue_number=self.queue_number,
            user_ids=merged_user_ids,
            description=new_description,
            image_url=image_url,
            map_type=self.map_type or existing_request.get("map_type") or existing_request.get("gamemode")
        )
        
        if success:
            # If a new image was uploaded, persist it locally (overwrites the prior
            # copy for this code) so the queue re-attaches the fresh map and it never
            # expires. self.image_data is cleared above when no new image was provided.
            if self.image_data:
                save_queue_image(self.ctx.guild.id, self.queue_number, self.image_data, self.image_filename)
            # Use the canonical DB-driven renderer, which sets every entry's image
            # from its stored image_url. A former per-view renderer (now removed)
            # only attached an image to the *current* entry and dropped (or actively
            # cleared) images on every other entry it had to re-render — that was the
            # "images disappear from the queue when a map is added/updated" bug.
            await MapCommands.update_queue_display(self.ctx.guild)
            # Send queue notification DMs if enabled (only send to NEW users being added)
            if new_user_ids:
                await self._send_queue_notification_dms(new_user_ids, self.queue_number, is_update=True)
            if self.message:
                # Create appropriate success message
                update_parts = []
                if self.user_ids:
                    update_parts.append("user IDs added")
                if self.description_addition is not None:
                    update_parts.append("description appended")
                if self.image_url:
                    update_parts.append("image updated")
                
                if update_parts:
                    update_text = " and ".join(update_parts)
                    message_content = f"✅ {update_text.capitalize()} for Queue #{self.queue_number}"
                else:
                    message_content = f"✅ Queue #{self.queue_number} updated"
                
                # Show confirmation view with tick/X buttons
                confirmation_view = self.QueueCompletionView(self)
                await self.message.edit(
                    content=f"{message_content}\n\n**Confirm completion:**",
                    view=confirmation_view
                )
        else:
            if self.message:
                await self.message.edit(
                    content=f"❌ **Failed to update queue entry #{self.queue_number}.**\n"
                            f"Please try again or contact an administrator.",
                    view=None
                )
    
    async def _create_new_request(self, interaction: discord.Interaction):
        """Create a new map request."""
        for attempt in range(3):
            if attempt > 0:
                # Queue number collision — fetch a fresh one and retry silently
                self.queue_number = await database.get_next_queue_number(self.ctx.guild.id)

            route_type = self.route_type or ("loot_route" if self.server_mode == "loot_route" else "drop_map")
            stored_map_type = self.map_type or ("drop_map" if self.server_mode == "drop_map" else None)

            success = await database.add_map_request(
                self.ctx.guild.id,
                self.queue_number,
                self.image_url,
                self.user_ids,
                self.description,
                stored_map_type,
                route_type
            )

            if success:
                # Persist the uploaded image to local disk so the queue can re-attach
                # it forever — Discord CDN URLs expire ~24h, a local file never does.
                if self.image_data:
                    save_queue_image(self.ctx.guild.id, self.queue_number, self.image_data, self.image_filename)
                # Canonical DB-driven renderer (see note in _update_existing_request):
                # renders every entry's image from the stored image_url instead of the
                # broken per-current-entry path that dropped other members' images.
                await MapCommands.update_queue_display(self.ctx.guild)
                # Send queue notification DMs if enabled
                await self._send_queue_notification_dms(self.user_ids, self.queue_number, is_update=False)
                if self.message:
                    if self.route_type == "surge_route":
                        queue_type = "surge route"
                    elif self.server_mode == "loot_route":
                        queue_type = "loot route"
                    else:
                        queue_type = "drop map"
                    # Show confirmation view with tick/X buttons
                    confirmation_view = self.QueueCompletionView(self)
                    await self.message.edit(
                        content=f"✅ Queue #{self.queue_number} added to {queue_type} queue!\n\n**Confirm completion:**",
                        view=confirmation_view
                    )
                return

        if self.message:
            await self.message.edit(
                content=f"❌ **Failed to add map request.**\n"
                        f"Please try again or contact an administrator.",
                view=None
            )
    
    class QueueNotificationFailureView(discord.ui.View):
        """View with buttons for handling queue notification DM failures."""
        
        def __init__(self, map_request_view, guild_id: int, queue_number: str,
                     failed_user_ids: List[int], timeout: float = 86400.0):  # 24 hour timeout
            super().__init__(timeout=timeout)
            self.map_request_view = map_request_view
            self.guild_id = guild_id
            self.queue_number = queue_number
            self.failed_user_ids = failed_user_ids
        
        @discord.ui.button(label="🔄 Retry Failed DMs", style=discord.ButtonStyle.primary, custom_id="retry_failed_queue_dms")
        async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Retry sending queue notification DMs to failed users."""
            await interaction.response.defer(ephemeral=False)
            
            # Get the guild
            guild = self.map_request_view.ctx.guild if self.map_request_view.ctx.guild else interaction.guild
            
            # Get server configuration
            config = await database.get_server_queue_config(self.guild_id)
            if not config:
                await interaction.followup.send("❌ Server configuration not found.", ephemeral=True)
                return
            
            # Check if queue notifications are enabled
            if not config.get("enable_queue_notifications", 0):
                await interaction.followup.send("❌ Queue notifications are disabled for this server.", ephemeral=True)
                return
            
            # Send retry status
            log_channel = interaction.channel
            retry_msg = await log_channel.send(
                f"🔄 Retrying queue notification DMs for queue **#{self.queue_number}**...\n"
                f"• **Users to retry:** {len(self.failed_user_ids)}"
            )
            
            # Retry sending DMs
            success_count = 0
            new_failed_user_ids = []
            
            for i, user_id in enumerate(self.failed_user_ids):
                try:
                    member = guild.get_member(user_id)
                    if not member:
                        try:
                            member = await guild.fetch_member(user_id)
                        except discord.NotFound:
                            new_failed_user_ids.append(user_id)
                            continue
                    
                    # Get server mode for message formatting
                    server_mode = config.get("server_mode", "drop_map")
                    
                    # Calculate priority for this user
                    priority_level, priority_role, _ = await self.map_request_view.calculate_priority([user_id], server_mode)
                    
                    # Get queue channel
                    queue_channel_id = config.get("queue_channel_id")
                    channel_mention = f"<#{queue_channel_id}>" if queue_channel_id else "Unknown channel"
                    
                    # Determine request type
                    request_type = "drop map" if server_mode == "drop_map" else "loot route"
                    
                    # Build message (similar to original)
                    # Get position in queue
                    all_requests = await database.get_all_map_requests(self.guild_id, status='active')
                    sorted_requests = await get_sorted_map_requests(guild, server_mode)
                    position = None
                    for i, req in enumerate(sorted_requests, 1):
                        if req.get("queue_number") == self.queue_number:
                            position = i
                            break
                    
                    # Build message with priority role name/number (not mention)
                    if position is not None:
                        if position >= 5:
                            message_lines = [
                                f"📋 **Your {request_type} request has been added to the queue!**\n",
                                f"🎯 **Position in queue:** #{position}",
                                f"📍 **Queue message:**  {channel_mention}"
                            ]
                            
                            # Add priority ranking - show role name/number instead of mention
                            if priority_role:
                                # Try to get role name or identifier
                                if hasattr(priority_role, 'name'):
                                    role_display = priority_role.name
                                elif isinstance(priority_role, str):
                                    role_display = priority_role
                                else:
                                    role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                                message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                            else:
                                message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                            
                            # Add ranking system link with fire emoji based on server mode
                            if server_mode == "drop_map":
                                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                            elif server_mode == "loot_route":
                                ranking_system_link = "https://canary.discord.com/channels/971731167621574666/1131190892707979284/1180367272288714772"
                            else:
                                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                            
                            message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                            
                            message = "\n".join(message_lines)
                        else:
                            message_lines = [
                                f"📋 **Your {request_type} request has been added to the queue!**\n",
                                f"🎯 **Position in queue:** #{position}",
                                f"📍 **Queue message:**  "
                            ]
                            
                            if priority_role:
                                if hasattr(priority_role, 'name'):
                                    role_display = priority_role.name
                                elif isinstance(priority_role, str):
                                    role_display = priority_role
                                else:
                                    role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                                message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                            else:
                                message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                            
                            # Add ranking system link with fire emoji based on server mode
                            if server_mode == "drop_map":
                                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                            elif server_mode == "loot_route":
                                ranking_system_link = "https://canary.discord.com/channels/971731167621574666/1131190892707979284/1180367272288714772"
                            else:
                                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                            
                            message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                            
                            message = "\n".join(message_lines)
                    else:
                        # Fallback
                        message_lines = [
                            f"📋 **Your {request_type} request has been added to the queue!**\n",
                            f"📍 **Queue message:**  "
                        ]
                        
                        if priority_role:
                            if hasattr(priority_role, 'name'):
                                role_display = priority_role.name
                            elif isinstance(priority_role, str):
                                role_display = priority_role
                            else:
                                role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                            message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                        else:
                            message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                        
                        # Add ranking system link with fire emoji based on server mode
                        if server_mode == "drop_map":
                            ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                        elif server_mode == "loot_route":
                            ranking_system_link = "https://canary.discord.com/channels/971731167621574666/1131190892707979284/1180367272288714772"
                        else:
                            ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
                        
                        message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                        
                        message = "\n".join(message_lines)
                    
                    await member.send(message)
                    success_count += 1
                    
                    # Rate limiting
                    if i < len(self.failed_user_ids) - 1:
                        await asyncio.sleep(3)
                        
                except discord.Forbidden:
                    # User has DMs disabled
                    new_failed_user_ids.append(user_id)
                except Exception as e:
                    print(f"Error retrying queue notification DM to user {user_id}: {e}")
                    new_failed_user_ids.append(user_id)
            
            # Update retry message
            retry_result_msg = f"✅ Retry completed for queue **#{self.queue_number}**!\n"
            retry_result_msg += f"• **Successful DMs:** {success_count}\n"
            retry_result_msg += f"• **Still failed:** {len(new_failed_user_ids)}"
            
            if len(new_failed_user_ids) > 0:
                retry_result_msg += f"\n\n⚠️ **Some DMs still failed.**"
                retry_result_msg += f"\n• Click **🔄 Retry Failed DMs** to try again"
                retry_result_msg += f"\n• Click **⏭️ Skip User** to exclude these users from future notifications"
            
            await retry_msg.edit(content=retry_result_msg)
            
            # Update the original failure log embed
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                # Update failed users list
                for i, field in enumerate(embed.fields):
                    if field.name.startswith("Failed Users"):
                        if new_failed_user_ids:
                            user_mentions = []
                            for user_id in new_failed_user_ids[:10]:
                                user_mentions.append(f"<@{user_id}>")
                            
                            if len(new_failed_user_ids) > 10:
                                user_mentions.append(f"... and {len(new_failed_user_ids) - 10} more")
                            
                            embed.set_field_at(i,
                                name=f"Failed Users ({len(new_failed_user_ids)} total)",
                                value=", ".join(user_mentions) if user_mentions else "None",
                                inline=False
                            )
                        break
                
                # Update description
                embed.description = f"Queue notification DMs partially failed for queue **#{self.queue_number}** (Retried)"
                
                # Update the message with refreshed view
                self.failed_user_ids = new_failed_user_ids
                await interaction.message.edit(embed=embed, view=self)
            
            # Auto-delete the failure log message if all DMs succeeded
            if len(new_failed_user_ids) == 0:
                # Delete the failure log message since all DMs are now successful
                try:
                    await interaction.message.delete()
                    await interaction.followup.send("✅ All queue notification DMs successfully sent! The failure log has been removed.", ephemeral=True)
                except discord.NotFound:
                    # Message already deleted
                    await interaction.followup.send("✅ All queue notification DMs successfully sent!", ephemeral=True)
                except Exception as e:
                    print(f"Error deleting failure log message: {e}")
                    button.disabled = True
                    button.label = "✅ All DMs Sent"
                    await interaction.message.edit(view=self)
                    await interaction.followup.send("✅ All queue notification DMs successfully sent!", ephemeral=True)
        
        @discord.ui.button(label="⏭️ Skip User", style=discord.ButtonStyle.secondary, custom_id="skip_failed_user")
        async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Skip failed users - don't send them notifications but keep queue entry."""
            await interaction.response.defer(ephemeral=False)
            
            # Update the failure log embed
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                embed.description = f"⏭️ Skipped {len(self.failed_user_ids)} users for queue **#{self.queue_number}**"
                embed.color = discord.Color.light_grey()
                embed.set_footer(text="Users skipped - queue entry remains active")
                
                # Remove buttons
                await interaction.message.edit(embed=embed, view=None)
            
            await interaction.followup.send(f"⏭️ Skipped {len(self.failed_user_ids)} users for queue **#{self.queue_number}**. Queue entry remains active.")

    class QueueCompletionView(discord.ui.View):
        """View with tick/X buttons for confirming completion and deleting the queue message."""
        
        def __init__(self, map_request_view, timeout: float = 180.0):  # 3 minute timeout
            super().__init__(timeout=timeout)
            self.map_request_view = map_request_view
        
        @discord.ui.button(label="✅ Confirm & Delete", style=discord.ButtonStyle.success, custom_id="confirm_delete")
        async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Confirm completion and delete the message in the channel."""
            await interaction.response.defer(ephemeral=False)
            
            # Delete the message in the channel
            if self.map_request_view.message:
                try:
                    await self.map_request_view.message.delete()
                    await interaction.followup.send("✅ Message in channel deleted.", ephemeral=True)
                except discord.NotFound:
                    await interaction.followup.send("⚠️ Message already deleted.", ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send("❌ No permission to delete message.", ephemeral=True)
                except Exception as e:
                    await interaction.followup.send(f"❌ Error deleting message: {e}", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ No message to delete.", ephemeral=True)
            
            # Disable buttons
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
        
        @discord.ui.button(label="❌ Keep Message", style=discord.ButtonStyle.danger, custom_id="keep_message")
        async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Keep the message visible."""
            await interaction.response.defer(ephemeral=False)
            
            # Update message to show it will be kept
            if interaction.message:
                await interaction.message.edit(content=f"{interaction.message.content}\n\n📝 **Message kept - it will auto-delete in 5 minutes.**", view=None)
            
            await interaction.followup.send("📝 Message will be kept (auto-deletes in 5 minutes).", ephemeral=True)
            
            # Schedule auto-delete after 5 minutes
            if self.map_request_view.message:
                async def auto_delete():
                    await asyncio.sleep(300)  # 5 minutes
                    try:
                        await self.map_request_view.message.delete()
                    except:
                        pass  # Message already deleted or inaccessible
                
                asyncio.create_task(auto_delete())

    class RequestConfirmationView(discord.ui.View):
        """View with confirmation buttons (tick/X) for final step of map request creation/update."""
        
        def __init__(self, map_request_view, is_existing: bool, timeout: float = 180.0):
            super().__init__(timeout=timeout)
            self.map_request_view = map_request_view
            self.is_existing = is_existing
        
        @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, custom_id="confirm_request")
        async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Confirm and proceed with request creation/update."""
            await interaction.response.defer(ephemeral=False)
            
            # Update message to show processing
            if self.map_request_view.message:
                await self.map_request_view.message.edit(
                    content="⏳ **Processing your request...**",
                    view=None
                )
            
            # Call the appropriate method based on request type
            if self.is_existing:
                await self.map_request_view._update_existing_request(interaction)
            else:
                await self.map_request_view._create_new_request(interaction)
        
        @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger, custom_id="cancel_request")
        async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Cancel the request creation/update."""
            await interaction.response.defer(ephemeral=False)
            
            if self.map_request_view.message:
                await self.map_request_view.message.edit(
                    content="❌ **Request cancelled.**",
                    view=None
                )

    async def _send_queue_notification_dms(self, user_ids: List[int], queue_number: str, is_update: bool = False):
        """Send DM notifications to users when their request is added/updated in queue."""
        # Check if notifications are enabled for this server
        config = await database.get_server_queue_config(self.ctx.guild.id)
        if not config:
            return
        
        # Check if notifications are enabled
        if not config.get("enable_queue_notifications", 0):
            return
        
        # Get server mode to determine message type
        server_mode = config.get("server_mode", "drop_map")
        
        # Get queue channel to construct message link
        queue_channel_id = config.get("queue_channel_id")
        if not queue_channel_id:
            return
        
        # Get all active map requests to calculate position
        all_requests = await database.get_all_map_requests(self.ctx.guild.id, status='active')
        
        # Sort by priority to get display position
        sorted_requests = await get_sorted_map_requests(self.ctx.guild, server_mode)
        
        # Find position of this queue number
        position = None
        for i, req in enumerate(sorted_requests, 1):
            if req.get("queue_number") == queue_number:
                position = i
                break
        
        # Get queue message link (we need the message ID)
        # Try to fetch the map request to get message_id
        channel_mention = f"<#{queue_channel_id}>"  # fallback
        map_request = await database.get_map_request(self.ctx.guild.id, queue_number)
        if map_request and map_request.get("message_id"):
            message_id = map_request["message_id"]
            message_link = f"https://discord.com/channels/{self.ctx.guild.id}/{queue_channel_id}/{message_id}"
            channel_mention = f"[Jump to queue message]({message_link})"
        # else keep channel mention
        
        # Determine request type based on server mode
        if server_mode == "drop_map":
            request_type = "drop map"
            ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
        elif server_mode == "loot_route":
            request_type = "loot route"
            ranking_system_link = "https://canary.discord.com/channels/971731167621574666/1131190892707979284/1180367272288714772"
        else:
            request_type = "map"
            ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
        
        # Send DMs with rate limiting (similar to DM processor)
        success_count = 0
        failed_user_ids = []
        
        for i, user_id in enumerate(user_ids):
            try:
                member = self.ctx.guild.get_member(user_id)
                if not member:
                    # Try to fetch member
                    try:
                        member = await self.ctx.guild.fetch_member(user_id)
                    except discord.NotFound:
                        failed_user_ids.append(user_id)
                        continue
                
                # Calculate priority for this specific user
                priority_level, priority_role, _ = await self.calculate_priority([user_id], server_mode)
                
                # Prepare message based on position and server mode
                if position is not None:
                    if position >= 5:
                        # Build message with priority role info
                        message_lines = [
                            f"📋 **Your {request_type} request has been added to the queue!**\n",
                            f"🎯 **Position in queue:** #{position}",
                            f"📍 **Queue message:** {channel_mention}"
                        ]
                        
                        # Add priority ranking - show role name/number instead of mention
                        if priority_role:
                            # Try to get role name or identifier
                            if hasattr(priority_role, 'name'):
                                role_display = priority_role.name
                            elif isinstance(priority_role, str):
                                role_display = priority_role
                            else:
                                role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                            message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                        else:
                            message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                        
                        # Add ranking system link with fire emoji
                        message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                        
                        message = "\n".join(message_lines)
                    else:
                        # Build message for position < 5
                        message_lines = [
                            f"📋 **Your {request_type} request has been added to the queue!**\n",
                            f"🎯 **Position in queue:** #{position}",
                            f"📍 **Queue message:** {channel_mention}"
                        ]
                        
                        # Add priority ranking - show role name/number instead of mention
                        if priority_role:
                            if hasattr(priority_role, 'name'):
                                role_display = priority_role.name
                            elif isinstance(priority_role, str):
                                role_display = priority_role
                            else:
                                role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                            message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                        else:
                            message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                        
                        # Add ranking system link with fire emoji
                        message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                        
                        message = "\n".join(message_lines)
                else:
                    # Fallback if position not found
                    message_lines = [
                        f"📋 **Your {request_type} request has been added to the queue!**\n",
                        f"📍 **Queue message:** {channel_mention}"
                    ]
                    
                    # Add priority ranking - show role name/number instead of mention
                    if priority_role:
                        if hasattr(priority_role, 'name'):
                            role_display = priority_role.name
                        elif isinstance(priority_role, str):
                            role_display = priority_role
                        else:
                            role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                        message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
                    else:
                        message_lines.append(f"🏆 **Current priority ranking:** No priority role")
                    
                    # Add ranking system link with fire emoji
                    message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
                    
                    message = "\n".join(message_lines)
                
                await member.send(message)
                success_count += 1
                
                # Rate limiting: 3 second pause between each DM
                if i < len(user_ids) - 1:
                    await asyncio.sleep(3)
                    
            except discord.Forbidden:
                # User has DMs disabled
                failed_user_ids.append(user_id)
            except Exception as e:
                print(f"Error sending queue notification DM to user {user_id}: {e}")
                failed_user_ids.append(user_id)
        
        # Log results
        if success_count > 0:
            print(f"Sent queue notifications to {success_count} user(s) for queue #{queue_number}")
        if failed_user_ids:
            print(f"Failed to send queue notifications to {len(failed_user_ids)} user(s) for queue #{queue_number}")
    

class MapCommands(commands.Cog):
    """Map request queue commands."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @staticmethod
    async def calculate_priority_for_request(guild, server_mode, user_ids):
        """Calculate the highest priority among users based on their roles (static method)."""
        if not user_ids:
            return None, None, None

        # Priority mappings based on server mode
        if server_mode == "drop_map":
            # Drop map server priority order (1st is highest)
            priority_order = [
                (1, ["Paid Priority"]),
                (2, ["Wave Contributor"]),
                (3, ["Unreal (LVL 50)"]),
                (4, ["Elite (LVL 30)", "Active"]),
                (5, ["Silver (LVL 10)", "Staff", "Drop Map Tester", "Map Creator",
                      "Loot Route Map Creator", "Tips and Tricks Helper", "Promoters", "Drop Map Reviewer"])
            ]
        else:  # loot_route
            # Loot route server priority order (1st is highest)
            priority_order = [
                (1, ["Server Booster", "Wave Contributor"]),
                (2, ["Battle Pass Supporter"]),
                (3, ["Unreal (LVL 50)"]),
                (4, ["Elite (LVL 30)", "Active"]),
                (5, ["Bronze (LVL 5)", "Staff", "Drop Map Tester", "Map Creator",
                      "Loot Route Map Creator", "Tips and Tricks Helper", "Promoters", "Drop Map Reviewer"]),
                (6, ["Access", "Access Invite way"])
            ]

        highest_priority_level = None
        highest_priority_role = None
        highest_priority_user = None
        
        for user_id in user_ids:
            try:
                # Fetch the member from the guild
                member = guild.get_member(user_id)
                if not member:
                    # Try to fetch if not in cache
                    try:
                        member = await guild.fetch_member(user_id)
                    except:
                        continue
                
                # Check each role in priority order
                for level, roles in priority_order:
                    for role_name in roles:
                        # Case-sensitive role search
                        role = discord.utils.get(member.roles, name=role_name)
                        if role:
                            # Found a role at this priority level
                            if highest_priority_level is None or level < highest_priority_level:
                                highest_priority_level = level
                                highest_priority_role = role  # Store role object instead of name
                                highest_priority_user = user_id
                            break  # No need to check other roles at same level
                    
                    # If we found a role at this level, don't check lower levels for this user
                    if highest_priority_level == level:
                        break
            
            except Exception as e:
                print(f"Error checking priority for user {user_id}: {e}")
                continue
        
        return highest_priority_level, highest_priority_role, highest_priority_user
    
    @staticmethod
    async def update_queue_display(guild, create_missing: bool = True):
        """
        Update the queue display for a guild (static method).

        create_missing=True  (default): also post new Discord messages for any
            queue entry that has no stored message_id or whose stored message
            was deleted.  Use this when a new map is genuinely added.

        create_missing=False: edit-only mode — only touches messages that
            already exist in Discord.  Entries whose messages are gone are
            silently skipped.  Safe to call on startup / refresh so the
            Management bot never sees "new" messages for existing queue entries.
        """
        try:
            config = await database.get_server_queue_config(guild.id)
            if not config or not config["queue_channel_id"]:
                return False
            
            channel = guild.get_channel(config["queue_channel_id"])
            if not channel:
                return False
            
            # Get all map requests sorted by priority (highest priority first)
            server_mode = config.get("server_mode", "drop_map")
            sorted_requests = await get_sorted_map_requests(guild, server_mode)
            
            # Process each request individually with priority-based display numbers
            for i, req in enumerate(sorted_requests, 1):
                try:
                    display_number = i  # Priority-based display number (1 = highest priority)
                    
                    # Format timestamp nicely
                    from datetime import datetime, timedelta
                    try:
                        created_at = datetime.fromisoformat(req["created_at"])
                        # Format as "Today at HH:MM PM" or "Yesterday at HH:MM PM" or "MM/DD at HH:MM PM"
                        now = datetime.now()
                        if created_at.date() == now.date():
                            time_str = created_at.strftime("Today at %I:%M %p")
                        # timedelta, not day=now.day-1: replace() raises
                        # ValueError on the 1st of every month (day=0).
                        elif created_at.date() == (now - timedelta(days=1)).date():
                            time_str = created_at.strftime("Yesterday at %I:%M %p")
                        else:
                            time_str = created_at.strftime("%m/%d at %I:%M %p")
                    except (ValueError, KeyError):
                        time_str = "Unknown time"

                    # Determine route type for this entry
                    req_route_type = req.get("route_type", "loot_route")

                    # Create embed for this queue entry with appealing format
                    if req_route_type == "surge_route":
                        embed = discord.Embed(
                            title=f"⚡ SURGE ROUTE REQUEST #{display_number} (Queue)",
                            color=discord.Color.orange()
                        )
                    elif server_mode == "loot_route":
                        embed = discord.Embed(
                            title=f"🗺️ LOOT ROUTE REQUEST #{display_number} (Queue)",
                            color=discord.Color.blue()
                        )
                    else:
                        embed = discord.Embed(
                            title=f"🗺️ MAP REQUEST NUMBER #{display_number} (Queue)",
                            color=discord.Color.blue()
                        )

                    # Handle description - will be added to content_parts later
                    description = req.get("description", "")
                    description_field = ""
                    if description:
                        # Add emoji prefix "📝 "
                        description_field = f"📝 **Description:** {description}"
                        # Check length for field value limit (1024 chars)
                        if len(description_field) > 1024:
                            description_field = description_field[:1020] + "..."
                    
                    # Add user info with @mentions and emoji
                    user_ids = req.get("user_ids", [])
                    user_count = len(user_ids)
                    
                    # Create mention strings
                    if user_ids:
                        mention_text = ", ".join([f"<@{uid}>" for uid in user_ids])
                        if len(mention_text) > 1024:  # Discord field value limit
                            mention_text = f"{user_count} users (list too long to display)"
                        user_field = f"👥 **Requested by:** {mention_text}"
                    else:
                        user_field = "👥 **Requested by:** No users specified"
                    
                    # Add game mode/type information
                    map_type = req.get("map_type") or req.get("gamemode")
                    map_type_display = ""
                    if map_type:
                        # Format the map type for display
                        if map_type == "drop_map":
                            map_type_display = "Drop Map"
                        elif map_type == "loot_route":
                            map_type_display = "Loot Route"
                        else:
                            map_type_display = map_type.replace("_", " ").title()
                    
                    # Calculate and add priority information
                    priority_text = ""
                    if user_ids:
                        priority_level, priority_role, priority_user = await MapCommands.calculate_priority_for_request(
                            guild, server_mode, user_ids
                        )
                        
                        if priority_level and priority_role:
                            # Convert level to ordinal (1st, 2nd, 3rd, etc.)
                            if priority_level == 1:
                                level_text = "1st"
                            elif priority_level == 2:
                                level_text = "2nd"
                            elif priority_level == 3:
                                level_text = "3rd"
                            else:
                                level_text = f"{priority_level}th"
                            
                            priority_text = f"⭐ **Priority:** {level_text} Level Priority Ranking\n"
                            # Format: Bullet point with only role mention in parentheses
                            if hasattr(priority_role, 'id'):
                                priority_text += f"- (<@&{priority_role.id}>)"
                            else:
                                priority_text += f"- ({priority_role})"
                        else:
                            priority_text = "⭐ **Priority:** No priority roles found"
                    else:
                        priority_text = "⭐ **Priority:** No users specified"
                    
                    # Build the main content with emojis and formatting
                    content_parts = []

                    # Add user field (Requested by)
                    content_parts.append(user_field)

                    # Add priority
                    content_parts.append(priority_text)

                    # For loot_route servers: show type label then game mode for both route types
                    if server_mode == "loot_route":
                        if req_route_type == "surge_route":
                            content_parts.append("📌 **Type:** ⚡ Surge Route")
                        else:
                            content_parts.append("📌 **Type:** 🗺️ Loot Route")
                        if map_type_display:
                            content_parts.append(f"🎮 **Game Mode:** {map_type_display}")

                    # Add description if available
                    if description_field:
                        content_parts.append(description_field)
                    
                    # Join all parts with double newlines for spacing
                    main_content = "\n\n".join(content_parts)
                    
                    # Check if main content exceeds field value limit (1024 chars)
                    if len(main_content) > 1024:
                        # Split into multiple fields or handle continuation
                        # For now, truncate with continuation indicator
                        main_content = main_content[:1020] + "..."
                        # We could implement proper splitting here if needed
                    
                    embed.add_field(
                        name="\u200b",  # Zero-width space for field name
                        value=main_content,
                        inline=False
                    )
                    
                    # Add timestamp and queue code to footer (handle both integer and alphabetical codes)
                    queue_code = req['queue_number']
                    # If queue_code is an integer, convert to alphabetical
                    if isinstance(queue_code, int) or (isinstance(queue_code, str) and queue_code.isdigit()):
                        try:
                            queue_code = number_to_alpha(int(queue_code))
                        except:
                            pass  # Keep as-is if conversion fails
                    
                    # Format footer as requested: "Queue #3 (Code: e) • Created•Today at 8:19 pm"
                    # Note: Using middle dot (•) as separator
                    footer_text = f"Queue #{display_number} (Code: {queue_code}) • Created•{time_str}"
                    embed.set_footer(text=footer_text)
                    
                    # Add image. Prefer a locally-saved copy re-attached to the
                    # message (never expires); fall back to the stored URL for legacy
                    # entries that have no local file yet.
                    local_file_path = find_queue_image(guild.id, req["queue_number"])
                    local_attach_name = None
                    if local_file_path:
                        _ext = os.path.splitext(local_file_path)[1] or ".png"
                        local_attach_name = f"queue_{req['queue_number']}{_ext}"
                        embed.set_image(url=f"attachment://{local_attach_name}")
                    else:
                        image_url = req.get("image_url")
                        if image_url and image_url != "None" and image_url != "null" and image_url != "":
                            if isinstance(image_url, str):
                                image_url = image_url.strip()
                                # DO NOT strip query parameters - Discord attachment URLs need them.
                                if image_url.startswith(('http://', 'https://')):
                                    embed.set_image(url=image_url)
                                elif image_url.startswith('//'):
                                    embed.set_image(url=f"https:{image_url}")
                                else:
                                    embed.set_image(url=f"https://{image_url}")
                    
                    # Set timestamp for embed
                    try:
                        embed.timestamp = datetime.fromisoformat(req["created_at"])
                    except (ValueError, KeyError):
                        embed.timestamp = datetime.now()
                    
                    # Set footer
                    embed.set_footer(text=f"Queue #{display_number} • Code: {req['queue_number']}")
                    
                    # Check if we have an existing message for this queue number
                    message_id = req.get("message_id")
                    
                    # Validate message_id - it should be a non-empty string that can be converted to int
                    has_valid_message_id = False
                    valid_message_id = None
                    
                    if message_id:
                        # Check if it's a string that can be converted to int (valid Discord message ID)
                        if isinstance(message_id, str) and message_id.strip():
                            # Check for common invalid values
                            lower_msg = message_id.lower().strip()
                            if lower_msg not in ['none', 'null', '0', '']:
                                try:
                                    # Try to convert to int to validate it's a valid message ID
                                    valid_message_id = int(message_id)
                                    has_valid_message_id = True
                                except (ValueError, TypeError):
                                    pass
                        elif isinstance(message_id, int) and message_id > 0:
                            valid_message_id = message_id
                            has_valid_message_id = True
                    
                    if has_valid_message_id:
                        try:
                            # Try to update existing message
                            message = await channel.fetch_message(valid_message_id)
                            has_existing_attachments = len(message.attachments) > 0

                            # Durable local image: if we have a saved local copy but this
                            # message has no attachment yet (legacy entry whose CDN URL has
                            # since expired), recreate it so the image lives ON the message
                            # as an attachment, which never expires. Falling through here
                            # (no `continue`) drops to the create-new block below, which
                            # re-posts this entry with the local file attached.
                            if local_file_path and not has_existing_attachments and create_missing:
                                # Only do the delete+recreate upgrade in full mode.
                                # In edit-only mode, just re-use whatever image is on the embed.
                                await message.delete()
                            elif local_file_path and not has_existing_attachments and not create_missing:
                                # Edit-only mode: keep the existing message, just update the embed.
                                if message.embeds and message.embeds[0].image and message.embeds[0].image.url:
                                    embed.set_image(url=message.embeds[0].image.url)
                                await message.edit(embed=embed)
                                await database.update_map_request_message(
                                    guild.id, req["queue_number"], str(message.id)
                                )
                                await asyncio.sleep(1.2)
                                continue
                            else:
                                # Message already carries its image. Re-point the embed at
                                # the FRESH attachment URL (Discord re-signs it on every
                                # fetch) and just edit the header/position — no re-upload.
                                if has_existing_attachments and message.attachments[0].url:
                                    embed.set_image(url=message.attachments[0].url)
                                elif (message.embeds and message.embeds[0].image
                                      and message.embeds[0].image.url):
                                    embed.set_image(url=message.embeds[0].image.url)
                                await message.edit(embed=embed)
                                await database.update_map_request_message(
                                    guild.id,
                                    req["queue_number"],
                                    str(message.id)
                                )
                                await asyncio.sleep(1.2)
                                continue
                        except discord.NotFound:
                            if not create_missing:
                                # Edit-only mode: stale message_id — skip silently.
                                print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Message {valid_message_id} not found; skipping (edit-only mode)")
                                continue
                            print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Message {valid_message_id} not found, creating new")
                            pass
                        except discord.Forbidden as e:
                            # Can't access message due to permissions
                            print(f"WARNING: Queue #{req['queue_number']} (Display #{display_number}) - Cannot access message {valid_message_id} (Forbidden): {e}")
                            # Skip this request to avoid creating duplicate
                            continue
                        except discord.HTTPException as e:
                            # Check if it's a rate limit error (429)
                            if e.status == 429:
                                # Rate limited - wait and retry with exponential backoff
                                retry_after = e.retry_after if hasattr(e, 'retry_after') else 8
                                print(f"RATE LIMIT: Queue #{req['queue_number']} (Display #{display_number}) - Rate limited, waiting {retry_after} seconds (with 2 second buffer)...")
                                # Wait longer than retry_after to be safe, plus add jitter
                                import random
                                jitter = random.uniform(0.5, 2.5)
                                await asyncio.sleep(retry_after + 2 + jitter)  # Add 2 second buffer and jitter
                                
                                # Try again once with better error handling
                                try:
                                    message = await channel.fetch_message(valid_message_id)
                                    
                                    # Check if the existing message has attachments
                                    has_existing_attachments = len(message.attachments) > 0
                                    
                                    # Extract image URL from existing attachments if available
                                    existing_attachment_url = None
                                    if has_existing_attachments:
                                        # Try to get image URL from first attachment
                                        attachment = message.attachments[0]
                                        if attachment.url:
                                            existing_attachment_url = attachment.url
                                            # DO NOT strip query parameters - Discord attachment URLs need query parameters
                                            # for proper embed display
                                            print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Found existing attachment URL in retry: {existing_attachment_url[:80]}...")
                                    
                                    if has_existing_attachments:
                                        # Existing message has attachments
                                        # Always use the existing attachment URL for the embed image to avoid duplicate images
                                        if existing_attachment_url:
                                            # Override any previously set embed image with the existing attachment URL
                                            embed.set_image(url=existing_attachment_url)
                                            print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Using existing attachment as embed image in retry")
                                        else:
                                            # No attachment URL found, clear any embed image to avoid mismatch
                                            embed.set_image(url=None)
                                            print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Clearing embed image in retry (no attachment URL)")
                                    
                                    await message.edit(embed=embed)
                                    await asyncio.sleep(1.2)
                                    await database.update_map_request_message(
                                        guild.id,
                                        req["queue_number"],
                                        str(message.id)
                                    )
                                    continue
                                except discord.HTTPException as retry_error:
                                    if retry_error.status == 429:
                                        print(f"CRITICAL RATE LIMIT: Queue #{req['queue_number']} (Display #{display_number}) - Still rate limited after retry, skipping to avoid further limits")
                                        # Skip this request to avoid hitting more rate limits
                                        continue
                                    else:
                                        print(f"WARNING: Queue #{req['queue_number']} (Display #{display_number}) - Retry failed with HTTP error: {retry_error}")
                                        # Continue to create new message
                                        pass
                                except Exception as retry_error:
                                    print(f"WARNING: Queue #{req['queue_number']} (Display #{display_number}) - Retry failed: {retry_error}")
                                    # Continue to create new message
                                    pass
                            else:
                                # Other HTTP error
                                print(f"WARNING: Queue #{req['queue_number']} (Display #{display_number}) - HTTP error accessing message {valid_message_id}: {e}")
                                # Skip this request to avoid creating duplicate
                                continue
                    
                    # Create new message for this queue entry — only when permitted.
                    if not create_missing:
                        print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - skipping new-post (edit-only mode)")
                        continue

                    # Attach the durable local image so the picture lives on the
                    # message and never expires; fall back to embed-only otherwise.
                    if local_file_path:
                        try:
                            message = await channel.send(
                                embed=embed,
                                file=discord.File(local_file_path, filename=local_attach_name)
                            )
                        except (FileNotFoundError, OSError):
                            message = await channel.send(embed=embed)
                    else:
                        message = await channel.send(embed=embed)

                    # Store the message ID in the database
                    await database.update_map_request_message(
                        guild.id,
                        req["queue_number"],
                        str(message.id)
                    )
                    
                except Exception as e:
                    print(f"Error processing queue #{req.get('queue_number', 'unknown')} (Display #{i}): {e}")
                    # Log to file if logging is configured
                    try:
                        import logging
                        logging.error(f"Error processing queue #{req.get('queue_number', 'unknown')} (Display #{i}): {e}")
                    except:
                        pass
                
                # Add delay between processing queue entries to avoid rate limiting
                # Discord has strict limits for message edits: ~5 requests per 5 seconds per channel
                # We use 3.5 seconds between requests with jitter to stay well within limits
                # This ensures we don't hit rate limits when updating many queue entries
                if i < len(sorted_requests):  # Don't delay after the last one
                    import random
                    jitter = random.uniform(0.1, 0.5)  # Add 0.1-0.5 seconds of jitter
                    delay = 3.5 + jitter  # 3.6-4.0 second delay between requests
                    print(f"DEBUG: Queue #{req['queue_number']} (Display #{display_number}) - Waiting {delay:.1f} seconds before next update to avoid rate limits")
                    await asyncio.sleep(delay)

            # Refresh sticky message after all queue updates
            await refresh_sticky_message(channel, guild)

            return True
        
        except Exception as e:
            print(f"Error in update_queue_display: {e}")
            return False
    
    def has_map_request_helper_role():
        """Check if user has map request helper role."""
        async def predicate(ctx):
            # Allow the Wave Management Bot to run automated commands (e.g. the
            # drop-map voting winner auto-running `-z addmap`). It's a bot, so it
            # can't hold the helper role normally — allowlist it by user ID.
            if ctx.author.id == WAVE_MANAGEMENT_BOT_ID:
                return True
            # Search for role with "map request helper" in name (case-insensitive)
            target_role_name = "map request helper"
            
            # First try exact match (case-insensitive)
            role = discord.utils.find(lambda r: r.name.lower() == target_role_name.lower(), ctx.guild.roles)
            
            # If not found, try partial match
            if not role:
                role = discord.utils.find(lambda r: target_role_name.lower() in r.name.lower(), ctx.guild.roles)
            
            # If still not found, try common variations
            if not role:
                variations = ["map helper", "request helper", "map request"]
                for variation in variations:
                    role = discord.utils.find(lambda r: variation.lower() in r.name.lower(), ctx.guild.roles)
                    if role:
                        break
            
            if role and role in ctx.author.roles:
                return True
            
            # Also check for administrator permission
            if ctx.author.guild_permissions.administrator:
                return True
                
            # Provide helpful error message
            if role:
                await ctx.send(f"❌ You need the '{role.name}' role to use this command.")
            else:
                await ctx.send("❌ You need the 'map request helper' role to use this command. (Role not found on server)")
            return False
        return commands.check(predicate)
    
    @commands.command(name='addmap')
    @has_map_request_helper_role()
    async def addmap(self, ctx, *, args=None):
        """
        Add a map to the queue.

        Only users with the 'map request helper' role can use this command.

        Interactive mode (manual):
          >addmap
          Then select options via dropdown

        Automated mode (for bots like Wave Management Bot):
          >addmap new --spot-name "Fort Terry" --image <url> --users <id1> <id2> --description "So good"

        Parameters:
          new              - Create a new queue entry
          --spot-name      - Map/spot name (required for new)
          --image          - Image URL (required for new)
          --users          - Space-separated user IDs or mentions (required for new)
          --description    - Map description (required for new)
        """
        try:
            # Check if automated mode (parameters provided)
            if args and args.lower().startswith('new'):
                print(f"[addmap] Automated mode triggered with args: {args}")
                await self._addmap_automated(ctx, args)
            else:
                # Interactive mode
                print(f"[addmap] Interactive mode triggered")
                await self._addmap_interactive(ctx)
        except Exception as e:
            print(f"[addmap] ERROR: {e}")
            import traceback
            traceback.print_exc()
            await ctx.reply(f"❌ Error in addmap command: {e}", mention_author=False)

    async def _addmap_interactive(self, ctx):
        """Interactive mode - user selects options via dropdown."""
        view = MapRequestView(ctx)

        # Create initial dropdown options
        options = [
            discord.SelectOption(label="Existing queue number", value="existing",
                               description="Add to an existing queue entry"),
            discord.SelectOption(label="New request", value="new",
                               description="Create a new queue entry")
        ]

        dropdown = discord.ui.Select(
            placeholder="❓ Is this an existing queue number or a new request?",
            options=options,
            min_values=1,
            max_values=1
        )
        dropdown.callback = view.handle_dropdown
        view.add_item(dropdown)

        # Send the initial message and store it in the view
        message = await ctx.send(
            "📋 **Map Request Helper**\n"
            "❓ Is this an existing queue number or a new request?",
            view=view
        )
        view.message = message  # Store the message for later editing

    async def _addmap_automated(self, ctx, args):
        """Automated mode - accepts all parameters for bot-to-bot communication."""
        import re

        print(f"[_addmap_automated] Starting with args: {args}")

        # Parse arguments: new --spot-name "name" --image url --users id1 id2 --description "desc"
        spot_name_match = re.search(r'--spot-name\s+"([^"]+)"', args)
        image_match = re.search(r'--image\s+(\S+)', args)
        users_match = re.search(r'--users\s+(.+?)(?=--description|\s*$)', args)
        desc_match = re.search(r'--description\s+"([^"]+)"', args)

        spot_name = spot_name_match.group(1) if spot_name_match else None
        image_url = image_match.group(1) if image_match else None
        description = desc_match.group(1) if desc_match else None

        # Bot-to-bot automation (e.g. the Wave Management Bot voting winner)
        # attaches the image file directly instead of passing a hosted URL.
        # Prefer the uploaded attachment's CDN URL — in that case the --image arg
        # is a local file path on the other bot's machine, which is NOT a valid
        # URL and makes Discord reject the queue embed (error 50035).
        if ctx.message.attachments:
            image_url = ctx.message.attachments[0].url
            print(f"[_addmap_automated] Using attached image URL: {image_url}")

        print(f"[_addmap_automated] Parsed - name: {spot_name}, image: {image_url}, desc: {description}")

        # Parse user IDs/mentions
        user_ids = []
        if users_match:
            users_str = users_match.group(1).strip()
            print(f"[_addmap_automated] Users string: {users_str}")
            # Parse mentions like <@123> or raw IDs
            for item in users_str.split():
                item = item.strip()
                if item.startswith('<@') and item.endswith('>'):
                    uid = int(item[2:-1])
                    user_ids.append(uid)
                elif item.isdigit():
                    user_ids.append(int(item))

        print(f"[_addmap_automated] User IDs: {user_ids}")

        # Validate required fields
        if not all([spot_name, image_url, user_ids, description]):
            missing = [f for f, v in [('spot-name', spot_name), ('image', image_url), ('users', user_ids), ('description', description)] if not v]
            print(f"[_addmap_automated] VALIDATION FAILED - Missing: {missing}")
            await ctx.reply(
                "❌ **Automated mode requires all parameters:**\n"
                "`-z addmap new --spot-name \"Name\" --image URL --users ID1 ID2 --description \"Desc\"`\n\n"
                f"Missing: {', '.join(missing)}",
                mention_author=False
            )
            return

        # Create the map request directly in database
        try:
            print(f"[_addmap_automated] Getting queue number for guild {ctx.guild.id}")
            queue_number = await database.get_next_queue_number(ctx.guild.id)
            print(f"[_addmap_automated] Queue number: {queue_number}")

            config = await database.get_server_queue_config(ctx.guild.id)
            server_mode = config.get("server_mode", "drop_map") if config else "drop_map"
            print(f"[_addmap_automated] Server mode: {server_mode}")

            # Combine spot_name with description for storage
            full_description = description
            if spot_name and description:
                full_description = f"**{spot_name}**: {description}"
            elif spot_name:
                full_description = spot_name

            # Add to database (using positional args to match function signature)
            print(f"[_addmap_automated] Adding map to database...")
            success = await database.add_map_request(
                ctx.guild.id,
                queue_number,
                image_url,
                user_ids,
                full_description,
                server_mode
            )
            if not success:
                await ctx.reply(f"❌ Failed to add map to database", mention_author=False)
                return
            print(f"[_addmap_automated] Map added successfully!")

            # Update queue display
            print(f"[_addmap_automated] Updating queue display...")
            await self.update_queue_display(ctx.guild)
            print(f"[_addmap_automated] Queue display updated!")

            success_msg = (
                f"✅ **Map added to queue!**\n"
                f"Queue #{queue_number}: **{spot_name}**\n"
                f"👥 Users: {len(user_ids)}\n"
                f"📝 Description: {description}"
            )
            print(f"[_addmap_automated] Sending success reply")
            await ctx.reply(success_msg, mention_author=False)
            print(f"[_addmap_automated] Success reply sent!")
        except Exception as e:
            print(f"[_addmap_automated] EXCEPTION: {e}")
            import traceback
            traceback.print_exc()
            await ctx.reply(f"❌ Failed to add map: {e}", mention_author=False)
    
    @commands.command(name='refreshqueue')
    @commands.has_permissions(administrator=True)
    async def refreshqueue(self, ctx, *, flags: str = ""):
        """
        Refresh the queue display channel from the database.

        Usage:
          -z refreshqueue              (edit existing messages + re-post any missing)
          -z refreshqueue --edit-only  (safe mode: only edit existing messages,
                                        never create new posts — use after restart
                                        to avoid the Management bot treating
                                        re-posted entries as new submissions)
        """
        # If the Management bot is sending this command (e.g. on Logistics bot
        # restart), default to edit-only so it never floods the queue channel
        # with new messages that the Management bot would treat as new submissions.
        management_bot_caller = ctx.author.id == WAVE_MANAGEMENT_BOT_ID
        edit_only = "--edit-only" in flags.lower() or management_bot_caller
        create_missing = not edit_only
        requests = await database.get_all_map_requests(ctx.guild.id)
        ok = await self.update_queue_display(ctx.guild, create_missing=create_missing)
        if not ok:
            await ctx.reply("❌ Queue display channel is not configured or not reachable.", mention_author=False)
            return
        loot = sum(1 for r in requests if (r.get("route_type") or "loot_route") == "loot_route")
        surge = sum(1 for r in requests if r.get("route_type") == "surge_route")
        breakdown = f" (🗺️ {loot} loot · ⚡ {surge} surge)" if surge else ""
        mode_note = " *(edit-only — no new posts created)*" if edit_only else ""
        await ctx.reply(
            f"🔄 Queue display refreshed — {len(requests)} active entr{'y' if len(requests) == 1 else 'ies'}{breakdown}.{mode_note}",
            mention_author=False
        )

    @commands.command(name='removequeue')
    @has_map_request_helper_role()
    async def removequeue(self, ctx, queue_code: str):
        """
        Remove a map from the queue by its alphabetical code.
        
        Usage: -z removequeue <queue_code>
        Example: -z removequeue a  (removes queue entry 'a')
        Example: -z removequeue ab (removes queue entry 'ab')
        
        Queue codes are shown in the queue display messages (like 'a', 'b', 'c').
        Check the queue channel to see the codes in the embed footer.
        """
        # Convert to lowercase for consistency
        queue_code = queue_code.lower()
        
        # Step 1: Send instant acknowledgment
        progress_msg = await ctx.send(f"⏳ Removing queue entry `{queue_code}`...")
        
        # Step 2: Check if queue code exists
        map_request = await database.get_map_request(ctx.guild.id, queue_code)
        if not map_request:
            await progress_msg.edit(content=f"❌ Queue code `{queue_code}` doesn't exist!\n\n**Tip:** Queue codes are shown in the queue display messages. Check the queue channel to see codes like 'a', 'b', 'c' in the embed footer.")
            return
        
        # Step 3: Remove from database
        success = await database.remove_map_request(ctx.guild.id, queue_code)
        
        if not success:
            await progress_msg.edit(content=f"❌ Failed to remove queue entry `{queue_code}` from database.")
            return
        
        await progress_msg.edit(content=f"⏳ Removing queue entry `{queue_code}`...\n✅ Deleted from database!")
        
        # Step 4: Clean up the Discord message in the queue channel
        await progress_msg.edit(content=f"⏳ Removing queue entry `{queue_code}`...\n✅ Deleted from database!\n🗑️ Cleaning up queue message...")
        
        config = await database.get_server_queue_config(ctx.guild.id)
        message_deleted = False
        if config and config["queue_channel_id"]:
            channel = ctx.guild.get_channel(config["queue_channel_id"])
            if channel and map_request.get("message_id"):
                try:
                    message = await channel.fetch_message(int(map_request["message_id"]))
                    await message.delete()
                    message_deleted = True
                except discord.NotFound:
                    message_deleted = True  # Already gone, effectively cleaned up
                except discord.Forbidden:
                    pass  # No permission, but we continue
                except discord.HTTPException:
                    pass  # HTTP error, but we continue
        
        if message_deleted or not (config and config["queue_channel_id"] and map_request.get("message_id")):
            await progress_msg.edit(content=f"⏳ Removing queue entry `{queue_code}`...\n✅ Deleted from database!\n✅ Queue message cleaned up!")
        else:
            await progress_msg.edit(content=f"⏳ Removing queue entry `{queue_code}`...\n✅ Deleted from database!\n⚠️ Could not delete queue message (no permission)")
        
        # Step 5: Refresh the queue display
        await progress_msg.edit(content=f"⏳ Removing queue entry `{queue_code}`...\n✅ Deleted from database!\n✅ Queue message cleaned up!\n🔄 Refreshing queue display... this may take a moment")
        
        try:
            await self.update_queue_display(ctx.guild)
            await progress_msg.edit(content=f"✅ Queue entry `{queue_code}` removed successfully!")
        except Exception as e:
            await progress_msg.edit(content=f"⚠️ Queue entry `{queue_code}` removed from database, but display refresh failed:\n```{str(e)[:500]}```")
    
    @removequeue.error
    async def removequeue_error(self, ctx, error):
        """Error handler for removequeue command."""
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                "❌ **Missing queue code!**\n\n"
                "**Usage:** `-z removequeue <queue_code>`\n"
                "**Example:** `-z removequeue a`\n\n"
                "**Queue codes** are alphabetical codes shown in the queue display messages.\n"
                "Check the queue channel to see codes like 'a', 'b', 'c' in the embed footer.\n\n"
                "**Tip:** You can also use `-z queueconfig` to see current queue configuration."
            )
            return
        # Re-raise other errors
        raise error
    
    @commands.command(name='setqueuechannel')
    @commands.has_permissions(administrator=True)
    async def setqueuechannel(self, ctx, channel: discord.TextChannel):
        """
        Set the channel where the queue will be displayed.
        
        Usage: -z setqueuechannel #channel
        Example: -z setqueuechannel #map-queue
        """
        success = await database.set_server_queue_config(
            ctx.guild.id,
            queue_channel_id=channel.id
        )
        
        if success:
            # Update queue display in the new channel
            await ctx.send(f"✅ Queue display channel set to {channel.mention}")
            
            # Trigger queue display update using static method
            await self.update_queue_display(ctx.guild)
        else:
            await ctx.send("❌ Failed to set queue channel.")
    
    @commands.command(name='setconfigqueue')
    @commands.has_permissions(administrator=True)
    async def setconfigqueue(self, ctx, mode: str):
        """
        Set server mode (drop_map vs loot_route) for queue display.
        
        Usage: -z setconfigqueue <mode>
        Modes: drop_map, loot_route, other
        Examples:
          -z setconfigqueue drop_map
          -z setconfigqueue loot_route
          -z setconfigqueue other
        
        Note: Queue notifications are controlled separately with -z setqueuenotifications
        """
        valid_modes = ["drop_map", "loot_route", "other"]
        if mode.lower() not in valid_modes:
            await ctx.send(f"❌ Invalid mode. Valid modes: {', '.join(valid_modes)}")
            return
        
        success = await database.set_server_queue_config(
            ctx.guild.id,
            server_mode=mode.lower()
        )
        
        if success:
            await ctx.send(f"✅ Server mode set to '{mode.lower()}'.")
            
            # Refresh queue display since priority calculation may change with mode
            await self.update_queue_display(ctx.guild)
        else:
            await ctx.send("❌ Failed to update server configuration.")
    
    @commands.command(name='queueconfig')
    @commands.has_permissions(administrator=True)
    async def queueconfig(self, ctx):
        """
        Show server configuration for map requests.
        
        Usage: -z queueconfig
        """
        config = await database.get_server_queue_config(ctx.guild.id)
        
        if not config:
            await ctx.send("❌ No configuration found for this server.")
            return
        
        embed = discord.Embed(
            title="Map Request Queue Configuration",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Add server info
        embed.add_field(name="Server", value=ctx.guild.name, inline=True)
        embed.add_field(name="Server ID", value=ctx.guild.id, inline=True)
        
        # Add queue channel info
        channel_id = config.get("queue_channel_id")
        if channel_id:
            channel = ctx.guild.get_channel(channel_id)
            if channel:
                embed.add_field(name="Queue Channel", value=channel.mention, inline=True)
            else:
                embed.add_field(name="Queue Channel", value=f"ID: {channel_id} (not found)", inline=True)
        else:
            embed.add_field(name="Queue Channel", value="Not set", inline=True)
        
        # Add mode info
        mode = config.get("server_mode", "drop_map")
        embed.add_field(name="Mode", value=mode, inline=True)
        
        # Add queue notification setting
        notifications_enabled = config.get("enable_queue_notifications", 0)
        notifications_status = "✅ Enabled" if notifications_enabled else "❌ Disabled"
        embed.add_field(name="Queue Notifications", value=notifications_status, inline=True)
        
        # Add map request count
        map_requests = await database.get_all_map_requests(ctx.guild.id)
        total_count = len(map_requests)
        
        embed.add_field(name="Total Requests", value=str(total_count), inline=True)
        
        # Add footer with instructions
        embed.set_footer(text="Use -z setconfigqueue <mode> [enable/disable] to change settings")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='clearqueue')
    @commands.has_permissions(administrator=True)
    async def clearqueue(self, ctx, confirm: str = None):
        """
        Clear ALL map requests and reset queue number back to 1.
        
        Usage: -z clearqueue confirm
        Example: -z clearqueue confirm
        WARNING: This will delete ALL map requests for this server!
        """
        if confirm != "confirm":
            await ctx.send(
                "⚠️ **WARNING: This will delete ALL map requests for this server!**\n"
                "To confirm, use: `-z clearqueue confirm`\n"
                "This will:\n"
                "1. Delete ALL map requests from database\n"
                "2. Reset last queue number back to 1\n"
                "3. Delete all queue display messages\n"
                "**This action cannot be undone!**"
            )
            return

        # Step 1: Instant acknowledgment
        progress_msg = await ctx.send("⏳ Clearing all queue entries...")

        # Step 2: Gather data
        map_requests = await database.get_all_map_requests(ctx.guild.id)
        config = await database.get_server_queue_config(ctx.guild.id)

        # Step 3: Delete queue display messages if channel exists
        await progress_msg.edit(content=f"⏳ Clearing all queue entries...\n🗑️ Deleting {len(map_requests)} queue messages...")
        deleted_count = 0
        if config and config.get("queue_channel_id"):
            channel = ctx.guild.get_channel(config["queue_channel_id"])
            if channel:
                for req in map_requests:
                    if req.get("message_id"):
                        try:
                            message = await channel.fetch_message(int(req["message_id"]))
                            await message.delete()
                            deleted_count += 1
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass
        await progress_msg.edit(content=f"⏳ Clearing all queue entries...\n✅ Deleted {deleted_count} queue messages!")

        # Step 4: Clear database
        await progress_msg.edit(content=f"⏳ Clearing all queue entries...\n✅ Deleted {deleted_count} queue messages!\n🗄️ Clearing database...")
        db = await database.get_db()
        await db.execute('DELETE FROM map_requests WHERE guild_id = ?', (ctx.guild.id,))

        # Step 5: Reset queue counter
        await progress_msg.edit(content=f"⏳ Clearing all queue entries...\n✅ Deleted {deleted_count} queue messages!\n🗄️ Clearing database...\n✅ Database cleared! Resetting queue counter...")
        # Reset last_queue_number to 0 (next will be 1)
        config = await database.get_server_queue_config(ctx.guild.id)
        if config:
            await db.execute('''
                UPDATE server_queue_config
                SET last_queue_number = 0
                WHERE guild_id = ?
            ''', (ctx.guild.id,))
        else:
            await database.set_server_queue_config(
                ctx.guild.id,
                queue_channel_id=None,
                server_mode='drop_map',
                enable_queue_notifications=0
            )
            await db.execute('''
                UPDATE server_queue_config
                SET last_queue_number = 0
                WHERE guild_id = ?
            ''', (ctx.guild.id,))

        await db.commit()

        # Final success message
        await progress_msg.edit(content=f"✅ Queue cleared! {len(map_requests)} entries removed, counter reset.")
    @commands.command(name='setqueuenotifications')
    @commands.has_permissions(administrator=True)
    async def setqueuenotifications(self, ctx, enable_notifications: str):
        """
        Enable or disable queue notification DMs when map requests are added to queue.
        
        Usage: -z setqueuenotifications <enable|disable>
        Options: enable, disable
        
        Examples:
          -z setqueuenotifications enable
          -z setqueuenotifications disable
        
        When enabled, users receive DMs when their map requests are added to the queue.
        When disabled, no DMs are sent when requests are added (but DM trigger system for completed requests still works).
        """
        # Parse notification setting
        enable_notifications_lower = enable_notifications.lower()
        if enable_notifications_lower == "enable":
            enable_queue_notifications = 1
            status_text = "enabled"
        elif enable_notifications_lower == "disable":
            enable_queue_notifications = 0
            status_text = "disabled"
        else:
            await ctx.send(
                f"❌ Invalid notification setting: '{enable_notifications}'\n"
                f"Valid options: enable, disable\n\n"
                f"**Examples:**\n"
                f"`-z setqueuenotifications enable` - Enable queue notifications\n"
                f"`-z setqueuenotifications disable` - Disable queue notifications"
            )
            return
        
        # Get current config to preserve other settings
        config = await database.get_server_queue_config(ctx.guild.id)
        current_mode = config.get("server_mode", "drop_map") if config else "drop_map"
        current_channel = config.get("queue_channel_id") if config else None
        
        success = await database.set_server_queue_config(
            ctx.guild.id,
            server_mode=current_mode,
            queue_channel_id=current_channel,
            enable_queue_notifications=enable_queue_notifications
        )
        
        if success:
            await ctx.send(f"✅ Queue notifications have been **{status_text}**.")
            
            # Refresh queue display
            await self.update_queue_display(ctx.guild)
        else:
            await ctx.send("❌ Failed to update queue notification settings.")
    
    @commands.command(name='testqueuenotify')
    @commands.has_permissions(administrator=True)
    async def testqueuenotify(self, ctx, queue_number: str):
        """
        Test queue notification DM system for a specific queue entry.
        
        Usage: -z testqueuenotify <queue_number>
        Example: -z testqueuenotify a
        Example: -z testqueuenotify ab
        
        This will manually trigger queue notification DMs for all users
        in the specified map request, simulating what happens when a
        request is added to the queue.
        """
        # Get the map request
        map_request = await database.get_map_request(ctx.guild.id, queue_number)
        if not map_request:
            await ctx.send(f"❌ No map request found with queue number `{queue_number}`.")
            return
        
        # Check if notifications are enabled for this server
        config = await database.get_server_queue_config(ctx.guild.id)
        if not config:
            await ctx.send("❌ Server queue configuration not found. Please set up queue channel first.")
            return
        
        # Check server mode for informational message
        server_mode = config.get("server_mode", "drop_map")
        if server_mode == "drop_map":
            request_type = "drop map"
        elif server_mode == "loot_route":
            request_type = "loot route"
        else:
            request_type = "map"
        
        # Inform about server mode (no longer restricting to drop_map only)
        await ctx.send(f"ℹ️ Server mode is `{server_mode}`. Sending {request_type} notification DMs.")
        
        # Check if notifications are enabled
        if not config.get("enable_queue_notifications", 0):
            await ctx.send("⚠️ Queue notifications are disabled. Enable them with `-z setconfigqueue drop_map enable_notifications`.")
            # Continue anyway for testing
        
        # Get user IDs from the map request
        user_ids = map_request.get("user_ids", [])
        if not user_ids:
            await ctx.send(f"⚠️ Map request `{queue_number}` has no user IDs associated.")
            return
        
        await ctx.send(
            f"🔧 **Testing queue notifications for `{queue_number}`**\n"
            f"• Found {len(user_ids)} user(s): {', '.join(str(uid) for uid in user_ids)}\n"
            f"• Server mode: `{config.get('server_mode')}`\n"
            f"• Notifications enabled: `{'Yes' if config.get('enable_queue_notifications', 0) else 'No'}`\n"
            f"• Sending DMs with rate limiting (3s between each)..."
        )
        
        try:
            # Replicate the notification logic directly
            queue_channel_id = config.get("queue_channel_id")
            if not queue_channel_id:
                await ctx.send("❌ Queue channel not configured. Please set queue channel first.")
                return
            
            # Get all active map requests to calculate position
            from utils.queue_priority import get_sorted_map_requests
            server_mode = config.get("server_mode", "drop_map")
            sorted_requests = await get_sorted_map_requests(ctx.guild, server_mode)
            
            # Find position of this queue number
            position = None
            for i, req in enumerate(sorted_requests, 1):
                if req.get("queue_number") == queue_number:
                    position = i
                    break
            
            # Prepare message based on position
            # Try to fetch the map request to get message_id for message link
            channel_mention = f"<#{queue_channel_id}>"  # fallback
            if map_request and map_request.get("message_id"):
                message_id = map_request["message_id"]
                message_link = f"https://discord.com/channels/{ctx.guild.id}/{queue_channel_id}/{message_id}"
                channel_mention = f"[Jump to queue message]({message_link})"
            # else keep channel mention
            
            # Calculate priority for this request
            priority_level, priority_role, _ = await self.calculate_priority_for_request(
                ctx.guild, server_mode, user_ids
            )
            
            # Build message lines dynamically
            message_lines = []
            
            # Add main header with request type
            if server_mode == "drop_map":
                request_type = "drop map"
            elif server_mode == "loot_route":
                request_type = "loot route"
            else:
                request_type = "map"
                
            message_lines.append(f"📋 **Your {request_type} request has been added to the queue!**\n")
            
            # Add position if available
            if position is not None:
                message_lines.append(f"🎯 **Position in queue:** #{position}")
                message_lines.append(f"📍 **Queue message:** {channel_mention}")
            else:
                # Fallback if position not found
                message_lines.append(f"📍 **Queue message:** {channel_mention}")
            
            # Add priority ranking role if available - show role name/number instead of mention
            if priority_role:
                # Try to get role name or identifier
                if hasattr(priority_role, 'name'):
                    role_display = priority_role.name
                elif isinstance(priority_role, str):
                    role_display = priority_role
                else:
                    role_display = f"Priority Role #{priority_level if priority_level else 'Unknown'}"
                message_lines.append(f"🏆 **Current priority ranking:** {role_display}")
            else:
                message_lines.append(f"🏆 **Current priority ranking:** No priority role")
            
            # Add ranking system link with fire emoji based on server mode
            if server_mode == "drop_map":
                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
            elif server_mode == "loot_route":
                ranking_system_link = "https://canary.discord.com/channels/971731167621574666/1131190892707979284/1180367272288714772"
            else:
                ranking_system_link = "https://canary.discord.com/channels/988564962802810961/1210837116649742396/1210839503539798026"
            
            message_lines.append(f"🔥 **Ranking system:** {ranking_system_link}")
            
            # Join all lines
            message = "\n".join(message_lines)
            
            # Send DMs with rate limiting
            success_count = 0
            import asyncio
            for i, user_id in enumerate(user_ids):
                try:
                    member = ctx.guild.get_member(user_id)
                    if not member:
                        # Try to fetch member
                        try:
                            member = await ctx.guild.fetch_member(user_id)
                        except discord.NotFound:
                            continue
                    
                    await member.send(message)
                    success_count += 1
                    
                    # Rate limiting: 3 second pause between each DM
                    if i < len(user_ids) - 1:
                        await asyncio.sleep(3)
                        
                except discord.Forbidden:
                    # User has DMs disabled
                    pass
                except Exception as e:
                    print(f"Error sending queue notification DM to user {user_id}: {e}")
            
            await ctx.send(
                f"✅ **Test completed for `{queue_number}`**\n"
                f"• Sent queue notification DMs to {success_count} out of {len(user_ids)} user(s)\n"
                f"• Check user DMs for the notification message\n"
                f"• If users didn't receive DMs, they may have DMs disabled or blocked the bot"
            )
            
            if success_count == 0:
                await ctx.send("⚠️ **Warning:** No DMs were successfully sent. Users may have DMs disabled or have blocked the bot.")
                
        except Exception as e:
            await ctx.send(
                f"❌ **Error during test:**\n"
                f"```{str(e)}```\n"
                f"Check bot logs for more details."
            )
            import traceback
            traceback.print_exc()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Re-post sticky message whenever anyone sends in the queue channel."""
        if message.guild is None:
            return
        if message.author.bot:
            return  # Bot messages already handled by queue update flow

        config = await database.get_server_queue_config(message.guild.id)
        if not config or config.get("server_mode") != "drop_map":
            return
        if config.get("queue_channel_id") != message.channel.id:
            return

        await refresh_sticky_message(message.channel, message.guild)


async def setup(bot):
    await bot.add_cog(MapCommands(bot))
