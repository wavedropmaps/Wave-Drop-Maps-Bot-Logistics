import discord
from discord.ext import commands
import logging

logger = logging.getLogger('discord')

MANAGEMENT_ROLES = ('Management', '007', '+')

def is_authorized():
    async def predicate(ctx):
        if ctx.author.guild_permissions.administrator:
            return True
        role_names = {r.name for r in ctx.author.roles}
        if role_names & set(MANAGEMENT_ROLES):
            return True
        raise commands.CheckFailure("You need **Administrator** or a **Management** role to use this command.")
    return commands.check(predicate)

PAGES = [
    {
        "title": "📖 Help — Page 1/11 | Configuration",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "⚙️ Configuration Commands",
                "value": (
                    "These commands configure the bot for your server. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z setpriority @role`",
                "value": "Sets the role the bot will track as the **Priority** role for this server.",
                "inline": False
            },
            {
                "name": "`-z setcontributor @role`",
                "value": "Sets the role the bot will track as the **Contributor** role for this server.",
                "inline": False
            },
            {
                "name": "`-z setstreakinfo #channel`",
                "value": (
                    "Sets the channel where the streak info overview and leaderboard are posted. "
                    "Both messages are sent automatically on startup and kept up to date."
                ),
                "inline": False
            },
            {
                "name": "`-z setservertype <drop_maps|loot_routes>`",
                "value": (
                    "Sets the server type which controls streak perk descriptions shown in the info embed and `/streak` command.\n"
                    "`drop_maps` — for drop map servers *(default)*\n"
                    "`loot_routes` — for loot route servers"
                ),
                "inline": False
            },
            {
                "name": "`-z servertype`",
                "value": "Shows the current server type configured for this server.",
                "inline": False
            },
            {
                "name": "`-z refreshstreak`",
                "value": (
                    "Manually refreshes the streak info overview and leaderboard messages in the configured channel. "
                    "Useful after changing the server type without restarting the bot."
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 2/11 | Manual Control",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "🎛️ Manual Control Commands",
                "value": (
                    "These commands let you manually manage tracked users. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z listexpiry <priority|contributor>`",
                "value": (
                    "Lists all tracked users of that role type in this server along with "
                    "their expiry date and how many days are left."
                ),
                "inline": False
            },
            {
                "name": "`-z setexpiry <priority|contributor> [@user] <DD/MM/YYYY HH:MM>`",
                "value": (
                    "Sets the expiry date for a **single user** or **all** tracked users of that role type.\n"
                    "Example (all): `-z setexpiry priority 25/04/2026 18:00`\n"
                    "Example (one): `-z setexpiry priority @Wave 25/04/2026 18:00`"
                ),
                "inline": False
            },
            {
                "name": "`-z addtracked <priority|contributor> @user [DD/MM/YYYY HH:MM]`",
                "value": (
                    "Manually adds a user to tracking. If no date is provided the timer "
                    "starts from now and expires in 30 days.\n"
                    "Example: `-z addtracked priority @Wave 01/04/2026 12:00`"
                ),
                "inline": False
            },
            {
                "name": "`-z addtrackedall <priority|contributor> [DD/MM/YYYY HH:MM]`",
                "value": (
                    "Adds **all members** with the configured role to tracking at once. "
                    "Optional date sets a custom start time.\n"
                    "Example: `-z addtrackedall contributor 01/04/2026 12:00`"
                ),
                "inline": False
            },
            {
                "name": "`-z removetracked <priority|contributor> @user`",
                "value": (
                    "Removes a user from tracking without touching their Discord role.\n"
                    "Example: `-z removetracked contributor @Wave`"
                ),
                "inline": False
            },
            {
                "name": "`-z removetrackedall <priority|contributor>`",
                "value": (
                    "Removes **all** tracked users of that role type from tracking without touching their Discord roles.\n"
                    "Example: `-z removetrackedall priority`"
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 3/11 | Member Commands",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "👤 Member Commands",
                "value": (
                    "These slash commands are available to members who hold a **Priority** or **Contributor** role."
                ),
                "inline": False
            },
            {
                "name": "`/status`",
                "value": (
                    "Check your own or another user's subscription status — shows active **Priority** and/or **Contributor** "
                    "role timers, start date, expiry date, days remaining, and a progress bar.\n"
                    "Add an optional user parameter (username, ID, or mention) to check someone else's status.\n"
                    "Only visible to you. Only works if the user currently holds a tracked role."
                ),
                "inline": False
            },
            {
                "name": "`/streak`",
                "value": (
                    "See how many times you or another user has supported Wave and your current milestone badge.\n"
                    "Shows total months supported, first support date, and unlocked/upcoming perks "
                    "for both **Priority** and **Contributor** roles.\n"
                    "Add an optional user parameter (username, ID, or mention) to check someone else's streak.\n"
                    "Only visible to you. Only works if the user currently holds a tracked role."
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 4/11 | How It Works",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "🔄 How the Bot Works",
                "value": (
                    "The bot automatically tracks users who are given the Priority or "
                    "Contributor role and manages their 30 day timer."
                ),
                "inline": False
            },
            {
                "name": "✅ Role Assigned",
                "value": (
                    "When a user is given a tracked role the bot adds them to the database, "
                    "increments their streak, and sends them a DM explaining their perks and role duration."
                ),
                "inline": False
            },
            {
                "name": "⚠️ Day 27 — Expiry Warning",
                "value": (
                    "3 days before the role expires the user receives a DM warning them to "
                    "renew before it is removed."
                ),
                "inline": False
            },
            {
                "name": "❌ Day 30 — Role Removed",
                "value": (
                    "On day 30 the role is automatically removed, the user receives a DM, "
                    "and the event is logged in your configured log channel."
                ),
                "inline": False
            },
            {
                "name": "🏅 Streaks",
                "value": (
                    "Each time a user is assigned a tracked role their streak count goes up by 1. "
                    "Milestones unlock perks — use `/streak` to check progress. "
                    "The streak info and leaderboard are posted automatically on startup in the configured channel."
                ),
                "inline": False
            },
            {
                "name": "🔁 Startup Audit",
                "value": (
                    "Every time the bot starts up it scans the last 24 hours of audit logs "
                    "to catch any role assignments it may have missed while offline."
                ),
                "inline": False
            },
            {
                "name": "📨 DM Rate Limiting",
                "value": (
                    "When sending bulk DMs the bot sends in batches of 10 with a pause "
                    "between each batch to avoid Discord rate limits."
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 5/11 | Map Request Queue",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "🗺️ Map Request Queue Commands",
                "value": (
                    "These commands manage the map request queue system. "
                    "`-z addmap` requires **Map Request Helper** role."
                ),
                "inline": False
            },
            {
                "name": "`-z addmap` (Interactive Mode)",
                "value": (
                    "Add a map request to the queue. Opens a dropdown menu to choose between:\n"
                    "• **Existing queue code** - Add to existing queue entry (use alphabetical codes like a, b, c)\n"
                    "• **New request** - Create new queue entry\n\n"
                    "**Flow for new requests:**\n"
                    "1. **Image** (URL or attachment)\n"
                    "2. **User IDs** (space-separated IDs or mentions)\n"
                    "3. **Game mode** (for loot route config) or map type\n"
                    "4. **Description** (optional, can be left blank)\n\n"
                    "**Map types:** drop_map, loot_route, other\n"
                    "**Note:** Description field is left blank if not provided."
                ),
                "inline": False
            },
            {
                "name": "`-z addmap new ...` (Automated Mode)",
                "value": (
                    "Add a map directly without the dropdown menu. Used by bots (like Wave Management Bot's voting system) for bot-to-bot integration.\n\n"
                    "**Format:**\n"
                    "`-z addmap new --spot-name \"Name\" --image URL --users ID1 ID2 --description \"Desc\"`\n\n"
                    "**Parameters (all required):**\n"
                    "• `--spot-name \"Name\"` - Map/spot name (quoted)\n"
                    "• `--image URL` - Image URL (no quotes)\n"
                    "• `--users ID1 ID2` - Space-separated user IDs or mentions\n"
                    "• `--description \"Desc\"` - Description (quoted)"
                ),
                "inline": False
            },
            {
                "name": "`-z removequeue <queue_code>`",
                "value": "Remove a map request from the queue by its alphabetical code (a, b, c, etc.). Requires **Map Request Helper** role.\n\n**Tip:** Queue codes are shown in the queue display messages in the embed footer. If you forget the code, check the queue channel.",
                "inline": False
            },
            {
                "name": "`-z setqueuechannel #channel`",
                "value": "Set the channel where queue entries are displayed. Requires **Administrator** or **Management** role.",
                "inline": False
            },
            {
                "name": "`-z setconfigqueue <drop_map|loot_route>`",
                "value": "Set server mode for queue display (affects default map type and priority calculation).\n\n**Modes:** drop_map, loot_route, other\n**Examples:**\n`-z setconfigqueue drop_map` - Set to drop map mode\n`-z setconfigqueue loot_route` - Set to loot route mode\n\n**Note:** Queue notifications are controlled separately with `-z setqueuenotifications`",
                "inline": False
            },
            {
                "name": "`-z setqueuenotifications <enable|disable>`",
                "value": "Enable or disable queue notification DMs when map requests are added to queue.\n\n**Options:** enable, disable\n**Examples:**\n`-z setqueuenotifications enable` - Enable notifications\n`-z setqueuenotifications disable` - Disable notifications",
                "inline": False
            },
            {
                "name": "`-z testqueuenotify <queue_code>`",
                "value": "Test queue notification DMs for a specific queue entry. Sends DMs to all users in that request as if it was just added to queue.\n\n**Example:** `-z testqueuenotify a` - Test notifications for queue entry 'a'",
                "inline": False
            },
            {
                "name": "`-z queueconfig`",
                "value": "Show current queue configuration for this server.",
                "inline": False
            },
            {
                "name": "`-z clearqueue [confirm]`",
                "value": "**Admin only**: Clear all map requests and reset queue number counter. Use `-z clearqueue yes` to confirm.",
                "inline": False
            },
            {
                "name": "📊 Queue Display Features",
                "value": (
                    "Each queue entry gets its own message in the configured channel with:\n"
                    "• **Map Request Queue Number (X)** - Priority-based display number\n"
                    "• **Alphabetical queue code** - Shown in footer (e.g., Code: a)\n"
                    "• **User mentions** (@user)\n"
                    "• **Image preview** (if provided)\n"
                    "• **Clean description field** - Left blank if no description provided\n"
                    "• **Priority ranking** - Shows highest priority level with role mention (e.g., Role: @Silver (LVL 10))"
                ),
                "inline": False
            },
            {
                "name": "🎯 Priority System Overview",
                "value": (
                    "The bot automatically calculates priority based on user roles. "
                    "Queue ordering: Highest priority first, then by creation time (oldest first) for tiebreakers. "
                    "Database storage: Uses alphabetical codes (a, b, c, ..., aa, ab) to avoid conflicts."
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 6/11 | DM System",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "📨 DM System Commands",
                "value": (
                    "These commands configure the DM notification system for completed map requests. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z setdmchannel #channel`",
                "value": (
                    "Set the channel where users can type queue codes to trigger DM notifications.\n"
                    "When a user types a queue code in brackets (like '(a)', '[b]', '{c}') in this channel, the bot will DM all users associated with that map request."
                ),
                "inline": False
            },
            {
                "name": "`-z setdmlogchannel #channel`",
                "value": (
                    "Set the channel where DM success/failure logs are posted.\n"
                    "After sending DMs for a queue code, the bot posts success/failure messages here showing who was DM'd and for what map.\n"
                    "**Note:** Success/failure messages only appear in this log channel, not in the DM trigger channel."
                ),
                "inline": False
            },
            {
                "name": "`-z dmconfig`",
                "value": "Show current DM configuration for this server. DM templates are hardcoded.",
                "inline": False
            },
            {
                "name": "`-z setdmenabled <enable|disable>`",
                "value": (
                    "Enable or disable the DM system for this server.\n"
                    "Options: enable, disable\n"
                    "Examples: `-z setdmenabled enable` - Enable DM system (sets system to enabled)\n"
                    "`-z setdmenabled disable` - Disable DM system (sets system to disabled)\n"
                    "**Note:** The DM processor checks if the system is enabled before processing any queue codes."
                ),
                "inline": False
            },
            {
                "name": "`-z senddm <queue_code>`",
                "value": (
                    "Manually trigger DM sending for a specific queue code.\n"
                    "Example: `-z senddm a` - Sends DMs to all users in queue entry 'a'"
                ),
                "inline": False
            },
            {
                "name": "🔄 DM System Workflow",
                "value": (
                    "1. **Configure channels** with `-z setdmchannel` and `-z setdmlogchannel`\n"
                    "2. **User types queue code** in brackets (like '(a)', '[b]', '{c}') in the DM trigger channel\n"
                    "3. **Bot automatically extracts** channel link from the message (channel mention or URL)\n"
                    "4. **Bot checks** if DM system is enabled for the server\n"
                    "5. **Bot sends DMs** to all users in that request using hardcoded templates\n"
                    "6. **Rate limiting**: 3 second pause between each DM to avoid Discord limits\n"
                    "7. **Success/failure logs**: Bot posts results in log channel only (not trigger channel)\n"
                    "8. **Failure handling**: Failed DMs show retry/delete buttons in log channel\n"
                    "9. **Cleanup**: Queue entry is deleted from database and Discord"
                ),
                "inline": False
            },
            {
                "name": "📝 DM Templates (Hardcoded)",
                "value": (
                    "**Drop Map Template:**\n"
                    "Hey {mention} :wave:\n\n"
                    "Your **requested dropmap** is complete, you can find it over at {link}\n\n"
                    "If you want wave to keep **making more free drop maps**, you can **support wave **by giving a vouch in https://discord.com/channels/988564962802810961/1210814682357698621 and say something you like about the server!\n\n"
                    "Any and all vouches are** greatly appreciated**, thank you for choosing wave!\n\n"
                    "**Loot Route Template:**\n"
                    "Hey {mention} :wave:\n\n"
                    "Your **requested loot route** is complete, you can find it over at {link}\n\n"
                    "If you want wave to keep **making more free loot routes**, you can **support wave **by giving a vouch in https://discord.com/channels/971731167621574666/1132639885883359302 and say something you like about the server!\n\n"
                    "Any and all vouches are** greatly appreciated**, thank you for choosing wave!\n\n"
                    "**Placeholders:** {mention} = user mention, {link} = channel link extracted from the message"
                ),
                "inline": False
            },
            {
                "name": "🔧 Key Features",
                "value": (
                    "• **Queue code detection**: Only processes codes in brackets `(a)`, `[b]`, `{c}`\n"
                    "• **Automatic channel extraction**: Extracts channel links from mentions or URLs\n"
                    "• **Failure recovery**: Retry/delete buttons for failed DMs in log channel\n"
                    "• **No trigger channel spam**: Success/failure messages only in log channel\n"
                    "• **Enable/disable control**: DM system can be turned on/off per server\n"
                    "• **Rate limiting**: 3 second delay between DMs to avoid Discord limits"
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 7/11 | Local Server Configuration",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "⚙️ Local Server Configuration Commands",
                "value": (
                    "These commands configure which channels the bot can respond in. "
                    "If no channels are configured, the bot responds in all channels. "
                    "Requires **Administrator** permissions."
                ),
                "inline": False
            },
            {
                "name": "`-z allowchannel #channel1 #channel2 ...`",
                "value": (
                    "Add one or more channels to the allowed list where the bot can respond.\n"
                    "After adding channels, the bot will only respond to commands in those channels.\n"
                    "**Examples:**\n"
                    "• `-z allowchannel #bot-commands` (single channel)\n"
                    "• `-z allowchannel #bot-commands #general #support` (multiple channels)"
                ),
                "inline": False
            },
            {
                "name": "`-z removeallowed #channel`",
                "value": (
                    "Remove a channel from the allowed list.\n"
                    "The bot will no longer respond to commands in that channel.\n"
                    "**Example:** `-z removeallowed #general`"
                ),
                "inline": False
            },
            {
                "name": "`-z listallowed`",
                "value": (
                    "List all channels where the bot is allowed to respond.\n"
                    "Shows channel mentions, who added them, and when.\n"
                    "If no channels are listed, the bot responds in all channels."
                ),
                "inline": False
            },
            {
                "name": "`-z clearallowed confirm`",
                "value": (
                    "Clear ALL channel restrictions. The bot will respond in **all channels**.\n"
                    "Requires confirmation: `-z clearallowed confirm`\n"
                    "**Warning:** This cannot be undone!"
                ),
                "inline": False
            },
            {
                "name": "📝 How Channel Restrictions Work",
                "value": (
                    "1. **No restrictions:** If no channels are configured, bot responds everywhere\n"
                    "2. **With restrictions:** Bot only responds in allowed channels\n"
                    "3. **DM channels:** Always allowed (bot responds to DMs)\n"
                    "4. **Administrators:** Can always use commands in any channel\n"
                    "5. **Error message:** If command used in non-allowed channel, shows list of allowed channels"
                ),
                "inline": False
            },
            {
                "name": "🔧 Use Cases",
                "value": (
                    "• **Restrict bot to specific channels** like #bot-commands\n"
                    "• **Prevent spam** in general channels\n"
                    "• **Organize bot usage** to dedicated channels\n"
                    "• **Temporary restrictions** during events or maintenance"
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 8/11 | AntiNuke",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "🛡️ AntiNuke Commands",
                "value": (
                    "These commands configure the antinuke system. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z enableantinuke`",
                "value": "Enables the antinuke system for this server.",
                "inline": False
            },
            {
                "name": "`-z disableantinuke`",
                "value": "Disables the antinuke system for this server.",
                "inline": False
            },
            {
                "name": "`-z setquarantine @role`",
                "value": "Sets the role that gets assigned to anyone detected nuking.",
                "inline": False
            },
            {
                "name": "`-z setantinukelog #channel`",
                "value": "Sets the channel where antinuke detections are logged.",
                "inline": False
            },
            {
                "name": "`-z whitelist <add|remove|list> [@user|user_id|@role]`",
                "value": (
                    "Manage trusted users who are exempt from antinuke checks. "
                    "Accepts @mentions, user IDs, and @roles (adds all members of that role).\n"
                    "Example: `-z whitelist add @Wave 123456789 @Staff`"
                ),
                "inline": False
            },
            {
                "name": "`-z clearwhitelist`",
                "value": "Removes **all** users from the antinuke whitelist at once.",
                "inline": False
            },
            {
                "name": "`-z weightedwhitelist <add|remove|list> [@user|user_id]`",
                "value": (
                    "Manage users who get **50% higher thresholds** before being quarantined — "
                    "trusted users who shouldn't be instantly caught but aren't fully immune.\n"
                    "Example: `-z weightedwhitelist add @Wave 123456789`"
                ),
                "inline": False
            },
            {
                "name": "`-z antinukeinfo`",
                "value": "Shows the current antinuke configuration for this server.",
                "inline": False
            },
            {
                "name": "⚡ Triggers (standard → weighted)",
                "value": (
                    "`@everyone` pings: **3**/min/hr/day → **5**/min/hr/day\n"
                    "Channel deletions: **3**/min · **5**/hr · **7**/day → **5** · **8** · **11**\n"
                    "Role deletion: instant → 2nd deletion\n"
                    "Role permission changes: instant → 2nd change\n"
                    "Mass bans/kicks: **100**/min → **150**/min"
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 9/11 | Auto Join Ghost-Ping",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "👋 Auto Join Ghost-Ping",
                "value": (
                    "When a new member joins, the bot ghost-pings them (mention + immediate delete) "
                    "in configured channels so they get an unread notification. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z enableautojoinping`",
                "value": "Enable the ghost-ping-on-join system for this server.",
                "inline": False
            },
            {
                "name": "`-z disableautojoinping`",
                "value": "Disable the system. Configured channels are preserved.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingadd #channel [#channel2 ...]`",
                "value": "Add one or more channels where new members will be ghost-pinged. Bot needs View Channel, Send Messages, and Manage Messages in each.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingremove #channel [#channel2 ...]`",
                "value": "Remove channels from the ghost-ping list.",
                "inline": False
            },
            {
                "name": "`-z autojoinpinglist`",
                "value": "List all configured ghost-ping channels and their permission status.",
                "inline": False
            },
            {
                "name": "`-z autojoinpinglogchannel #channel`",
                "value": "Set the channel where ghost-ping events are logged. Use `none` to clear.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingdelay <ms>`",
                "value": "How long to wait between sending and deleting the ping (100–5000 ms). Default: 1000 ms. Run without argument to see current value.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingbatch <ms>`",
                "value": "Batch window — how long to gather join events before sending pings (100–5000 ms). Default: 800 ms.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingcooldown <seconds>`",
                "value": "Don't re-ping a member who rejoins within this many seconds (0–86400). Default: 60 s.",
                "inline": False
            },
            {
                "name": "`-z autojoinpingstatus`",
                "value": "Show current config, timing settings, and all-time stats (total members pinged, total batches).",
                "inline": False
            },
            {
                "name": "`-z autojoinpingtest [@member]`",
                "value": "Simulate a join ghost-ping in all configured channels. Targets you if no member is provided. Bypasses the cross-bot claim system.",
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 10/11 | Proof & Invite Utilities",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "📁 Proof Archival",
                "value": (
                    "Watches a designated channel and downloads attachments from messages "
                    "when a member with the **Role Giver** role replies to them. "
                    "Files are saved to `proof_assets/<guild>/<date>/<user>/`. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z setproofchannel #channel`",
                "value": "Set the channel to watch for proof submissions. Also enables archival for this server.",
                "inline": False
            },
            {
                "name": "`-z clearproofchannel`",
                "value": "Clear the proof channel and disable archival for this server.",
                "inline": False
            },
            {
                "name": "`-z proofstatus`",
                "value": "Show proof archival config — active state, channel, total messages saved, and last save time.",
                "inline": False
            },
            {
                "name": "🤖 Proof Automation",
                "value": (
                    "Automated YOLO-based proof processing for servers configured in the bot. "
                    "Watches a channel for image submissions and classifies them automatically."
                ),
                "inline": False
            },
            {
                "name": "`-z prooftoggle`",
                "value": "Toggle proof automation on or off for this server. Only works on servers pre-configured in the bot.",
                "inline": False
            },
            {
                "name": "`-z proofautostatus`",
                "value": "Show proof automation status — enabled state, watch channel, and active detection classes.",
                "inline": False
            },
            {
                "name": "🔗 Invite Auto-Purge",
                "value": (
                    "Automatically deletes stale invites on a schedule. "
                    "Requires **Administrator** permissions."
                ),
                "inline": False
            },
            {
                "name": "`-z invitedelete`",
                "value": (
                    "Toggle invite auto-purge on or off for this server.\n"
                    "**Purge rules:**\n"
                    "• 2-day expiry with 1 or fewer uses\n"
                    "• 4-day expiry with 0 uses\n"
                    "• Infinite invites older than 2 weeks with 0 uses\n"
                    "*(Bot-created invites are excluded)*"
                ),
                "inline": False
            },
        ]
    },
    {
        "title": "📖 Help — Page 11/11 | HITL Review Queue",
        "color": discord.Color.blurple(),
        "fields": [
            {
                "name": "🧹 HITL Review Queue Commands",
                "value": (
                    "Inspect and clear stale or bugged human-in-the-loop (HITL) proof "
                    "reviews from the queue. Use these when a review card is stuck, "
                    "abandoned, or no longer needed. "
                    "Requires **Administrator** or a **Management** role."
                ),
                "inline": False
            },
            {
                "name": "`-z reviewqueue`",
                "value": (
                    "List all pending (unresolved) reviews in this server — shows each "
                    "review's message id, who submitted it, how long it's been waiting, "
                    "claim status, and a jump link.\n"
                    "*(aliases: `-z reviewpending`, `-z pendingreviews`)*"
                ),
                "inline": False
            },
            {
                "name": "`-z clearreview <message_id>`",
                "value": (
                    "Clear **one** stale review card by its message id (find ids with "
                    "`-z reviewqueue`). Marks it resolved, deletes the card, and refreshes "
                    "the queue sticky.\n"
                    "*(alias: `-z reviewclear`)*"
                ),
                "inline": False
            },
            {
                "name": "`-z clearreviewqueue confirm`",
                "value": (
                    "Clear **all** pending reviews in this server at once. Requires the "
                    "word `confirm` as a safety guard — running it without `confirm` shows "
                    "how many would be cleared first.\n"
                    "*(alias: `-z reviewqueueclear`)*"
                ),
                "inline": False
            },
            {
                "name": "ℹ️ Does clearing affect staff stats?",
                "value": (
                    "No. Cleared reviews are logged as an audit-only `review_cleared` event "
                    "and are **never** counted toward a staff member's *Reviews Completed* "
                    "total on the Management website. Only genuinely finished reviews count."
                ),
                "inline": False
            },
        ]
    },
]

class HelpView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=120)
        self.ctx = ctx
        self.page = 0

    def build_embed(self):
        page_data = PAGES[self.page]
        embed = discord.Embed(title=page_data["title"], color=page_data["color"])
        for field in page_data["fields"]:
            embed.add_field(name=field["name"], value=field["value"], inline=field["inline"])
        embed.set_footer(text=f"Wave Free Drop Maps Bot | Page {self.page + 1} of {len(PAGES)}")
        return embed

    async def update(self, interaction: discord.Interaction):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page == len(PAGES) - 1
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary, disabled=True)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Only the person who ran this command can navigate.", ephemeral=True)
            return
        self.page -= 1
        await self.update(interaction)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.ctx.author:
            await interaction.response.send_message("Only the person who ran this command can navigate.", ephemeral=True)
            return
        self.page += 1
        await self.update(interaction)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='help')
    @is_authorized()
    async def help_command(self, ctx):
        view = HelpView(ctx)
        await ctx.send(embed=view.build_embed(), view=view)
        logger.info(f"{ctx.author} used help command in {ctx.guild.name}")

async def setup(bot):
    await bot.add_cog(Help(bot))
    logger.info("✅ Help cog loaded")