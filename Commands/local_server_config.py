"""
Local Server Configuration Commands for Wave Logistics Bot.

Commands:
- -z allowchannel <channel> - Add a channel to allowed list (admin only)
- -z removeallowed <channel> - Remove a channel from allowed list (admin only)
- -z listallowed - List all allowed channels
- -z clearallowed - Clear all allowed channels (admin only, requires confirmation)
"""

import discord
from discord.ext import commands
from typing import Optional, List
from datetime import datetime
import Database.database_improved as database

class LocalServerConfig(commands.Cog):
    """Local server configuration commands for allowed channels."""
    
    def __init__(self, bot):
        self.bot = bot
    
    # Helper function to check if user is administrator
    def is_administrator():
        async def predicate(ctx):
            return ctx.author.guild_permissions.administrator
        return commands.check(predicate)
    
    @commands.command(name='allowchannel')
    @is_administrator()
    async def allowchannel(self, ctx, *channels: discord.TextChannel):
        """
        Add one or more channels to the allowed list where the bot can respond.
        
        Usage: -z allowchannel #channel1 #channel2 #channel3
        """
        if not channels:
            embed = discord.Embed(
                title="❌ No Channels Specified",
                description="Please mention one or more channels to add.\n"
                          "Example: `-z allowchannel #bot-commands #general`",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        added_channels = []
        already_allowed_channels = []
        failed_channels = []
        
        for channel in channels:
            # Check if channel is already allowed
            if await database.is_channel_allowed(ctx.guild.id, channel.id):
                already_allowed_channels.append(channel.mention)
                continue
            
            # Add channel to allowed list
            success = await database.add_allowed_channel(
                guild_id=ctx.guild.id,
                channel_id=channel.id,
                added_by=ctx.author.id
            )
            
            if success:
                added_channels.append(channel.mention)
            else:
                failed_channels.append(channel.mention)
        
        # Create response embed
        embed = discord.Embed(
            color=discord.Color.green() if added_channels else discord.Color.orange()
        )
        
        if added_channels:
            embed.title = "✅ Channels Added"
            embed.description = f"Successfully added {len(added_channels)} channel(s) to the allowed list."
            embed.add_field(
                name="Added Channels",
                value="\n".join(added_channels) if added_channels else "None",
                inline=False
            )
        
        if already_allowed_channels:
            embed.add_field(
                name="Already Allowed",
                value="\n".join(already_allowed_channels) if already_allowed_channels else "None",
                inline=False
            )
            if not added_channels:
                embed.title = "⚠️ No New Channels Added"
                embed.color = discord.Color.orange()
        
        if failed_channels:
            embed.add_field(
                name="Failed to Add",
                value="\n".join(failed_channels) if failed_channels else "None",
                inline=False
            )
            embed.color = discord.Color.red()
        
        if not added_channels and not already_allowed_channels and not failed_channels:
            embed.title = "❌ No Channels Processed"
            embed.description = "No channels were processed. Please check your input."
            embed.color = discord.Color.red()
        
        embed.set_footer(text=f"Added by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    
    @commands.command(name='removeallowed')
    @is_administrator()
    async def removeallowed(self, ctx, channel: discord.TextChannel):
        """
        Remove a channel from the allowed list.
        
        Usage: -z removeallowed #channel-name
        """
        # Check if channel is in allowed list
        if not await database.is_channel_allowed(ctx.guild.id, channel.id):
            embed = discord.Embed(
                title="❌ Channel Not Allowed",
                description=f"{channel.mention} is not in the allowed list.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        # Remove channel from allowed list
        success = await database.remove_allowed_channel(ctx.guild.id, channel.id)
        
        if success:
            embed = discord.Embed(
                title="✅ Channel Removed",
                description=f"{channel.mention} has been removed from the allowed list.\n"
                          f"The bot will no longer respond to commands in this channel.",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Removed by {ctx.author.display_name}")
        else:
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to remove channel from allowed list.",
                color=discord.Color.red()
            )
        
        await ctx.send(embed=embed)
    
    @commands.command(name='listallowed')
    async def listallowed(self, ctx):
        """
        List all channels where the bot is allowed to respond.
        
        Usage: -z listallowed
        """
        allowed_channels = await database.get_allowed_channels(ctx.guild.id)
        
        if not allowed_channels:
            embed = discord.Embed(
                title="📋 Allowed Channels",
                description="No channels are configured. The bot responds in all channels.\n"
                          f"Use `-z allowchannel #channel` to restrict bot responses.",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
            return
        
        # Format the list
        channel_list = []
        for i, channel_data in enumerate(allowed_channels, 1):
            channel_id = channel_data["channel_id"]
            added_by = channel_data["added_by"]
            added_at = channel_data["added_at"]
            
            # Try to get channel mention
            channel = ctx.guild.get_channel(channel_id)
            channel_mention = channel.mention if channel else f"<#{channel_id}> (deleted)"
            
            # Try to get user who added
            user = ctx.guild.get_member(added_by)
            user_name = user.display_name if user else f"User {added_by}"
            
            # Format date
            try:
                date_obj = datetime.fromisoformat(added_at)
                date_str = date_obj.strftime("%Y-%m-%d")
            except:
                date_str = added_at
            
            channel_list.append(f"{i}. {channel_mention} - Added by **{user_name}** on {date_str}")
        
        embed = discord.Embed(
            title="📋 Allowed Channels",
            description="The bot will only respond to commands in these channels:\n\n" + 
                       "\n".join(channel_list),
            color=discord.Color.blue()
        )
        
        if len(allowed_channels) == 1:
            embed.add_field(
                name="Note",
                value="Only 1 channel is allowed. The bot will ignore commands in other channels.",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    @commands.command(name='clearallowed')
    @is_administrator()
    async def clearallowed(self, ctx, confirm: str = None):
        """
        Clear all allowed channels (bot will respond everywhere).
        
        Usage: -z clearallowed confirm
        """
        # Check for confirmation
        if confirm != "confirm":
            embed = discord.Embed(
                title="⚠️ Confirmation Required",
                description="This will remove ALL channel restrictions.\n"
                          f"The bot will respond in **all channels** after this.\n\n"
                          f"To confirm, type: `-z clearallowed confirm`",
                color=discord.Color.orange()
            )
            
            # Show current count
            allowed_channels = await database.get_allowed_channels(ctx.guild.id)
            if allowed_channels:
                embed.add_field(
                    name="Current Restrictions",
                    value=f"{len(allowed_channels)} channel(s) are currently restricted.",
                    inline=False
                )
            
            await ctx.send(embed=embed)
            return
        
        # Clear all allowed channels
        success = await database.clear_allowed_channels(ctx.guild.id)
        
        if success:
            embed = discord.Embed(
                title="✅ All Restrictions Cleared",
                description="All channel restrictions have been removed.\n"
                          f"The bot will now respond to commands in **all channels**.",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Cleared by {ctx.author.display_name}")
        else:
            embed = discord.Embed(
                title="❌ Error",
                description="Failed to clear channel restrictions.",
                color=discord.Color.red()
            )
        
        await ctx.send(embed=embed)
    
    @allowchannel.error
    @removeallowed.error
    @clearallowed.error
    async def admin_command_error(self, ctx, error):
        """Handle errors for admin-only commands."""
        if isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                title="❌ Permission Denied",
                description="You need **Administrator** permissions to use this command.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="❌ Missing Argument",
                description=f"Usage: `{ctx.command.signature}`\n\n"
                          f"Example: `-z {ctx.command.name} #channel-name`",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        elif isinstance(error, commands.BadArgument):
            embed = discord.Embed(
                title="❌ Invalid Channel",
                description="Please mention a valid text channel.\n"
                          f"Example: `-z {ctx.command.name} #general`",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
        else:
            # Log other errors
            print(f"Error in {ctx.command.name}: {error}")
            embed = discord.Embed(
                title="❌ Unexpected Error",
                description="An unexpected error occurred. Please try again.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)

async def setup(bot):
    """Add the cog to the bot."""
    await bot.add_cog(LocalServerConfig(bot))