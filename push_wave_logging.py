"""
Push events from Wave Logistics bot to the shared wave_logging_local/data/
directory that Flask (Management Bot web_api.py) serves.

Both bots write to the SAME shared directory — they use different
data/<bot>/... subfolders so there's no collision.

CLI:
  python push_wave_logging.py            (live push)
  python push_wave_logging.py --dry-run  (stage locally, no push)
"""

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Load .env so BOT_TOKEN etc. are found automatically.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from utils.global_logger import fetch_unpushed, mark_pushed

# Shared local directory served by Flask (Management Bot web_api.py).
# Both bots write here; Flask at 127.0.0.1:5000 serves /logging/data/<path>.
LOCAL_ROOT = Path(r"C:\Users\kiere\Desktop\Wave Management Bot") / "wave_logging_local"

# Fat-event payloads are ~10-20× larger than the old thin format, so we
# drain fewer rows per cycle and split any single bucket that exceeds
# MAX_DELTA_BYTES into HHMMSS.partN.json so no file gets too large.
MAX_EVENTS_PER_PUSH = 1000
MAX_DELTA_BYTES = 8 * 1024 * 1024  # 8 MB per delta file


def _ensure_local_root() -> None:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    (LOCAL_ROOT / "data").mkdir(exist_ok=True)


def _delta_relpath(bot: str, category: str, ts_utc: datetime) -> str:
    day = ts_utc.strftime("%Y-%m-%d")
    fname = ts_utc.strftime("%H%M%S") + ".json"
    return f"data/{bot}/{category}/{day}/{fname}"


def _manifest_relpath(bot: str, category: str, day: str) -> str:
    return f"data/{bot}/{category}/{day}/_manifest.json"


def _stage_file(relpath: str, content: bytes) -> Path:
    full = LOCAL_ROOT / relpath
    full.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file then rename to avoid cross-process
    # file-lock collisions (Errno 22) when both bots write to the same dir.
    tmp = full.with_suffix(full.suffix + ".tmp")
    tmp.write_bytes(content)
    tmp.replace(full)
    return full


def _read_local(relpath: str) -> Optional[bytes]:
    full = LOCAL_ROOT / relpath
    if not full.exists():
        return None
    return full.read_bytes()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _split_events_for_size(events: list[dict], max_bytes: int) -> list[list[dict]]:
    """Split a flat event list into chunks whose serialized JSON each
    weighs <= max_bytes."""
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_size = 200  # rough overhead for the {"pushed_at": ..., "events": []} wrapper
    for ev in events:
        try:
            ev_size = len(json.dumps(ev, default=str))
        except Exception:
            ev_size = 1024
        projected = current_size + ev_size + 2
        if current and projected > max_bytes:
            chunks.append(current)
            current = []
            current_size = 200
            projected = current_size + ev_size + 2
        current.append(ev)
        current_size = projected
    if current:
        chunks.append(current)
    return chunks


async def _stage_guilds_json(bot) -> Optional[bytes]:
    if bot is None or not getattr(bot, "guilds", None):
        return None
    guilds_payload = [
        {
            "id": str(g.id),
            "name": g.name,
            "member_count": getattr(g, "member_count", None),
            "icon_url": str(g.icon.url) if getattr(g, "icon", None) else None,
        }
        for g in bot.guilds
    ]
    return json.dumps({"updated_at": _now_iso(), "guilds": guilds_payload},
                      indent=2).encode("utf-8")


async def push_unpushed_events(*, bot=None, dry_run: bool = False) -> dict:
    summary: dict[str, Any] = {
        "fetched": 0, "buckets": 0, "files_staged": 0,
        "errors": [], "dry_run": dry_run,
    }
    _ensure_local_root()
    rows = await fetch_unpushed(limit=MAX_EVENTS_PER_PUSH)
    summary["fetched"] = len(rows)
    if not rows:
        return summary

    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    row_ids: list[int] = []
    for r in rows:
        row_ids.append(r["id"])
        payload = {k: v for k, v in r.items() if k != "id"}
        buckets[(r["bot"], r["category"])].append(payload)
    summary["buckets"] = len(buckets)

    now = datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")

    for (bot_name, category), events in buckets.items():
        parts = _split_events_for_size(events, MAX_DELTA_BYTES)
        base_path = _delta_relpath(bot_name, category, now)
        part_filenames: list[str] = []
        for idx, part_events in enumerate(parts):
            if idx == 0:
                part_path = base_path
            else:
                part_path = base_path.replace(".json", f".part{idx + 1}.json")
            part_bytes = json.dumps(
                {"pushed_at": _now_iso(), "events": part_events}, indent=2,
            ).encode("utf-8")
            _stage_file(part_path, part_bytes)
            part_filenames.append(Path(part_path).name)
            summary["files_staged"] += 1

        manifest_path = _manifest_relpath(bot_name, category, day)
        existing = _read_local(manifest_path)
        if existing:
            try:
                manifest = json.loads(existing)
            except Exception:
                manifest = {"day": day, "files": []}
        else:
            manifest = {"day": day, "files": []}
        for delta_filename in part_filenames:
            if delta_filename not in manifest["files"]:
                manifest["files"].append(delta_filename)
        manifest["files"].sort()
        manifest["updated_at"] = _now_iso()
        manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
        _stage_file(manifest_path, manifest_bytes)
        summary["files_staged"] += 1

    guilds_bytes = await _stage_guilds_json(bot)
    if guilds_bytes:
        _stage_file("data/guilds.json", guilds_bytes)
        summary["files_staged"] += 1

    if not dry_run:
        await mark_pushed(row_ids)

    return summary


async def rollup_yesterday() -> dict:
    # Local files don't need rollup — Flask serves the delta files directly.
    # This stub keeps the nightly_rollup cog loop from failing on import.
    return {"rolled_up": 0, "errors": [], "note": "local-only mode, no rollup needed"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Push Logistics bot logs to shared wave_logging_local/data/")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build delta files locally without marking pushed")
    args = parser.parse_args()

    async def _run() -> int:
        summary = await push_unpushed_events(dry_run=args.dry_run)
        print(json.dumps(summary, indent=2))
        return 0 if not summary.get("errors") else 1

    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
