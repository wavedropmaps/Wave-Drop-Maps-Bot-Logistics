"""
DM Processor for Wave Logistics Bot.

Listens for queue codes in the configured DM trigger channel and sends DMs
to all users associated with that map request, with rate limiting.
"""

import discord
from discord.ext import commands
import Database.database_improved as database
import asyncio
from typing import List, Dict, Any, Optional
import re

class DMProcessor(commands.Cog):
    """Processes DM requests for queue codes."""
    
    def __init__(self, bot):
        self.bot = bot
        self.locks: Dict[int, asyncio.Lock] = {}  # guild_id -> Lock; serializes DM batches per guild
        # SOURCE OF TRUTH for outgoing DM text. The dm_template_* columns in
        # the dm_config DB table are NOT read by any sender — editing those
        # has no effect. (Commands/dm_commands.py `senddm` carries its own
        # copy of these; keep them in sync if you change the wording.)
        self.dm_templates = {
            "drop_map": {
                "default": """Hey {mention} :wave:

Your **requested dropmap** is complete, you can find it over at {link}

If you want wave to keep **making more free drop maps**, you can **support wave **by giving a vouch in https://discord.com/channels/988564962802810961/1210814682357698621 and say something you like about the server!

Any and all vouches are** greatly appreciated**, thank you for choosing wave!"""
            },
            "loot_route": {
                "default": """Hey {mention} :wave:

Your **requested loot route** is complete, you can find it over at {link}

If you want wave to keep **making more free loot routes**, you can **support wave **by giving a vouch in https://discord.com/channels/971731167621574666/1132639885883359302 and say something you like about the server!

Any and all vouches are** greatly appreciated**, thank you for choosing wave!"""
            },
            "surge_route": {
                "default": """Hey {mention} :wave:

Your **requested surge route** is complete, you can find it here: {link}

If you'd like Wave to keep **making __free__ surge routes**, consider leaving a vouch in https://discord.com/channels/971731167621574666/1132639885883359302 and sharing what you **enjoy about the server.**

It really helps and is **greatly appreciated** — thanks for choosing Wave!"""
            }
        }

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        self.locks.pop(guild.id, None)

    class DMFailureView(discord.ui.View):
        """View with buttons for handling DM failures."""
        
        def __init__(self, dm_processor, guild_id: int, queue_code: str, map_request: Dict[str, Any],
                     failed_user_ids: List[int], channel_link: str, timeout: float = 86400.0):  # 24 hour timeout
            super().__init__(timeout=timeout)
            self.dm_processor = dm_processor
            self.guild_id = guild_id
            self.queue_code = queue_code
            self.map_request = map_request
            self.failed_user_ids = failed_user_ids
            self.channel_link = channel_link
        
        @discord.ui.button(label="🔄 Retry Failed DMs", style=discord.ButtonStyle.primary, custom_id="retry_failed_dms")
        async def retry_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Retry sending DMs to failed users."""
            # Send immediate response without "thinking" state
            await interaction.response.defer(ephemeral=False)
            
            # Select template: server_mode first, then route_type as sub-type within loot_route
            server_config = await database.get_server_queue_config(self.guild_id)
            server_mode = server_config.get("server_mode", "drop_map") if server_config else "drop_map"
            if server_mode == "drop_map":
                template = self.dm_processor.dm_templates["drop_map"]["default"]
            else:
                route_type = self.map_request.get("route_type", "loot_route")
                if route_type == "surge_route":
                    template = self.dm_processor.dm_templates["surge_route"]["default"]
                else:
                    template = self.dm_processor.dm_templates["loot_route"]["default"]
            
            # Get the log channel from the interaction
            log_channel = interaction.channel
            
            # Separate genuine role IDs from user IDs by asking the guild.
            # (The old snowflake-size heuristic `id > 1e18` misclassified every
            # user account created after mid-2022, silently dropping them
            # from retries.)
            guild = self.dm_processor.bot.get_guild(self.guild_id)
            role_ids = []
            user_ids_only = []
            for user_id in self.failed_user_ids:
                if guild and guild.get_role(user_id) is not None:
                    role_ids.append(user_id)
                else:
                    user_ids_only.append(user_id)
            
            retry_msg = await log_channel.send(
                f"🔄 Retrying DMs for queue code **{self.queue_code}**..."
            )
            
            # Only retry actual user IDs (skip role IDs)
            users_to_retry = user_ids_only if user_ids_only else self.failed_user_ids
            
            # Retry sending DMs
            success_count, failed_count, new_failed_user_ids = await self.dm_processor._send_dms_to_users(
                self.guild_id, users_to_retry, template, self.map_request,
                self.queue_code, log_channel, self.channel_link
            )
            
            # Update the retry message
            retry_result_msg = f"✅ Retry completed for queue code **{self.queue_code}**!\n"
            retry_result_msg += f"• **Successful DMs:** {success_count}\n"
            retry_result_msg += f"• **Failed DMs:** {failed_count}"
            
            # Add special message if all DMs failed
            if success_count == 0 and failed_count > 0:
                retry_result_msg += "\n\n⚠️ **All DMs failed!**"
                
                if role_ids:
                    retry_result_msg += f"\n• **Role IDs detected:** {len(role_ids)} (cannot DM roles)"
                    retry_result_msg += "\n• **Solution:** Remove role IDs from queue entry using `-z addmap` edit"
                
                retry_result_msg += "\n\n**Options:**"
                retry_result_msg += "\n• Click **🔄 Retry Failed DMs** to try again"
                retry_result_msg += "\n• Click **🗑️ Delete Queue Entry** to remove this queue"
            
            await retry_msg.edit(content=retry_result_msg)
            
            # Update the original failure log embed
            embed = interaction.message.embeds[0] if interaction.message.embeds else None
            if embed:
                # Update the embed fields
                for i, field in enumerate(embed.fields):
                    if field.name == "Failed DMs":
                        embed.set_field_at(i, name="Failed DMs", value=str(failed_count), inline=True)
                    elif field.name.startswith("Failed Users"):
                        # Update failed users list
                        if new_failed_user_ids:
                            user_mentions = []
                            for user_id in new_failed_user_ids[:10]:  # Limit to 10 mentions
                                user_mentions.append(f"<@{user_id}>")
                            
                            if len(new_failed_user_ids) > 10:
                                user_mentions.append(f"... and {len(new_failed_user_ids) - 10} more")
                            
                            embed.set_field_at(i,
                                name=f"Failed Users ({len(new_failed_user_ids)} total)",
                                value=", ".join(user_mentions) if user_mentions else "None",
                                inline=False
                            )
                        break
                
                # Update the description
                embed.description = f"DM operation partially failed for queue code **{self.queue_code}** (Retried)"
                
                # Update the message with refreshed view
                await interaction.message.edit(embed=embed, view=self)
            
            # Disable the retry button if all DMs succeeded
            if failed_count == 0:
                button.disabled = True
                button.label = "✅ All DMs Sent"
                await interaction.message.edit(view=self)
                # Send follow-up confirmation
                await interaction.followup.send("✅ All DMs successfully sent!", ephemeral=True)
            else:
                # Send follow-up with retry options
                follow_up_msg = f"🔄 Retry completed with {failed_count} failed DMs."
                
                if role_ids:
                    follow_up_msg += f"\n⚠️ **Note:** {len(role_ids)} role IDs detected (cannot DM roles)."
                    follow_up_msg += "\nUse `-z addmap` to edit the queue entry and remove role IDs."
                
                follow_up_msg += "\n\n**Options:**"
                follow_up_msg += "\n• Click **🔄 Retry Failed DMs** to try again"
                follow_up_msg += "\n• Click **🗑️ Delete Queue Entry** to remove this queue"
                
                await interaction.followup.send(follow_up_msg, ephemeral=True)
        
        @discord.ui.button(label="🗑️ Delete Queue Entry", style=discord.ButtonStyle.danger, custom_id="delete_queue_entry")
        async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            """Delete the queue entry from database and remove queue message."""
            await interaction.response.defer(ephemeral=False)
            
            # Remove from database
            success = await database.remove_map_request(self.guild_id, self.queue_code)
            
            if success:
                # Get guild for queue operations
                guild = self.dm_processor.bot.get_guild(self.guild_id)
                
                # Try to delete the corresponding queue message if it exists
                config = await database.get_server_queue_config(self.guild_id)
                if config and config["queue_channel_id"] and guild:
                    channel = guild.get_channel(config["queue_channel_id"])
                    if channel and self.map_request.get("message_id"):
                        try:
                            message = await channel.fetch_message(int(self.map_request["message_id"]))
                            await message.delete()
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                            pass  # Message already deleted or can't be accessed
                
                # Update queue display if guild is available (edit-only — renumber
                # existing posts; don't create new ones to avoid Management bot
                # treating them as fresh loot route submissions).
                if guild:
                    from Commands.map_commands import MapCommands
                    map_cog = MapCommands(self.dm_processor.bot)
                    await map_cog.update_queue_display(guild, create_missing=False)
                
                # Update the failure log embed
                embed = interaction.message.embeds[0] if interaction.message.embeds else None
                if embed:
                    embed.description = f"❌ Queue entry **{self.queue_code}** deleted by staff"
                    embed.color = discord.Color.red()
                    embed.set_footer(text="Queue entry deleted from database and queue display updated")
                    
                    # Remove buttons
                    await interaction.message.edit(embed=embed, view=None)
                
                await interaction.followup.send(f"✅ Queue entry '{self.queue_code}' deleted successfully. Queue display has been updated.")
            else:
                await interaction.followup.send(f"❌ Failed to delete queue entry '{self.queue_code}'.")
    
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for queue codes in the DM trigger channel."""
        # Ignore bot messages
        if message.author.bot:
            return
        
        # Check if message is in a guild
        if not message.guild:
            return
        
        guild_id = message.guild.id
        
        # Get DM configuration
        dm_config = await database.get_dm_config(guild_id)
        if not dm_config or not dm_config.get("dm_channel_id"):
            return
        
        # Check if DM system is enabled (default to enabled if field doesn't exist)
        if dm_config.get("enabled", 1) == 0:
            # DM system is disabled, silently ignore
            return
        
        # Check if message is in the DM trigger channel
        if message.channel.id != dm_config["dm_channel_id"]:
            return
        
        # Helper function to send error messages to log channel if configured
        async def send_error(error_message):
            """Send error to DM log channel if configured, otherwise don't show to members."""
            if dm_config.get("dm_log_channel_id"):
                log_channel = message.guild.get_channel(dm_config["dm_log_channel_id"])
                if log_channel:
                    await log_channel.send(error_message)
            # Don't send error to trigger channel where members can see it
        
        # NOTE: Concurrency is handled below by an asyncio.Lock per guild — second triggers
        # arriving mid-run now queue up instead of being dropped.

        # Extract queue code from message - more flexible matching
        content = message.content.strip()
        
        # Convert to lowercase for case-insensitive matching
        content_lower = content.lower()
        
        # Common English words to filter out (1-3 letters)
        common_words = {
            # 1-letter words
            'a', 'i',
            # 2-letter words
            'an', 'as', 'at', 'be', 'by', 'do', 'go', 'he', 'if', 'in', 'is', 'it', 'me', 'my', 'no', 'of', 'on', 'or',
            'so', 'to', 'up', 'us', 'we',
            # 3-letter words
            'and', 'are', 'but', 'can', 'did', 'for', 'get', 'had', 'has', 'her', 'him', 'his', 'how', 'its', 'let',
            'may', 'not', 'now', 'off', 'our', 'out', 'see', 'she', 'the', 'too', 'use', 'was', 'way', 'who', 'why',
            'yes', 'you'
        }
        
        # Look for queue codes in brackets: (a), [b], {c}
        # Only accept codes that are explicitly marked with brackets
        # Supports 1-5 letters to handle codes like a, aa, aaa, aaaa, aaaaa
        bracket_pattern = r'[(\[{]([a-z]{1,5})[)\]\}]'
        bracket_matches = re.findall(bracket_pattern, content_lower)
        
        if not bracket_matches:
            # No bracketed queue code found - stay silent (don't send error message)
            return
        
        # Use the first bracketed code found
        queue_code = bracket_matches[0]
        print(f"DM Processor: Found queue code '{queue_code}' in brackets from message: '{content}'")
        
        # Extract channel link from message
        # Look for Discord channel mentions: <#1234567890>
        channel_mention_pattern = r'<#(\d+)>'
        channel_mention_matches = re.findall(channel_mention_pattern, content)
        
        # Look for Discord channel URLs: https://discord.com/channels/guild_id/channel_id
        channel_url_pattern = r'discord\.com/channels/(\d+)/(\d+)'
        channel_url_matches = re.findall(channel_url_pattern, content)
        
        channel_id = None
        channel_link = None
        
        if channel_mention_matches:
            # Channel mention found
            channel_id = int(channel_mention_matches[0])
            channel_link = f"<#{channel_id}>"
            print(f"DM Processor: Found channel mention: {channel_link}")
        elif channel_url_matches:
            # Channel URL found
            guild_id_from_url, channel_id_from_url = channel_url_matches[0]
            if int(guild_id_from_url) == guild_id:
                channel_id = int(channel_id_from_url)
                channel_link = f"<#{channel_id}>"
                print(f"DM Processor: Found channel URL for channel ID: {channel_id}")
            else:
                # Send error to log channel if configured, otherwise stay silent
                await send_error("❌ Channel URL points to a different server. Please use a channel from this server.")
                return
        else:
            # No channel link found
            # Send error to log channel if configured, otherwise stay silent
            await send_error("❌ No channel link found in message. Please include a channel mention or URL (e.g., <#1234567890> or https://discord.com/channels/...).")
            return
        
        # Check if queue code exists
        map_request = await database.get_map_request(guild_id, queue_code)
        if not map_request:
            # Send error to log channel if configured, otherwise stay silent
            await send_error(f"❌ Queue code '{queue_code}' doesn't exist!")
            return
        
        # Select template: server_mode first, then route_type as sub-type within loot_route
        server_config = await database.get_server_queue_config(guild_id)
        server_mode = server_config.get("server_mode", "drop_map") if server_config else "drop_map"
        if server_mode == "drop_map":
            template = self.dm_templates["drop_map"]["default"]
        else:
            # loot_route server — check route_type to distinguish loot vs surge
            route_type = map_request.get("route_type", "loot_route")
            if route_type == "surge_route":
                template = self.dm_templates["surge_route"]["default"]
            else:
                template = self.dm_templates["loot_route"]["default"]

        # Get user IDs from the map request
        user_ids = map_request.get("user_ids", [])
        if not user_ids:
            # Send error to log channel if configured, otherwise stay silent
            await send_error(f"❌ No users associated with queue code '{queue_code}'.")
            return
        
        # Resolve log channel up-front; trigger channel stays silent for member-facing flows
        log_channel_id = dm_config.get("dm_log_channel_id")
        log_channel = message.guild.get_channel(log_channel_id) if log_channel_id else None

        # Get-or-create per-guild lock; later requests queue behind in-flight ones instead of being dropped
        lock = self.locks.setdefault(guild_id, asyncio.Lock())
        if lock.locked() and log_channel:
            try:
                await log_channel.send(
                    f"📥 Queued: code **{queue_code}** will run after the current DM batch finishes."
                )
            except discord.HTTPException:
                pass

        async with lock:
            # Re-verify the entry inside the lock — a prior queued run may have removed it,
            # or staff may have edited the user list while we waited
            fresh_request = await database.get_map_request(guild_id, queue_code)
            if not fresh_request:
                if log_channel:
                    try:
                        await log_channel.send(
                            f"⚠️ Skipping queued DM for **{queue_code}** — queue entry no longer exists."
                        )
                    except discord.HTTPException:
                        pass
                return
            map_request = fresh_request
            user_ids = map_request.get("user_ids", [])
            if not user_ids:
                if log_channel:
                    try:
                        await log_channel.send(
                            f"⚠️ Skipping queued DM for **{queue_code}** — no users associated."
                        )
                    except discord.HTTPException:
                        pass
                return

            try:
                success_count, failed_count, failed_user_ids = await self._send_dms_to_users(
                    guild_id, user_ids, template, map_request, queue_code, log_channel, channel_link
                )

                # Send completion message
                if failed_count == 0:
                    # All DMs succeeded
                    # Delete the original queue message if it exists
                    if map_request.get("message_id"):
                        try:
                            # Get queue channel from server config (not from map_request which doesn't have channel_id)
                            queue_channel_id = server_config.get("queue_channel_id", 0) if server_config else 0
                            if queue_channel_id:
                                queue_channel = message.guild.get_channel(int(queue_channel_id))
                                if queue_channel:
                                    queue_message = await queue_channel.fetch_message(int(map_request["message_id"]))
                                    await queue_message.delete()
                        except (discord.NotFound, discord.Forbidden, discord.HTTPException, ValueError):
                            pass  # Message already deleted or can't be accessed

                    # Auto-cleanup: Remove from database after Discord cleanup
                    try:
                        deleted = await database.remove_map_request(guild_id, queue_code)
                        if deleted and log_channel:
                            await log_channel.send(f"✅ Queue {queue_code} automatically cleaned up from database")
                    except Exception as db_error:
                        # Log error but don't fail the whole process
                        if log_channel:
                            await log_channel.send(f"⚠️ Database cleanup failed for {queue_code}: {db_error}")

                    # Update queue display to refresh numbering for remaining entries.
                    # Edit-only: don't create new messages (would look like new submissions
                    # to the Management bot).
                    from Commands.map_commands import MapCommands
                    map_cog = MapCommands(self.bot)
                    await map_cog.update_queue_display(message.guild, create_missing=False)

                    # Send success log to log channel if configured
                    if log_channel:
                        await self._send_success_log(
                            log_channel, queue_code, map_request, success_count, failed_count
                        )
                else:
                    # Some or all DMs failed
                    # Send failure log to log channel if configured
                    if log_channel:
                        await self._send_failure_log(
                            log_channel, queue_code, map_request, success_count,
                            failed_count, failed_user_ids, channel_link
                        )

            except Exception as e:
                # Send error to log channel if configured
                if log_channel:
                    await log_channel.send(f"❌ Error processing DM request for queue code **{queue_code}**: {str(e)}")
    
    async def _send_dms_to_users(self, guild_id: int, user_ids: List[int], template: str,
                                map_request: Dict[str, Any], queue_code: str,
                                status_channel: Optional[discord.TextChannel], channel_link: str) -> tuple[int, int, List[int]]:
        """Enqueue DMs into the shared DM queue for all users. Returns instantly."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return 0, len(user_ids), user_ids.copy()

        image_url = map_request.get("image_url", "No image")
        success_count = 0
        failed_count = 0
        failed_user_ids = []

        for user_id in user_ids:
            try:
                member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                template_vars = {
                    "mention": member.mention,
                    "user_id": user_id,
                    "queue_code": queue_code,
                    "image_url": image_url,
                    "link": channel_link,
                    "channel_link": channel_link,
                    "description": map_request.get("description", "No description"),
                    "created_at": map_request.get("created_at", "Unknown"),
                    "notes": map_request.get("description", "No additional notes"),
                }
                try:
                    dm_content = template.format(**template_vars)
                except KeyError as e:
                    missing_key = str(e).strip("'")
                    template_vars[missing_key] = f"[{missing_key}]"
                    dm_content = template.format(**template_vars)
                await member.send(dm_content)
                success_count += 1
            except discord.NotFound:
                failed_count += 1
                failed_user_ids.append(user_id)
            except discord.Forbidden:
                failed_count += 1
                failed_user_ids.append(user_id)
            except Exception as e:
                print(f"Error sending DM to user {user_id}: {e}")
                failed_count += 1
                failed_user_ids.append(user_id)

        return success_count, failed_count, failed_user_ids
    
    async def _send_success_log(self, log_channel: discord.TextChannel, queue_code: str,
                               map_request: Dict[str, Any], success_count: int, 
                               failed_count: int):
        """Send success log to the configured log channel."""
        embed = discord.Embed(
            title="✅ DMs Queued for Delivery",
            description=(
                f"All DMs for queue code **{queue_code}** were handed to the "
                f"shared DM queue. Delivery failures (DMs disabled etc.) are "
                f"reported separately by the DM queue."
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow()
        )

        embed.add_field(name="Queue Code", value=queue_code, inline=True)
        embed.add_field(name="Queued DMs", value=success_count, inline=True)
        embed.add_field(name="Failed DMs", value=failed_count, inline=True)
        
        if map_request.get("image_url"):
            embed.add_field(name="Image URL", value=map_request["image_url"], inline=False)
        
        if map_request.get("description"):
            embed.add_field(
                name="Description", 
                value=map_request["description"][:100] + "..." if len(map_request["description"]) > 100 else map_request["description"],
                inline=False
            )
        
        # List users who were DM'd (first 10)
        user_ids = map_request.get("user_ids", [])
        if user_ids:
            user_mentions = []
            for user_id in user_ids[:10]:  # Limit to 10 mentions
                user_mentions.append(f"<@{user_id}>")
            
            if len(user_ids) > 10:
                user_mentions.append(f"... and {len(user_ids) - 10} more")
            
            embed.add_field(
                name=f"Users queued ({len(user_ids)} total)",
                value=", ".join(user_mentions),
                inline=False
            )

        embed.set_footer(text=f"Queue entry removed from database")
        
        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Error sending success log: {e}")
    
    async def _send_failure_log(self, log_channel: discord.TextChannel, queue_code: str,
                               map_request: Dict[str, Any], success_count: int,
                               failed_count: int, failed_user_ids: List[int],
                               channel_link: str):
        """Send failure log to the configured log channel with retry/delete buttons."""
        embed = discord.Embed(
            title="⚠️ DM Operation Partially Failed",
            description=f"DM operation partially failed for queue code **{queue_code}**",
            color=discord.Color.orange(),
            timestamp=discord.utils.utcnow()
        )
        
        embed.add_field(name="Queue Code", value=queue_code, inline=True)
        embed.add_field(name="Successful DMs", value=success_count, inline=True)
        embed.add_field(name="Failed DMs", value=failed_count, inline=True)
        
        if map_request.get("image_url"):
            embed.add_field(name="Image URL", value=map_request["image_url"], inline=False)
        
        if map_request.get("description"):
            embed.add_field(
                name="Description",
                value=map_request["description"][:100] + "..." if len(map_request["description"]) > 100 else map_request["description"],
                inline=False
            )
        
        # List failed users (first 10)
        if failed_user_ids:
            user_mentions = []
            for user_id in failed_user_ids[:10]:  # Limit to 10 mentions
                user_mentions.append(f"<@{user_id}>")
            
            if len(failed_user_ids) > 10:
                user_mentions.append(f"... and {len(failed_user_ids) - 10} more")
            
            embed.add_field(
                name=f"Failed Users ({len(failed_user_ids)} total)",
                value=", ".join(user_mentions) if user_mentions else "None",
                inline=False
            )
        
        embed.set_footer(text="Use buttons below to retry failed DMs or delete queue entry")
        
        try:
            # Create view with buttons
            view = self.DMFailureView(
                dm_processor=self,
                guild_id=log_channel.guild.id,
                queue_code=queue_code,
                map_request=map_request,
                failed_user_ids=failed_user_ids,
                channel_link=channel_link
            )
            
            await log_channel.send(embed=embed, view=view)
        except Exception as e:
            print(f"Error sending failure log: {e}")


async def setup(bot):
    """Add the DMProcessor cog to the bot."""
    await bot.add_cog(DMProcessor(bot))