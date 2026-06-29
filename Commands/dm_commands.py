"""
DM System Commands for Wave Logistics Bot.

Commands:
- -z setdmchannel - Set the channel where users type queue codes to trigger DMs
- -z setdmlogchannel - Set the channel for success logs after DM operations
- -z dmconfig - Show current DM configuration
- -z senddm - Manually trigger DM sending for a queue code (admin only)
"""

import discord
from discord.ext import commands
import Database.database_improved as database
from typing import Optional
import asyncio

class DMCommands(commands.Cog):
    """Commands for configuring and managing the DM system."""
    
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name='setdmchannel')
    @commands.has_permissions(administrator=True)
    async def setdmchannel(self, ctx, channel: discord.TextChannel):
        """
        Set the channel where users can type queue codes to trigger DMs.
        
        Usage: -z setdmchannel #channel
        Example: -z setdmchannel #dm-trigger
        
        When users type a queue code (like "a", "b", "c") in this channel,
        the bot will DM all users associated with that map request.
        """
        success = await database.set_dm_config(
            ctx.guild.id,
            dm_channel_id=channel.id
        )
        
        if success:
            await ctx.send(f"✅ DM trigger channel set to {channel.mention}.\n"
                          f"Users can now type queue codes (like 'a', 'b', 'c') in this channel "
                          f"to trigger DMs to all users associated with that map request.")
        else:
            await ctx.send("❌ Failed to set DM trigger channel.")
    
    @commands.command(name='setdmlogchannel')
    @commands.has_permissions(administrator=True)
    async def setdmlogchannel(self, ctx, channel: discord.TextChannel):
        """
        Set the channel for success logs after DM operations.
        
        Usage: -z setdmlogchannel #channel
        Example: -z setdmlogchannel #dm-logs
        
        After successfully sending DMs for a queue code, the bot will post
        a success embed in this channel showing who was DM'd and for what map.
        """
        success = await database.set_dm_config(
            ctx.guild.id,
            dm_log_channel_id=channel.id
        )
        
        if success:
            await ctx.send(f"✅ DM log channel set to {channel.mention}.\n"
                          f"Success logs for DM operations will be posted here.")
        else:
            await ctx.send("❌ Failed to set DM log channel.")
    
    @commands.command(name='dmconfig')
    @commands.has_permissions(administrator=True)
    async def dmconfig(self, ctx):
        """
        Show current DM configuration for this server.
        
        Usage: -z dmconfig
        """
        config = await database.get_dm_config(ctx.guild.id)
        
        if not config:
            embed = discord.Embed(
                title="DM Configuration",
                description="No DM configuration set for this server.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="Setup Instructions",
                value="1. Use `-z setdmchannel #channel` to set the DM trigger channel\n"
                      "2. Use `-z setdmlogchannel #channel` to set the success log channel\n"
                      "3. Users can then type queue codes in the trigger channel to send DMs",
                inline=False
            )
            await ctx.send(embed=embed)
            return
        
        embed = discord.Embed(
            title="DM Configuration",
            color=discord.Color.blue()
        )
        
        # DM Trigger Channel
        if config.get("dm_channel_id"):
            channel = ctx.guild.get_channel(config["dm_channel_id"])
            if channel:
                embed.add_field(
                    name="DM Trigger Channel",
                    value=f"{channel.mention} (`{channel.id}`)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="DM Trigger Channel",
                    value=f"Channel not found (`{config['dm_channel_id']}`)",
                    inline=False
                )
        else:
            embed.add_field(
                name="DM Trigger Channel",
                value="Not set",
                inline=False
            )
        
        # DM Log Channel
        if config.get("dm_log_channel_id"):
            channel = ctx.guild.get_channel(config["dm_log_channel_id"])
            if channel:
                embed.add_field(
                    name="DM Log Channel",
                    value=f"{channel.mention} (`{channel.id}`)",
                    inline=False
                )
            else:
                embed.add_field(
                    name="DM Log Channel",
                    value=f"Channel not found (`{config['dm_log_channel_id']}`)",
                    inline=False
                )
        else:
            embed.add_field(
                name="DM Log Channel",
                value="Not set",
                inline=False
            )
        
        # DM System Status (enabled/disabled)
        enabled = config.get("enabled", 1)  # Default to enabled if not set
        if enabled == 1:
            status_text = "✅ **Enabled**"
            status_desc = "The DM system is active and will process queue codes in the trigger channel."
        else:
            status_text = "❌ **Disabled**"
            status_desc = "The DM system is inactive and will ignore all queue codes."
        
        embed.add_field(
            name="DM System Status",
            value=f"{status_text}\n{status_desc}\n\n"
                  f"Use `-z setdmenabled enable` to enable or `-z setdmenabled disable` to disable.",
            inline=False
        )
        
        # Templates are now hardcoded (not configurable)
        embed.add_field(
            name="DM Templates",
            value="✅ **Hardcoded (not configurable)**\n"
                  "• **Drop Map Template:** Default template with {mention} and {link} placeholders\n"
                  "• **Loot Route Template:** Default template with {mention} and {link} placeholders\n"
                  "\n"
                  "The {link} placeholder will be replaced with the channel link provided in the DM trigger message.",
            inline=False
        )
        
        embed.set_footer(text="Users type queue codes in brackets (a), [b], {c} in the DM trigger channel - bot automatically extracts channel link")
        
        await ctx.send(embed=embed)
    
    @commands.command(name='setdmenabled')
    @commands.has_permissions(administrator=True)
    async def setdmenabled(self, ctx, enable_dm: str):
        """
        Enable or disable the DM system for this server.
        
        Usage: -z setdmenabled <enable|disable>
        Options: enable, disable
        
        Examples:
          -z setdmenabled enable
          -z setdmenabled disable
        
        When enabled, the DM system will process queue codes in the DM trigger channel.
        When disabled, the DM system will ignore all queue codes (no DMs will be sent).
        """
        # Parse DM setting
        enable_dm_lower = enable_dm.lower()
        if enable_dm_lower == "enable":
            enabled_value = 1
            status_text = "enabled"
        elif enable_dm_lower == "disable":
            enabled_value = 0
            status_text = "disabled"
        else:
            await ctx.send(
                f"❌ Invalid DM setting: '{enable_dm}'\n"
                f"Valid options: enable, disable\n\n"
                f"**Examples:**\n"
                f"`-z setdmenabled enable` - Enable DM system\n"
                f"`-z setdmenabled disable` - Disable DM system"
            )
            return
        
        # Get current config to preserve other settings
        config = await database.get_dm_config(ctx.guild.id)
        
        if config:
            # Update existing config with enabled value
            success = await database.set_dm_config(
                ctx.guild.id,
                enabled=enabled_value
            )
        else:
            # Create new config with default values and specified enabled state
            success = await database.set_dm_config(
                ctx.guild.id,
                enabled=enabled_value
            )
        
        if success:
            await ctx.send(f"✅ DM system has been **{status_text}**.")
        else:
            await ctx.send("❌ Failed to update DM system settings.")
    
    @commands.command(name='senddm')
    @commands.has_permissions(administrator=True)
    async def senddm(self, ctx, queue_code: str):
        """
        Manually trigger DM sending for a queue code.
        
        Usage: -z senddm <queue_code>
        Example: -z senddm a
        
        This will immediately start sending DMs to all users associated with
        the specified queue code, using the configured templates.
        """
        queue_code = queue_code.lower()
        
        # Check if queue code exists
        map_request = await database.get_map_request(ctx.guild.id, queue_code)
        if not map_request:
            await ctx.send(f"❌ Queue code '{queue_code}' doesn't exist!")
            return
        
        # Check if DM configuration is set
        dm_config = await database.get_dm_config(ctx.guild.id)
        if not dm_config or not dm_config.get("dm_channel_id"):
            await ctx.send("❌ DM system not configured. Please set up DM channels first using `-z setdmchannel` and `-z setdmlogchannel`.")
            return
        
        # Get server mode to determine which template to use
        server_config = await database.get_server_queue_config(ctx.guild.id)
        server_mode = server_config.get("server_mode", "drop_map") if server_config else "drop_map"
        
        # Hardcoded templates for the manual `senddm` path. NOTE: the wording
        # here intentionally differs from DMProcessor's auto-trigger templates,
        # and the dm_template_* columns in the dm_config DB table are unused —
        # editing the DB values has no effect on what users receive.
        dm_templates = {
            "drop_map": {
                "default": """Hey {mention} 👋

Your **Requested Dropmap** is complete, you can find it over at {link}

If you want us to keep making more, you can support wave by giving us a vouch in https://discord.com/channels/988564962802810961/1210814682357698621 telling us what you wish you knew sooner about wave and why the server is actually good. Any and all vouches are greatly appreciated, Thank You for choosing wave!"""
            },
            "loot_route": {
                "default": """Hey {mention} 👋

Your **Requested Loot Route** is complete, you can find it over at {link}

If you want us to keep making more, you can support wave by giving us a vouch in <#1132639885883359302> telling us what you wish you knew sooner about wave and why the server is actually good. Any and all vouches are greatly appreciated, Thank You for choosing wave!"""
            }
        }
        
        # Determine which template to use
        if server_mode == "drop_map":
            template = dm_templates["drop_map"]["default"]
        else:
            template = dm_templates["loot_route"]["default"]
        
        # Get user IDs from the map request
        user_ids = map_request.get("user_ids", [])
        if not user_ids:
            await ctx.send(f"❌ No users associated with queue code '{queue_code}'.")
            return
        
        # Show confirmation
        embed = discord.Embed(
            title="DM Sending Confirmation",
            description=f"Ready to send DMs for queue code **{queue_code}**",
            color=discord.Color.yellow()
        )
        embed.add_field(name="Users to DM", value=f"{len(user_ids)} user(s)", inline=False)
        embed.add_field(name="Server Mode", value=server_mode, inline=False)
        embed.add_field(name="Template", value="✅ Using hardcoded template (not configurable)", inline=False)
        embed.add_field(
            name="Rate Limiting",
            value="3 second pause between each DM\n20 minute pause after every 5 DMs",
            inline=False
        )
        embed.set_footer(text="This will take some time due to rate limiting. Check the log channel for progress.")
        
        await ctx.send(embed=embed)
        
        # Ask for channel link
        await ctx.send("📝 **Please provide a channel link or mention where the map/route is posted.**\n"
                      "Example: `#channel-name` or `https://discord.com/channels/...`\n"
                      "Type `cancel` to cancel.")
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        try:
            channel_msg = await self.bot.wait_for('message', timeout=60.0, check=check)
            if channel_msg.content.lower() == 'cancel':
                await ctx.send("❌ DM sending cancelled.")
                return
            
            # Extract channel link from message
            import re
            channel_mention_pattern = r'<#(\d+)>'
            channel_url_pattern = r'discord\.com/channels/(\d+)/(\d+)'
            
            channel_id = None
            channel_link = None
            
            channel_mention_matches = re.findall(channel_mention_pattern, channel_msg.content)
            channel_url_matches = re.findall(channel_url_pattern, channel_msg.content)
            
            if channel_mention_matches:
                channel_id = int(channel_mention_matches[0])
                channel_link = f"<#{channel_id}>"
            elif channel_url_matches:
                guild_id_from_url, channel_id_from_url = channel_url_matches[0]
                if int(guild_id_from_url) == ctx.guild.id:
                    channel_id = int(channel_id_from_url)
                    channel_link = f"<#{channel_id}>"
                else:
                    await ctx.send("❌ Channel URL points to a different server. Please use a channel from this server.")
                    return
            else:
                await ctx.send("❌ No valid channel link found. Please include a channel mention or URL.")
                return
            
            # Start DM sending
            processing_msg = await ctx.send(f"🔄 Starting DM sending for queue code **{queue_code}**...")
            
            # Reuse the loaded DMProcessor cog so this senddm shares its per-guild lock
            # with the auto-trigger flow (prevents two batches running in parallel).
            dm_processor = self.bot.get_cog("DMProcessor")
            if dm_processor is None:
                await ctx.send("❌ DMProcessor cog is not loaded. Cannot send DMs.")
                return

            # Serialize against any in-flight auto-trigger run for this guild
            lock = dm_processor.locks.setdefault(ctx.guild.id, asyncio.Lock())
            if lock.locked():
                await ctx.send(
                    f"⏳ A DM batch is already running for this server. **{queue_code}** will start once it finishes."
                )

            async with lock:
                # Re-verify the queue entry still exists after waiting
                fresh_request = await database.get_map_request(ctx.guild.id, queue_code)
                if not fresh_request:
                    await ctx.send(f"⚠️ Queue code '{queue_code}' no longer exists. Aborting.")
                    return
                map_request = fresh_request
                user_ids = map_request.get("user_ids", [])
                if not user_ids:
                    await ctx.send(f"⚠️ Queue code '{queue_code}' has no users. Aborting.")
                    return

                # Call the DM sending method (returns 3 values; discard failed_user_ids)
                success_count, failed_count, _ = await dm_processor._send_dms_to_users(
                    ctx.guild.id, user_ids, template, map_request, queue_code, ctx.channel, channel_link
                )

                if success_count > 0:
                    # Remove from database
                    await database.remove_map_request(ctx.guild.id, queue_code)

                    # Send success log to log channel if configured
                    if dm_config.get("dm_log_channel_id"):
                        log_channel = ctx.guild.get_channel(dm_config["dm_log_channel_id"])
                        if log_channel:
                            await dm_processor._send_success_log(
                                log_channel, queue_code, map_request, success_count, failed_count
                            )

                    await processing_msg.edit(
                        content=f"✅ Successfully sent DMs for queue code **{queue_code}**!\n"
                               f"• **Successful DMs:** {success_count}\n"
                               f"• **Failed DMs:** {failed_count}\n"
                               f"• **Queue entry removed:** Yes"
                    )
                else:
                    await processing_msg.edit(
                        content=f"❌ Failed to send any DMs for queue code **{queue_code}**.\n"
                               f"• **Successful DMs:** 0\n"
                               f"• **Failed DMs:** {failed_count}\n"
                               f"• **Queue entry preserved:** Yes"
                    )

        except asyncio.TimeoutError:
            await ctx.send("❌ Timed out waiting for channel link. DM sending cancelled.")
        except Exception as e:
            await ctx.send(f"❌ Error during DM sending: {str(e)}")


async def setup(bot):
    """Add the DMCommands cog to the bot."""
    await bot.add_cog(DMCommands(bot))