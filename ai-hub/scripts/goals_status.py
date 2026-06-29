#!/usr/bin/env python3
"""SessionStart orientation: print in-flight goals from ai-hub/memory/global-memory/goals/.

Wired into Claude Code's SessionStart hook. Reads each goal file's frontmatter
and surfaces only the in-progress / review ones so any agent starts the session
knowing what's live.

Hard rule: this must NEVER break a session. Everything is wrapped; we always
exit 0 and print nothing on any failure.
"""
import os
import sys

# Statuses worth surfacing at session start.
SURFACE = ("in-progress", "review")
SKIP_FILES = ("README.md", "_TEMPLATE.md")


def parse_frontmatter(text):
    """Parse a leading '--- ... ---' YAML-ish block into a key:value dict.

    Only flat 'key: value' lines are read — enough for our frontmatter and
    dependency-free. Returns {} if there's no frontmatter block.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            # Drop inline '# ...' comments (e.g. the status enum hint).
            value = value.split("#", 1)[0]
            meta[key.strip().lower()] = value.strip()
    return meta


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    goals_dir = os.path.abspath(os.path.join(script_dir, "..", "memory", "global-memory", "goals"))
    if not os.path.isdir(goals_dir):
        return

    active = []
    for name in sorted(os.listdir(goals_dir)):
        if name in SKIP_FILES or not name.endswith(".md"):
            continue
        path = os.path.join(goals_dir, name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                meta = parse_frontmatter(fh.read())
        except Exception:
            continue
        if meta.get("status", "").lower() in SURFACE:
            active.append((name, meta))

    if not active:
        return  # stay quiet — nothing in flight

    print("\U0001F3AF In-flight goals (ai-hub/memory/global-memory/goals/) — run /codify when work wraps:")
    for name, meta in active:
        title = meta.get("title", name)
        status = meta.get("status", "?")
        print(f"  [{status}] {title}  ({name})")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
