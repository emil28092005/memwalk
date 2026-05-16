"""
MCP server — exposes memwalk as tools for Claude Code / opencode / any
MCP-aware agent. Runs over stdio.

Tools:
    ask(question)   — query the current memwalk state, returns the answer text
    standup()       — generate standup notes from accumulated activity
    status()        — config + state metadata (no model load required)
    update()        — refresh state from git/bash (slow, on demand)

Lifetime model: the underlying memba Session is loaded lazily on the first
tool call that needs it, then reused for the rest of the process — so the
first query pays ~2s of model+state load, subsequent queries are <500ms.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import __version__
from .config import load_config, read_last_update
from .ingest import open_session, update as run_update

_server = Server("memwalk")

# Singleton session — created on first call that needs it
_session = None
_QUERY_FRAMING = (
    "Drawing on the work activity I shared with you earlier, please answer "
    "this clearly and concretely:\n\n"
)


def _get_session():
    """Lazy-load (or reload) the memba Session."""
    global _session
    if _session is None:
        cfg = load_config()
        _session = open_session(cfg)
    return _session


def _reset_session() -> None:
    """Drop the cached session — used after `update` so next query sees fresh state."""
    global _session
    _session = None


# ── Tool declarations ────────────────────────────────────────────

@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="ask",
            description=(
                "Query the user's accumulated work memory. Returns the model's "
                "natural-language answer based on git commits and shell activity "
                "previously ingested by memwalk. Use for questions like "
                "'what was I working on last week?', 'which project saw the most "
                "activity?', 'when did I start branch X?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "Natural-language question about the user's recent work.",
                    },
                    "max_tokens": {
                        "type": "integer",
                        "description": "Maximum tokens to generate (default 400).",
                        "default": 400,
                    },
                },
                "required": ["question"],
            },
        ),
        Tool(
            name="standup",
            description=(
                "Generate concise daily-standup notes from the user's recent "
                "activity: what they did yesterday (grouped by project), planned "
                "next steps, and any blockers visible in commit messages."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="status",
            description=(
                "Return memwalk configuration and state metadata as JSON. "
                "Cheap — does not load the model. Useful for sanity-checking "
                "whether memwalk has up-to-date data."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="update",
            description=(
                "Ingest new git+bash activity into the state. Slow (a few seconds — "
                "loads the model). Call only when the user explicitly asks for a "
                "refresh, or when status() shows the last update is stale."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


# ── Tool dispatch ────────────────────────────────────────────────

@_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "ask":
        question = arguments.get("question", "").strip()
        if not question:
            return [TextContent(type="text", text="error: question is required")]
        sess = await asyncio.to_thread(_get_session)
        answer = await asyncio.to_thread(
            sess.chat,
            _QUERY_FRAMING + question,
            arguments.get("max_tokens", 400),
        )
        return [TextContent(type="text", text=answer)]

    if name == "standup":
        sess = await asyncio.to_thread(_get_session)
        standup_q = (
            "Generate my daily standup notes. Cover: what I worked on yesterday "
            "(grouped by project), what I plan today based on the trajectory, and "
            "any blockers visible in the activity. Be concise — bullet points, "
            "no preamble."
        )
        answer = await asyncio.to_thread(
            sess.chat, _QUERY_FRAMING + standup_q, 500
        )
        return [TextContent(type="text", text=answer)]

    if name == "status":
        cfg = load_config()
        last = read_last_update(cfg)
        info = {
            "version":      __version__,
            "model_path":   str(cfg.model_path),
            "state_file":   str(cfg.state_path),
            "state_bytes":  cfg.state_path.stat().st_size if cfg.state_path.exists() else 0,
            "last_update":  last.isoformat() if last else None,
            "scan_paths":   [str(p) for p in cfg.git.scan_paths],
            "bash_enabled": cfg.bash.enabled,
        }
        return [TextContent(type="text", text=json.dumps(info, indent=2))]

    if name == "update":
        cfg = load_config()
        result = await asyncio.to_thread(run_update, cfg)
        _reset_session()  # next ask/standup sees the freshly written state
        summary = (
            f"Ingested {result['ingested']} events "
            f"({result['git']} commits + {result['bash']} shell sessions) "
            f"in {result['elapsed_s']:.1f}s. "
            f"State now {result['state_size']:,} bytes."
        ) if result["ingested"] else (
            f"No new activity since {result['since'].strftime('%Y-%m-%d %H:%M')}."
        )
        return [TextContent(type="text", text=summary)]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


# ── Entry point ──────────────────────────────────────────────────

async def _run() -> None:
    async with stdio_server() as (read, write):
        await _server.run(read, write, _server.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
