"""
setup_mcp.py — Generate agent-specific MCP configs from ai-hub/mcp/servers.json

Run once on any new machine after cloning:
    python ai-hub/scripts/setup_mcp.py

Generates:
    .mcp.json          — Claude Code
    .cursor/mcp.json   — Cursor
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SERVERS_FILE = ROOT / "ai-hub" / "mcp" / "servers.json"


def load_servers():
    data = json.loads(SERVERS_FILE.read_text(encoding="utf-8"))
    return data["servers"]


def build_mcp_json(servers: dict) -> dict:
    """Claude Code / Cursor format — identical structure."""
    return {
        "mcpServers": {
            name: {k: v for k, v in cfg.items() if k != "description"}
            for name, cfg in servers.items()
        }
    }


def write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  wrote {path.relative_to(ROOT)}")


def main():
    print(f"Reading {SERVERS_FILE.relative_to(ROOT)}")
    servers = load_servers()

    print("Generating configs...")

    # Claude Code
    write(ROOT / ".mcp.json", build_mcp_json(servers))

    # Cursor
    write(ROOT / ".cursor" / "mcp.json", build_mcp_json(servers))

    print("\nDone. MCP servers registered for:")
    print("  Claude Code  -> .mcp.json")
    print("  Cursor       -> .cursor/mcp.json")
    print("\nServers:")
    for name, cfg in servers.items():
        print(f"  {name:20s} — {cfg.get('description', '')}")


if __name__ == "__main__":
    main()
