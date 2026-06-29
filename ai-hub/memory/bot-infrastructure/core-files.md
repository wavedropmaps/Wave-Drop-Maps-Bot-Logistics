# Core Files & Backbone
> Referenced from `AGENTS.md` → Codebase Map. The bot backbone: `Main.py` entry/intents/cog load order, the SQLite DB layer, shared config, dependency manifests, and top-level folder layout.

### Entry Point — `Main.py`
- Instantiates `commands.Bot(command_prefix="-z ", intents=intents, help_command=None)` — prefix is `-z ` (dash, z, space); the default help command is disabled in favor of a custom `Commands.help` cog (`Main.py:41`).
- Intents: starts from `discord.Intents.default()` then enables `message_content = True` and `members = True` (`Main.py:37-41`).
- `database.init_db()` is awaited during startup to create/attach the SQLite store before cogs run (`Main.py:437`).
- Allowlists the sibling Wave **Management** bot for bot-to-bot automation via `WAVE_MANAGEMENT_BOT_ID = 1269188273201352768` (`Main.py:152`).

### Cog / Extension Load Order (`Main.py:441-470`)
- Loaded in this exact order — `Tasks.dm_queue` is **first** because the shared DM queue/load-balancer must exist before anything sends a DM:
  1. `Tasks.dm_queue` (shared DM queue + load balancing)
  2. `Commands.priority_commands`
  3. `Commands.contributor_commands`
  4. `Commands.member_commands`
  5. `Commands.manual_control`
  6. `Commands.antinuke_commands`
  7. `Commands.map_commands`
  8. `Commands.streak_commands`
  9. `Commands.help`
  10. `Commands.local_server_config`
  11. `Commands.dm_commands`
  12. `Commands.auto_join_ping_commands`
  13. `Commands.proof_commands`
  14. `Tasks.dm_handling` → `Tasks.dm_processor` → `Tasks.auto_reply`
  15. `Tasks.antinuke_tasks`
  16. `Tasks.priority_task` (hourly priority-role expiration)
  17. `Tasks.contributor_task` → `Tasks.streak_tasks`
  18. `Tasks.auto_join_ping_task` → `Tasks.auto_cleanup_task`
  19. `Tasks.proof` → `Tasks.proof_automation_tasks` → `Commands.proof_automation_commands`
  20. `Commands.review_queue_commands` (HITL review cleanup)
  21. `Tasks.wave_logging` → `Tasks.vouch_dming` → `Tasks.surge_bridge`

### Startup Hooks (`Main.py`)
- `on_ready()` (`Main.py:292`) fires once and: runs a startup role audit (`312-314`), posts the streak leaderboard (`316-318`), syncs slash commands (`320-325`), refreshes drop_map sticky messages (`327-340`), re-registers persistent HITL claim views so buttons survive restart (`342-361`), and starts a 24h database-backup loop (`363-366`).
- Monkey-patches `discord.User.send` / `discord.Member.send` (`Main.py:427-428`, originals captured at `Main.py:8-14`) so **all** outbound DMs route through the shared `dm_queue` for cross-bot load balancing.

### Database Layer — `Database/database_improved.py`
- SQLite via **aiosqlite** (async). DB file is `Database/roles.db` (`database_improved.py:21`).
- Connection is a lazy, locked **singleton** (`get_db()`, `database_improved.py:27-55`): `isolation_level=None` (autocommit), `check_same_thread=False`, `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=30000` (30s lock wait) — tuned for concurrent access by the multi-task bot.
- Tables created in `_init_tables` (`database_improved.py:57-241`):
  - `tracked_roles` — guild_id, user_id, role_type, assigned_at, warned (drives priority/contributor expiration)
  - `role_streaks` — consecutive-assignment counter
  - `map_requests` — guild_id, `queue_number` (UNIQUE), image_url, user_ids (JSON), description, map_type, route_type, message_id, backup_message_id, status (`database_improved.py:79-94`)
  - `server_queue_config` (PK guild_id) — queue_channel_id, server_mode, sticky_message_id
  - `allowed_channels`, `dm_config`, `hitl_claim_state`, `hitl_sticky`, `stolen_detections`
- Indexes (`database_improved.py:221-240`) cover `(guild_id,user_id,role_type)`, `(guild_id)` on allowed_channels, role_streaks, and `(guild_id,queue_number)` + `(guild_id,status)` on map_requests.
- `bot.db` is the handle through which cogs/tasks reach this layer; the same module powers the 24h backup loop (`Database/database_backup.py`).

### Shared Config — `config.json`
- Full contents:
  ```json
  {
    "dm_queue": {
      "shared_db_path": "C:/Users/kiere/Desktop/dm_shared_queue.db",
      "log_channel_id": 1503714231566991441
    }
  }
  ```
- `dm_queue.shared_db_path` — a **separate** SQLite file (outside the repo, on the Windows desktop) that the DM queue shares with the sibling Management bot for cross-bot DM load balancing — distinct from `Database/roles.db`.
- `dm_queue.log_channel_id` — `1503714231566991441`, the Discord channel the DM-queue activity is logged to.

### Dependency Manifests
- `requirements.txt` (Python runtime): `discord.py>=2.0.0`, `aiosqlite>=0.17.0`, `python-dotenv>=0.19.0`, plus the ML/OCR proof-detection stack — `imagehash`, `easyocr`, `safetensors`, `transformers`, `ultralytics`, `torch`, `torchvision`, `Pillow`.
- `package.json` is metadata/scripts only (the bot is Python, not Node): `name` wave-logistics-bot, `version` 1.0.0, `main` `Main.py`, `scripts.start` = `python Main.py` (test/lint are no-op echoes), `engines.python >=3.8`, MIT license, repo `github.com/wavedropmaps/wave-logistics-bot`.

### Top-Level Folder Structure
- `Commands/` — prefix/slash command cogs: `map_commands`, `priority_commands`, `contributor_commands`, `member_commands`, `manual_control`, `antinuke_commands`, `streak_commands`, `help`, `local_server_config`, `dm_commands`, `auto_join_ping_commands`, `proof_commands`, `proof_automation_commands`, `review_queue_commands`.
- `Tasks/` — background loops & listeners: `priority_task`, `contributor_task`, `streak_tasks`, `dm_queue`, `dm_handling`, `dm_processor`, `auto_reply`, `antinuke_tasks`, `auto_join_ping_task`, `auto_cleanup_task`, `proof`, `proof_automation_tasks`, `wave_logging`, `vouch_dming`, `surge_bridge`.
- `utils/` — helpers: `queue_priority` (sort formula), `queue_encoding` (alpha codes), `automation_tree`, `model_nodes`, `automation_handlers`, `logging`, `global_logger`, `send_failure_capture`.
- `Database/` — `database_improved.py` (connection pool + schema), `roles.db` (runtime); `database_backups/database_backup.py` (24h backup).
- `Models/` — ML model artifacts for the proof-detection cascade (YOLO/ViT), used by `Tasks/proof_automation_tasks.py` (gitignored, local-only).
- Runtime-created: `queue_images/<guild_id>/<code>.<ext>` (durable queue images); `hitl_pending/` (staged review images); `.env` holds the bot token. Entry/config files at root: `Main.py`, `config.json`, `package.json`, `requirements.txt`, `push_wave_logging.py`.
