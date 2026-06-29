---
name: map-queue
description: Priority-based map-request queue — tiers, sort key, DROP MAP vs LOOT ROUTE, rendering, image durability
tags: [map-queue, priority-sorting, rendering, drop-map, loot-route]
related: [background-tasks, automation-tree, hitl-review-queue]
---
# Map Request Queue
> Referenced from `AGENTS.md` → Codebase Map. The priority-based map-request queue: tiers, sort key, DROP MAP vs LOOT ROUTE, embed/sticky rendering, and image-URL durability.

### Priority Sorting (NOT FIFO)
- The queue is ordered by **priority tier first, creation time second** — it is explicitly NOT first-in-first-out. A late high-tier request outranks an older low-tier one.
- Exact sort key (`utils/queue_priority.py:126-131`): `sorted(requests, key=lambda x: (x["priority_level"], x["created_at_dt"]))`. Both keys are ascending: lower `priority_level` = higher priority; older `created_at` breaks ties (FIFO only *within* a tier).
- `priority_level` is set from the highest-ranking role the request's users hold; if no priority role matches it defaults to **999** (sorts to the bottom) (`queue_priority.py:116-123`).
- Each request is also tagged `priority_role` (name of the winning role), `priority_user` (the user_id that earned it), and after sorting a 1-based `display_number` (`queue_priority.py:135-136`).
- `get_sorted_map_requests(guild, server_mode)` (`queue_priority.py:83`) returns the fully sorted list with display/priority fields; `update_queue_display_numbers(guild, server_mode)` (`queue_priority.py:199`) returns a `queue_code → display_number` mapping for renumbering.

### Priority Tiers — DROP MAP server mode
- Tier order (lower = higher), from `queue_priority.py:22-31`:
  - **Level 1:** Paid Priority
  - **Level 2:** Wave Contributor
  - **Level 3:** Unreal (LVL 50)
  - **Level 4:** Elite (LVL 30), Active
  - **Level 5:** Silver (LVL 10), Staff, Drop Map Tester, Map Creator, Loot Route Map Creator, Tips and Tricks Helper, Promoters, Drop Map Reviewer

### Priority Tiers — LOOT ROUTE server mode
- Tier order (lower = higher), from `queue_priority.py:32-42`:
  - **Level 1:** Server Booster, Wave Contributor
  - **Level 2:** Battle Pass Supporter
  - **Level 3:** Unreal (LVL 50)
  - **Level 4:** Elite (LVL 30), Active
  - **Level 5:** Bronze (LVL 5), Staff, Drop Map Tester, Map Creator, Loot Route Map Creator, Tips and Tricks Helper, Promoters, Drop Map Reviewer
  - **Level 6:** Access, Access Invite way

### DROP MAP vs LOOT ROUTE
- Server mode is per-guild, stored in `server_queue_config.server_mode` and read in the add wizard as `config.get("server_mode", "drop_map")` — default is **drop_map** (`map_commands.py:374`).
- **DROP MAP:** single implicit type; game mode is auto-set to `"drop_map"`; no route-type step; limit of 1 active request per person; shows the sticky message with role highlights.
- **LOOT ROUTE:** the wizard adds a route-type selection step — **loot_route vs surge_route** (`map_commands.py:516-559`) — and a user-entered game mode (Duos/Solos/etc.); a person may hold 1 loot_route + 1 surge_route concurrently; no sticky message.
- `route_type` (`'loot_route'`/`'surge_route'`) and `map_type` are persisted on the `map_requests` row (`database_improved.py:79-94`).

### Queue Codes (alpha encoding)
- Queue entries are addressed by **alphabetical codes**, not raw numbers, via `utils/queue_encoding.py`.
- `number_to_alpha(n)` (`queue_encoding.py:8-23`) is Excel-style base-26: 1→`a` … 26→`z`, 27→`aa`, 28→`ab`, 702→`zz`, 703→`aaa`. `alpha_to_number` (`queue_encoding.py:25-38`) is the inverse (case-insensitive, whitespace-stripped).
- `sort_alpha_codes` (`queue_encoding.py:56-61`) sorts codes by their numeric value (`['c','a','b','aa'] → ['a','b','c','aa']`), so multi-letter codes order correctly rather than lexically.
- These codes are display/identity only — they are not used to store images or persist data.

### Add Wizard & Request Creation
- Driven by `MapRequestView` (`map_commands.py:157+`); commands include `-z addmap`, `-z removequeue`, `-z setchannel`, `-z setconfigqueue`, `-z queueconfig`.
- New-request flow: choose "new" → upload image → enter user IDs → (loot_route only) pick route type → enter game mode → enter description (`map_commands.py:370-651`).
- Existing-request flow: choose "existing" → pick queue code from dropdown → update user IDs / mode / description (`map_commands.py:335-460`).
- The next code is reserved via `database.get_next_queue_number(guild.id)` (`map_commands.py:371`).

### Image-URL Durability
- **Problem:** Discord CDN attachment URLs expire (~24h), so a stored `image_url` goes blank after a day.
- **Mitigation:** on upload, the attachment bytes are downloaded (`attachment.read()`) and written to a permanent local file at `queue_images/<guild_id>/<queue_code>.<ext>` (`map_commands.py:39-69`). Allowed extensions: `.png, .jpg, .jpeg, .webp, .gif`. `save_queue_image` deletes any stale copy with a different extension before writing.
- The wizard keeps the **full** Discord URL *including* query parameters (`map_commands.py:665-669`) — the signature params are required for the embed to render the image; truncating them breaks display.
- `find_queue_image` (`map_commands.py:72-78`) locates the saved file by code, globbing on a literal `.` so `a` does not false-match `ab`. The saved bytes are re-attached to the queue message as a fresh Discord attachment, which sidesteps CDN expiry.

### Sticky / Queue Embed Rendering & Refresh
- `refresh_sticky_message` (`map_commands.py:85-155`) maintains a single sticky at the bottom of the queue channel (**drop_map mode only**).
- To avoid re-pinging members, it sends a placeholder first with no mentions (`map_commands.py:105-106`), resolves role/channel objects, builds the content with role mentions, then **edits** the placeholder in place (editing does not ping) (`map_commands.py:138-148`).
- The new sticky message id is persisted (`server_queue_config.sticky_message_id`) for the next refresh cycle (`map_commands.py:151`).
- Map request rows also carry `message_id` and `backup_message_id` so the live queue display message can be located and refreshed (`database_improved.py`, `map_requests` schema).
