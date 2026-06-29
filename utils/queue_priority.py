"""
Priority ranking and sorting logic for map request queue.
Handles priority-based ordering and display number assignment.
"""

import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
import Database.database_improved as database
from .queue_encoding import number_to_alpha, alpha_to_number, sort_alpha_codes

async def calculate_request_priority(guild, server_mode: str, user_ids: List[int]) -> Tuple[Optional[int], Optional[str], Optional[int]]:
    """
    Calculate priority for a map request based on user roles.
    Returns: (priority_level, priority_role_name, priority_user_id)
    Lower priority_level = higher priority (1 is highest)
    """
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
                    import discord
                    role = discord.utils.get(member.roles, name=role_name)
                    if role:
                        # Found a role at this priority level
                        if highest_priority_level is None or level < highest_priority_level:
                            highest_priority_level = level
                            highest_priority_role = role_name
                            highest_priority_user = user_id
                        break  # No need to check other roles at same level
                
                # If we found a role at this level, don't check lower levels for this user
                if highest_priority_level == level:
                    break
        
        except Exception as e:
            print(f"Error checking priority for user {user_id}: {e}")
            continue
    
    return highest_priority_level, highest_priority_role, highest_priority_user

async def get_sorted_map_requests(guild, server_mode: str) -> List[Dict[str, Any]]:
    """
    Get all map requests sorted by priority and creation time.
    Returns list of requests with added 'display_number' and 'priority_level' fields.
    """
    # Get all active map requests
    map_requests = await database.get_all_map_requests(guild.id, status='active')
    
    # Calculate priority for each request
    requests_with_priority = []
    for req in map_requests:
        user_ids = req.get("user_ids", [])
        priority_level, priority_role, priority_user = await calculate_request_priority(
            guild, server_mode, user_ids
        )
        
        # Convert alpha code to number for sorting if needed
        queue_code = req.get("queue_number")
        if isinstance(queue_code, str) and queue_code.isalpha():
            # It's an alpha code, convert to number for consistent sorting
            try:
                alpha_order = alpha_to_number(queue_code)
            except:
                alpha_order = 0
        else:
            # It's a number or something else
            try:
                alpha_order = int(queue_code) if queue_code else 0
            except:
                alpha_order = 0
        
        requests_with_priority.append({
            **req,
            "priority_level": priority_level if priority_level is not None else 999,
            "priority_role": priority_role,
            "priority_user": priority_user,
            "alpha_order": alpha_order,
            # Fallback must be tz-AWARE: created_at values are UTC-aware, and
            # sorted() raises TypeError when aware and naive datetimes mix.
            "created_at_dt": datetime.fromisoformat(req["created_at"]) if req.get("created_at") else datetime.min.replace(tzinfo=timezone.utc)
        })
    
    # Sort by: priority level (ascending), then creation time (ascending)
    sorted_requests = sorted(
        requests_with_priority,
        key=lambda x: (
            x["priority_level"],  # Lower number = higher priority
            x["created_at_dt"]    # Older requests first for tiebreaker
        )
    )
    
    # Assign display numbers (1-based)
    for i, req in enumerate(sorted_requests, 1):
        req["display_number"] = i
    
    return sorted_requests

async def get_next_alpha_queue_code(guild_id: int) -> str:
    """
    Get the next available alphabetical queue code for a guild.
    Uses existing queue codes to determine the next one.
    """
    # Get all active map requests for this guild
    map_requests = await database.get_all_map_requests(guild_id, status='active')
    
    # Extract alpha codes
    alpha_codes = []
    for req in map_requests:
        queue_code = req.get("queue_number")
        if isinstance(queue_code, str) and queue_code.isalpha():
            alpha_codes.append(queue_code.lower())
        elif isinstance(queue_code, int):
            # Convert number to alpha code
            alpha_codes.append(number_to_alpha(queue_code))
    
    # Sort codes to find the highest
    if not alpha_codes:
        return 'a'
    
    # Convert all codes to numbers, find max
    try:
        numbers = [alpha_to_number(code) for code in alpha_codes]
        max_num = max(numbers)
        return number_to_alpha(max_num + 1)
    except:
        # Fallback: find next code alphabetically
        sorted_codes = sort_alpha_codes(alpha_codes)
        last_code = sorted_codes[-1]
        
        # Simple increment: 'a' -> 'b', 'z' -> 'aa', etc.
        return _increment_alpha_code(last_code)

def _increment_alpha_code(code: str) -> str:
    """Increment an alphabetical code: 'a' -> 'b', 'z' -> 'aa', 'az' -> 'ba'."""
    if not code:
        return 'a'
    
    # Convert to list of characters
    chars = list(code.lower())
    
    # Increment from rightmost character
    i = len(chars) - 1
    while i >= 0:
        if chars[i] < 'z':
            chars[i] = chr(ord(chars[i]) + 1)
            break
        else:
            chars[i] = 'a'
            i -= 1
    
    # If we carried over past the first character
    if i < 0:
        chars.insert(0, 'a')
    
    return ''.join(chars)

async def update_queue_display_numbers(guild, server_mode: str) -> Dict[str, int]:
    """
    Update display numbers for all map requests based on current priority.
    Returns mapping of queue_code -> display_number.
    """
    sorted_requests = await get_sorted_map_requests(guild, server_mode)
    
    # Create mapping
    display_mapping = {}
    for req in sorted_requests:
        queue_code = req.get("queue_number")
        display_number = req.get("display_number")
        if queue_code and display_number:
            display_mapping[str(queue_code)] = display_number
    
    return display_mapping

# Test the functions
if __name__ == "__main__":
    # Mock test - can't run without Discord bot
    print("Queue priority module loaded successfully.")
    print("Functions available:")
    print("  - calculate_request_priority(guild, server_mode, user_ids)")
    print("  - get_sorted_map_requests(guild, server_mode)")
    print("  - get_next_alpha_queue_code(guild_id)")
    print("  - update_queue_display_numbers(guild, server_mode)")