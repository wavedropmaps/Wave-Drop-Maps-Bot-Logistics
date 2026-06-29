# Cross-Bot Interaction (Wave Management ⇄ Wave Logistics)

> How the two Wave bots coordinate. They do **NOT** talk over a network/API — they
> coordinate through **shared files and shared Discord channels**. This doc is the
> connective index; per-mechanism detail lives in the files linked below (and in the
> *other* bot's repo, which holds an identical copy of this file).
>
> **The two bots:**
> - **Wave Management Bot** — `C:/Users/kiere/Desktop/Wave Management Bot` (staff activity, rewards, WP economy, staff hub).
> - **Wave Logistics Bot** — `C:/Users/kiere/Desktop/Wave Logistics Bot` (request queue, proof ML/automation, HITL review).
>
> **Golden rule:** the two bots share channels and load similar task files, so **assume one
> bot's automated action can trigger the other bot's listeners.** When debugging
> "something happened by itself," check the OTHER bot before assuming a local bug.

---

## At a glance — the four channels

There are exactly **four** ways the bots coordinate. Two of them are "shared SQLite,"
but they are NOT the same thing — #1 is a queue *both* bots service; #4 is Logistics
writing into Management's *own* main DB. Keep that distinction straight.

| # | Channel | Medium | Direction |
|---|---|---|---|
| 1 | DM queue | shared SQLite file `dm_shared_queue.db` (outside both repos) | bidirectional, coordinated |
| 2 | Route bridges | Discord channel posts | Logistics → Management |
| 3 | Proof/HITL channel | shared Discord channel | mutual triggering |
| 4 | Reviews stat | direct DB write into Management's `bot_logs` table | Logistics → Management |

---

## 1. Shared DM System — a SQLite "mailbox" outside both repos

The shared-DMing system. Not an API — a **shared SQLite database file both bots open**.

- **File:** `C:/Users/kiere/Desktop/dm_shared_queue.db` — lives in `Desktop/`, **outside both repos**, hardcoded as `SHARED_DB` in **both** bots' `Tasks/dm_queue.py` / `tasks/dm_queue.py`.
- Each bot runs a near-identical `DMQueueCog`. Any bot that wants to DM a user **INSERTs a job** into the `dm_queue` table instead of calling `user.send()` directly (Management's `main.py` monkey-patch makes every send route through here automatically).
- **Coordinator loop** assigns each pending job to exactly one online bot, round-robin, load-balanced by a per-bot rate window (`DM_WINDOW_LIMIT=5` sends / `DM_WINDOW_SECONDS=300`, `DM_PER_SECOND_GAP=1.0`).
- **Worker loop** on the assigned bot does the actual send; **heartbeat** (`dm_bot_registry`, every 5s) marks which bots are alive (`BOT_OFFLINE_THRESHOLD=30s`).
- **Recovery loop** re-queues stuck/failed jobs (3 retries) and archives permanent failures (`dm_sent_archive` / `dm_failed_archive`).
- **Net effect:** either bot can send any DM, no duplicates, and if one bot is offline the other covers.
- **Shared sticky note:** `reply_dm_note` table (also in the shared DB) coordinates auto-replies — `auto_reply` jobs are pinned to the *source* bot so the bot that armed the note is the one that replies. See Management `reply_dm_state.py` / `reply_dm_inbound.py` / `reply_dm_outbound.py`.

**Detail:** Management `ai-hub/memory/bot-infrastructure/background-tasks.md` → "Shared DM Queue System"; Logistics `background-tasks.md` → DM queue.

---

## 2. The "task bridge" — queue → channel forwarding (Logistics → Management)

How loot-route / surge-route queue entries become assignable maps in Management. **One-way**, two cooperating halves. Logistics owns the request queue (`map_requests` table in *its* DB); Management owns the maker-assignment channels.

### Logistics side (the pusher)
- `Tasks/loot_bridge.py` — 60s sweep posts undispatched `loot_route` entries into Management's **maps-not-taken** channel `1205406903463710750`.
- `Tasks/surge_bridge.py` — same pattern for `surge_route` entries → Management's **surge-maps** channel `1416770574042140804`.
- Both pull from queue guild `971731167621574666`, format the post like a normal member submission (`Game Mode:` / `Description:` + image), and **hide the queue code + priority inside the image FILENAME** (`loot-q<code>-p<n>-<orig>.png` / `surge-q…`) so staff never see raw marker text. URL-only fallback uses a `-#` subtext marker line instead.
- `dispatched_at` is stamped so an entry is never double-posted.

### Management side (the reader)
- `tasks/map_request.py` (`MapRequestForwarder`) + `tasks/loot_routes.py` watch those channels, parse the embed/filename, extract the queue code, and tie it to the maker assignment.
- On completion, Management fires `-z removequeue <code>` back to drop the entry from the Logistics queue.

> ⚠️ **Stale-path warning:** the bridge files **replaced** the older indirect path where queue *display embeds* leaked into maps-not-taken via `MapRequestForwarder`. The forwarder now filters out loot-route queue display embeds (title contains "LOOT ROUTE REQUEST"). Don't document the old indirect path as current.

**Detail:** Logistics `ai-hub/memory/bot-infrastructure/map-queue.md`; Management `tasks/map_request.py` + `tasks/loot_routes.py`.

---

## 3. The proof / review (HITL) channel — shared channel, mutual triggering

How submissions are verified across the two bots.

- **Proof verification + HITL review queue lives in the Logistics bot** (`Tasks/proof.py`, `Tasks/proof_automation_tasks.py`, `Commands/review_queue_commands.py`). The ML cascade auto-grants/rejects; uncertain cases become HITL review cards. Resolving a review emits a `review_completed` event that feeds Management's "Reviews Completed" engagement stat. Clearing a *stale* card emits audit-only `review_cleared` (NOT counted).
- **The cross-bot gotcha:** the proof channel (`#┃❗・proof・❗┃`) is configured as **Management's** `reply_dm_channel`. Management's `reply_dm_outbound` `is_staff` check intentionally includes `message.author.bot` → it treats **any** bot's reply as a staff reply. So when **Logistics** auto-replies to a proof (grant/reject), **Management** arms a 5-minute auto-delete timer and sweeps the original proof. This is one bot's automation firing the other bot's delete mechanism — not a human, not the 12h expiry.
- **Preferred fix** (in Management): guard `reply_dm_outbound.on_message` to skip auto-delete when the replier is the Logistics proof bot. (Alt: remove the proof channel from `reply_dm_channel_id`.)

**Detail:** Logistics `ai-hub/memory/bot-infrastructure/hitl-review-queue.md`, `proof-automation.md`, and the post-mortem `global-memory/context/001-cross-bot-proof-deletion.md`.

---

## 4. Shared `bot_logs` table — how reviewing data crosses into Management

This is the actual transport for the "Reviews Completed" stat (and other Logistics
events the website shows). **Logistics writes event rows directly into the Management
bot's main database** — there is no API call and no separate sync job for this.

- Logistics's `utils/global_logger.py` is a mirror of Management's `core/global_logger.py`,
  but its `_DB_FILE` is **dynamically resolved to the sibling folder**:
  `os.path.join(_PARENT_DIR, "Wave Management Bot", "bot_database.db")`. So both bots
  funnel events into the **same `bot_logs` table** inside Management's `bot_database.db`.
- Every row carries a `bot` column (`"manager"`, `"logistics"`, or `"server"`) so the
  website can route it to the right section. Logistics rows are tagged `bot="logistics"`.
- **The review flow:** when a HITL review is resolved (grant/reject/discard/class-pick),
  Logistics calls `log_event(category="hitl_review", action="review_completed", actor=<staff>, ...)`
  in `Tasks/proof_automation_tasks.py`. That INSERTs a row into Management's `bot_logs`.
  Stale-card clears emit `review_cleared` instead (audit-only, never counted).
- **Management reads it back** by filtering `bot_logs WHERE category='hitl_review' AND
  action='review_completed'` — see `database.py` (~line 4189) and
  `tasks/unified_weekly_loop.py` (~line 611, weekly + lifetime stats). That count is the
  staff "Reviews Completed" engagement metric and feeds WP / challenge logic.
- **Implication:** the path only works while both folders sit side-by-side on the same
  machine (`Desktop/Wave Management Bot` + `Desktop/Wave Logistics Bot`). If Logistics
  can't reach that file (renamed/moved folder, Management DB locked), review counts
  silently stop landing. A `NullHandler` logger means failures are quiet — check the
  `bot_logs` table for recent `bot='logistics'` rows when reviews read as 0.

**Detail:** Logistics `utils/global_logger.py`; Management `core/global_logger.py` + `database.py` + `tasks/unified_weekly_loop.py`.

---

## Quick reference — shared IDs & files

| Thing | Value |
|---|---|
| Shared DM DB | `C:/Users/kiere/Desktop/dm_shared_queue.db` |
| Queue source guild (Logistics) | `971731167621574666` |
| Maps-not-taken channel (loot) | `1205406903463710750` |
| Surge-maps channel | `1416770574042140804` |
| Bridge files (Logistics) | `Tasks/loot_bridge.py`, `Tasks/surge_bridge.py` |
| Bridge readers (Management) | `tasks/map_request.py`, `tasks/loot_routes.py` |
| DM queue cog (both) | `Tasks/dm_queue.py` / `tasks/dm_queue.py` |
| Proof/HITL (Logistics) | `Tasks/proof.py`, `Commands/review_queue_commands.py` |
| Shared event table | `bot_logs` inside Management's `bot_database.db` (Logistics writes here too) |
| Event loggers | Management `core/global_logger.py`, Logistics `utils/global_logger.py` |
| Review-stat readers (Management) | `database.py` (~4189), `tasks/unified_weekly_loop.py` (~611) |

*This file is mirrored in both repos. When the cross-bot wiring changes, update BOTH copies.*
