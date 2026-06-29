# Background Tasks & Listeners
> Referenced from `AGENTS.md` → Codebase Map. Every background loop, monkey-patch, and event listener in `Tasks/` (excluding the proof pipeline), with intervals, triggers, wiring, and cross-bot coordination points with the separate **Wave Management Bot**.

### Shared cross-bot DM queue — overview (`dm_queue.py`)
- Single source of truth is a **shared SQLite DB** at `C:/Users/kiere/Desktop/dm_shared_queue.db` (hardcoded as `SHARED_DB` in every task that touches it; also in `config.json` → `dm_queue.shared_db_path`). Both Logistics AND Management bots open the same file with `PRAGMA journal_mode=WAL` + `busy_timeout=30000` so concurrent access doesn't corrupt.
- Architecture: bots **INSERT** jobs → a **coordinator** assigns each job to exactly ONE bot (round-robin) → that bot's **worker** sends. No duplicates; perfect round-robin when both bots have capacity (`dm_queue.py:1-7`).
- All outbound DMs are routed here via a `Main.py` monkey-patch: `discord.User.send`/`discord.Member.send` are replaced (`Main.py:427-428`) with patched versions that call `inst.enqueue(self, content=..., **kwargs)` and return `None` immediately (`Main.py:410-425`). Nothing in the bot relies on the send return value. Fallback path (`_original_user_send`/`_original_member_send`) only fires if the cog isn't loaded yet (early startup). `dm_queue` is loaded FIRST among extensions (`Main.py:441`).
- Tables (created/migrated in `_init_db`, `dm_queue.py:678-848`): `dm_queue` (live jobs), `dm_bot_registry` (per-bot heartbeat + rate state), `dm_sent_archive`, `dm_failed_archive`, plus `reply_dm_note` (sticky notes, see below). Legacy `auto_reply_queue` is dropped on init.

### DM queue — enqueue & serialization (`dm_queue.py`)
- `enqueue()` (`:143`) is synchronous; it pops reserved kwargs `batch_id` and `_source`, then spawns `_insert_job` as a task tracked in `self._insert_tasks` (strong refs prevent GC dropping an in-flight insert, `:91-94`).
- Only `content`/`embed`/`embeds` survive the shared-DB round-trip; `file`, `files`, `view`, `delete_after`, `allowed_mentions` etc. are **dropped and logged loudly** (`_serialize_kwargs`, `:38-65`). Anything passing attachments through the queue silently loses them — callers must be aware.
- `_source` tags the DM's origin; `'reply_dm_duty'` (Management bot's staff-reply forwards) is the ONLY source that preserves the sticky note on send; everything else wipes it (`_send_job`, `:573-574`).
- Startup race guard: `enqueue` may fire before `on_ready` sets `_bot_id`; `_insert_job` resolves a LOCAL bot id via `wait_until_ready` rather than setting `self._bot_id` (which `on_ready` uses as its "already started" guard, `:156-168`).

### DM queue — background loops (all started in `on_ready`, `dm_queue.py:101-134`)
- **Coordinator** (`_coordinator_loop`, every `COORDINATOR_INTERVAL=1.0s`, `:203`): loads online bots (`last_seen > now-30s`, `BOT_OFFLINE_THRESHOLD`), filters to capable ones (gap ≥ `DM_PER_SECOND_GAP=1.0s` AND `< DM_WINDOW_LIMIT=5` sends in `DM_WINDOW_SECONDS=300s`), then assigns up to 10 pending jobs round-robin via CAS `UPDATE ... WHERE status='pending'`. **`auto_reply` source jobs are pinned to their source bot** for sticky-note consistency, with a 5-min grace fallback to any capable bot if the source bot stays offline (`:271-285`).
- **Worker** (`_worker_loop`, every `WORKER_INTERVAL=1.0s`, `:490`): claims one `assigned` job for THIS bot via CAS `UPDATE status='sending'`, then `_send_job` calls the original (unpatched) send. On success marks `sent`, updates registry rate window, wipes sticky note (unless `reply_dm_duty`), logs to `DM_SEND_LOG_CHANNEL=1488725444357263390`. `discord.Forbidden`/`NotFound` → permanent fail → archived; other errors → transient fail (retried). Note: `discord.InvalidArgument` was removed in discord.py 2.x and is deliberately NOT caught (`:606-609`).
- **Heartbeat** (`_heartbeat_loop`, every `HEARTBEAT_INTERVAL=5.0s`, `:651`): upserts `last_seen` in `dm_bot_registry` so the other bot's coordinator sees this bot as online.
- **Recovery** (`_recovery_loop`, every 10s, `:311`): re-pends jobs stuck in `sending`/`assigned` > 60s (up to `MAX_RETRIES=3`, then archived to `dm_failed_archive`); retries transient `failed` jobs but NEVER `Forbidden`/`NotFound`/`user_error` ones.
- **Archive cleanup** (`_archive_cleanup_loop`, every 600s, `:424`): moves `sent` jobs older than 24h to `dm_sent_archive`; prunes both archive tables of entries older than 30 days.
- **Dashboard logging**: assignment/sent/failed events posted to `LOG_CHANNEL_ID=1503714231566991441` ("Queue manager dashboard, **shared with Management Bot**", `:31`); also emits `dm_queue` events to the Wave-Logging website via `utils.global_logger.log_event` (`:182-197`).

### DM receive logging (`dm_handling.py`)
- **Listener-only cog** (sends are NOT patched here anymore — `Main.py` owns that). `on_message` for non-guild, non-bot messages spawns `_log_received_dm` → posts a `📥 DM Received` embed to `RECEIVE_LOG_CHANNEL=1488725432726716446` (`:79-138`).
- Enriches each received-DM embed with the **last outbound DM** to that user, read from the shared DB (`dm_queue` UNION `dm_sent_archive`, ordered by `sent_at`, `_get_last_sent_dm`, `:31-68`) — cross-references the same shared queue both bots write to.

### DM processor — queue-code trigger (`dm_processor.py`)
- `on_message` listener (`:236`) watches each guild's configured `dm_channel_id` (from `database.get_dm_config`) for a **bracketed queue code** `(a)`/`[b]`/`{c}` (1–5 letters, `bracket_pattern` `:297`) plus a channel mention/URL. Stays silent to members on any error (errors only go to `dm_log_channel_id`).
- Picks DM template by server mode then `route_type`: `drop_map` / `loot_route` / `surge_route` (`self.dm_templates` at `:25-53` are the **source of truth** for DM wording — the `dm_template_*` DB columns are NOT read by any sender; `Commands/dm_commands.py senddm` carries a duplicate copy to keep in sync).
- Sends DMs to all `user_ids` on the request via a **per-guild `asyncio.Lock`** (`self.locks`, `:374`) so overlapping triggers queue instead of dropping; re-verifies the entry inside the lock. Each `member.send` is the patched call → enqueues into the shared DM queue (so progress is reported as "queued" not "sent", `:536-545`); the queue handles actual rate limiting.
- On full success: deletes the queue message, removes the DB entry, refreshes the queue display. On partial failure: posts a `DMFailureView` with **Retry Failed DMs** / **Delete Queue Entry** buttons (24h timeout, `:59-234`). Retry distinguishes role IDs vs user IDs by asking the guild (`guild.get_role`), not the old snowflake-size heuristic.

### Reply-DM sticky notes (`reply_dm_note.py`) + auto-reply (`auto_reply.py`)
- `reply_dm_note.py` is a helper module (no-op `setup`, `:108-111`), three primitives over the shared `reply_dm_note` table: `arm_note` (called by Management bot's `reply_dm_duty` after forwarding a staff reply), `wipe_note` (any non-`reply_dm_duty` outbound DM, or after auto-reply fires), `get_active_note` → `(guild_id, source_bot_id)` if armed within `NOTE_TTL_SECONDS=48h` (`:49-105`).
- `auto_reply.py` mirrors Management's cog. `on_message` on incoming DMs: if the author has an active note AND `source_bot_id == this bot.user.id` (so only the bot that armed it replies — prevents BOTH bots double-replying, `:74-79`), sends the guild-specific auto-reply via `message.author.send(..., _source="auto_reply")`. Templates keyed by guild `988564962802810961` / `971731167621574666` (`:26-43`). On `Forbidden` the note stays armed for retry.

### Surge bridge — cross-bot dispatch (`surge_bridge.py`)
- **Loop `surge_dispatch_sweep` every 60s** (`:72`). Reads undispatched surge-route queue entries for guild `SURGE_QUEUE_GUILD_ID=971731167621574666` (`database.get_undispatched_surge_requests`) and posts each into the **Management bot's** staff-hub surge-maps channel `SURGE_MAPS_CHANNEL_ID=1416770574042140804` (`:42-43`).
- Posts mimic loot-route "maps-not-taken" format (`Game Mode:` / `Description:` + image). Queue linkage (code + customer priority from `calculate_request_priority`) is **hidden in the attachment filename** `surge-q<code>-p<n>-<orig>.png` so staff never see raw markers; URL-only fallback uses a Discord subtext marker line `-# [surge-bridge] queue:<code> priority:<n>` (`:108-125`).
- `database.mark_surge_dispatched` stamps `dispatched_at` so the 60s sweep (the "reconciliation") never double-posts. On completion the **Management bot** fires `-z removequeue <code>` back to drop the entry — explicit cross-bot handshake.

### Auto-cleanup / ghost-ping deletion (`auto_cleanup_task.py`)
- Detects & deletes leaked auto-join ghost-ping messages (pure user-mention messages from a bot, `is_ghost_ping`, `:57-83`). **Runs on BOTH bots** (Management primary, Logistics fallback/double-check) — both read the same config and gracefully `NotFound`-skip messages the other already deleted (`:227-228`).
- Config read from shared DB `tippy_join_config` (`channel_ids`, `enabled`, `log_channel_id`, `_get_config` `:31-54`).
- **Live listener** `on_message` (`:111`): on a watched-channel ghost ping, waits `GRACE_PERIOD_SECONDS=5s`, re-fetches, deletes if still present.
- **Loop `daily_sweep` every 24h** (`:163`, startup delay 60s, lookback 48h): scans history, bulk-deletes ghost pings < 14 days old in chunks of 100, single-deletes older ones with 0.3s spacing.

### Auto-join ghost-ping creation (`auto_join_ping_task.py`)
- `on_member_join` (`:194`): batches a **mention + immediate delete** ghost-ping in configured channels. Cross-bot dedup via shared DB `join_ping_claims` — `_try_claim` does `INSERT OR IGNORE` then a cooldown-gated CAS `UPDATE`; only the winning bot pings, the other exits silently (`:151-190`). This is the counterpart that *creates* the pings `auto_cleanup_task.py` exists to clean up if they leak.
- Per-channel batch worker waits `batch_window_ms` (default 800ms), sends combined mentions, waits `delete_delay_ms` (default 1000ms), deletes with one retry; on `Forbidden` to delete it posts a **"GHOST PING LEAKED"** critical log (`_delete_with_retry`, `:323-357`). Respawns a worker if members joined during the drain (`:311-321`).
- Tables `tippy_join_config` + `join_ping_claims` created here (`_init_db`, `:76-114`). **Background `_claim_cleanup_loop` every 60s** (`on_ready`, `:58-62`) purges claims older than `CLAIM_RECORD_TTL_SECONDS=600s`. Stats and consolidated per-guild logs are buffered/flushed (`LOG_FLUSH_DELAY_SECONDS=2.5s`).

### Wave logging — server watcher (`wave_logging.py`)
- Heaviest cog; Logistics is the designated **server watcher**. Bot-side events use `category` via `BOT_NAME`; server-side events pass `bot=SERVER_BOT="server"` to route under `data/server/...` on the website (`:60`).
- Captures via `discord.py` listeners + `log_event`: command completion/errors (slash via monkey-patched `tree.on_completion`/`on_error` chaining previous handlers `:314-383`, prefix via cog listeners), bot lifecycle (ready/disconnect/resume), member join/leave/ban/kick/timeout/boost/screening, role & channel & guild-settings changes (each enriched with `fetch_audit`), voice state, soundboard, message delete/edit (+ raw fallbacks), reactions (raw), threads, webhooks, scheduled events, stage instances, AutoMod rules+actions, integrations, invites, and global user-profile updates.
- `install_terminal_log_capture()` mirrors all `logger.*` INFO+ into `bot_logs` for the dashboard's Terminal Logs tab (`:299`).
- **Loops**: `push_loop` every **5 min** (`:2043`, dropped from 15min because "fat events" are ~10-20× larger; pushes via `push_wave_logging.push_unpushed_events`); `nightly_rollup` at **00:10 UTC** (`:2059`, runs 5 min after Manager's rollup to avoid concurrent edits via `rollup_yesterday`).
- **Startup audit replay** (`_startup_audit_replay`, once per process, `:2000-2037`): walks each guild's audit log for the last 24h and backfills events missed while offline (best-effort, skips guilds lacking audit perms, 2s between guilds).

### Streaks (`streak_tasks.py`)
- **No interval loop.** `StreakTask.refresh_all_guilds` is called on startup to post/edit a streak-info embed + live leaderboard per guild, each with its own saved message id in `server_config.json` (`streak_info_message_id`, `streak_leaderboard_message_id`, `post_streak_info` `:159-206`). Streak data read from `database.get_all_tracked_roles` / `get_streak`. Badge tiers ✨/⭐/🔥/💎/👑 at 1/2/3/6/12 (`_badge` `:209-218`).

### Contributor role tracking (`contributor_task.py`)
- **Loop `check_contributor_roles` every 1h** (`:122`). Iterates `database.get_all_tracked_roles` for `contributor` records: day-27 → sends a "expiring in 3 days" warning DM (sets `warned` flag), day-30 → removes the role and DMs removal. **Cache-miss safety**: a missing `get_member` is confirmed via `guild.fetch_member` before untracking, and on an `HTTPException` removing the role the record is KEPT so next hour retries (never strands a user with an expired paid role, `:181-201`). DMs batched (10 at a time, 1s gap, `send_dms_batched` `:107-112`). Per-guild renewal link text in `GUILD_RENEW_TEXT`.

### Server protection — anti-nuke (`antinuke_tasks.py`)
- Pure event-driven (no loop). Per-guild config from `server_config.json` → `antinuke` (`get_antinuke_config` `:30-32`). In-memory `ActionTracker` keeps rolling 1min/1hr/1day timestamp windows per `(guild,user,action)` (`:37-64`).
- Listeners + thresholds (base | weighted-whitelist ×1.5): `@everyone`/`@here` pings 3/3/3 (`on_message`); channel deletes 3/5/7 per min/hr/day; **role delete & role perm-update → instant quarantine** (weighted users get 1 free); mass bans/kicks 100/min — all confirmed against the audit log (`audit_logs limit=5`, within 10s) to find the actor.
- `quarantine_user` (`:109-189`): 3s delay, dedup via `_recent_quarantines` (`QUARANTINE_DEDUP_SECONDS=600s`, expires so re-offenders can be re-quarantined), strips all removable roles, adds the quarantine role, logs to the configured channel AND emits an `antinuke` Wave-Logging event. `whitelist` users are fully immune.

### Server protection — invite purge (`invite_purge_task.py`)
- **Loop `purge_invites` every 24h** (`:26`, also runs on startup). For each guild with `database.get_invite_rules_enabled`, deletes invites by rule: (1) ≤2 days left AND ≤1 use; (2) ≤4 days left AND 0 uses; (3) infinite invites > 2 weeks old with 0 uses — **bot-created infinite invites are explicitly excluded** (`:86-98`). Gracefully handles `Forbidden`/`NotFound`.

### Vouch DMing (`vouch_dming.py`)
- `on_message` listener, fully hardcoded: watches `MONITOR_CHANNEL_ID=1210814682357698621` in `GUILD_ID=988564962802810961`. If the author lacks any of `REQUIRED_ROLES` (`1055713830988157039`, `993395068826296361`), spawns a DM thanking them for vouching and advertising the free-drop-maps channel (`:34-66`). The `member.send` routes through the shared DM queue like all other sends.

### Cross-bot collision notes
- **Shared DB write contention**: `dm_queue`, `reply_dm_note`, `auto_cleanup_task` (`tippy_join_config`), and `auto_join_ping_task` (`tippy_join_config` + `join_ping_claims`) all read/write `dm_shared_queue.db` from BOTH bots — every connection sets WAL + 30s busy_timeout; coordination relies on CAS `UPDATE`/`INSERT OR IGNORE` patterns, not locks.
- **Dashboard channel `1503714231566991441`** is shared with the Management bot (both post DM-queue events there).
- **Auto-cleanup runs on both bots** — relies on idempotent `NotFound`-tolerant deletes; both could attempt to delete the same ghost ping.
- **Auto-reply double-fire** is prevented only by the `source_bot_id == self.bot.user.id` check; the other bot sees the same incoming DM but bails.
- **Surge bridge writes into a Management-owned channel** and depends on Management firing `-z removequeue <code>` back to close the loop. Per project memory, Management's `reply_dm_outbound` can auto-delete member proof messages — the known two-bot deletion-collision source (see `global-memory/context/001-cross-bot-proof-deletion.md`).
