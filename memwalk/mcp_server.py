"""
MCP server (stdio) — exposes memwalk as tools for Claude Code / opencode /
Hermes / any MCP-aware agent.

Tools:
    digest(path)              — ingest a codebase into cached SSM state
    ask(path, question)       — query a codebase (auto-digests if needed)
    list_caches()             — show all cached codebases
    drop_cache(path)          — invalidate a cache
    status()                  — config + cache summary
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from . import __version__, cache, corpus
from .config import CONFIG_PATH, load_config
from .engine import ask as engine_ask
from .engine import digest as engine_digest
from .engine import digest_subdirs as engine_digest_subdirs

_server = Server("memwalk")


# ── Tool declarations ────────────────────────────────────────────

@_server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="digest",
            description=(
                "Read all source files under the given directory and build a "
                "cached SSM state that can be queried in subsequent ask() calls. "
                "Use this for small-to-medium repos that fit in a single context "
                "window (default ~120K chars). For large repos that exceed the "
                "context limit, use digest_split instead. Slow first time (5-30s); "
                "cache is reused on subsequent calls until source files change. "
                "You can also call ask() directly which auto-digests as needed."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the codebase root.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-ingest even if cache is fresh.",
                        "default": False,
                    },
                    "n_ctx": {
                        "type": "integer",
                        "description": "Override config n_ctx for this digest.",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="ask",
            description=(
                "Step 3 in the multi-corpus workflow. Query a codebase using its "
                "cached SSM state. Returns the model's answer based on the previously "
                "digested source. For large repos that were split-digested, you must "
                "pass the specific subdirectory path in 'path' that is most relevant "
                "to the question — not the repo root. For example: "
                "ask('/repo/src/auth', 'how does login work?') or "
                "ask('/repo/backend/db', 'what migrations exist?'). "
                "Auto-digests if no fresh cache exists (first call may be slow). "
                "Subsequent calls on the same codebase are fast (<1s typical)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path":     {"type": "string"},
                    "question": {"type": "string"},
                    "max_tokens": {"type": "integer", "default": 400},
                },
                "required": ["path", "question"],
            },
        ),
        Tool(
            name="list_caches",
            description=(
                "Return all cached codebases as JSON: source path, file count, "
                "char count, n_ctx, and last-used timestamp. Cheap — does not "
                "load the model."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="drop_cache",
            description=(
                "Invalidate the cached state for the given codebase path. Next "
                "ask() on that path will trigger a fresh digest."
            ),
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        Tool(
            name="status",
            description=(
                "Return memwalk config and cache summary as JSON. No model load."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_subdirs",
            description=(
                "Step 1 in the multi-corpus workflow. Lists subdirectories of a "
                "codebase root with file counts, estimated char sizes, recursion "
                "depth, and cache status. For large repos, this shows you what leaf "
                "directories are available so you can pick the right ones to digest. "
                "Directories that are too large for the current n_ctx budget are "
                "shown with their depth; you can use max_depth to control how deep "
                "the recursion goes. Cheap — does not load the model."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Codebase root to inspect."},
                    "max_depth": {"type": "integer", "description": "Max recursion depth (default: unlimited). Use 1 for immediate children only."},
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="digest_split",
            description=(
                "Step 2 in the multi-corpus workflow. Discovers subdirectories under "
                "the given path and digests each independently into its own cached "
                "SSM state. Each subdirectory gets a separate cache keyed by its "
                "absolute path, so subsequent ask() calls can target specific "
                "sub-caches (e.g. ask '/repo/src/auth' 'how does login work?'). "
                "Use list_subdirs first to see what will be digested. Slow first "
                "time (5-30s per subdirectory); cache is reused on subsequent calls. "
                "Large subdirectories that exceed the n_ctx budget are skipped with "
                "an error unless you increase max_depth to split them further."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Codebase root to split-digest."},
                    "force": {"type": "boolean", "description": "Re-ingest even if cache is fresh.", "default": False},
                    "n_ctx": {"type": "integer", "description": "Override config n_ctx for this digest (default: auto-detected from free VRAM)."},
                    "max_depth": {"type": "integer", "description": "Max recursion depth (default: unlimited). Use 1 for immediate children only."},
                },
                "required": ["path"],
            },
        ),
    ]


# ── Tool dispatch ────────────────────────────────────────────────

@_server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    if name == "digest":
        cfg = load_config()
        source = Path(arguments["path"]).expanduser().resolve()
        result = await asyncio.to_thread(
            engine_digest, cfg, source,
            n_ctx=arguments.get("n_ctx"),
            force=arguments.get("force", False),
        )
        payload = {
            "source_path": result.meta.source_path,
            "key":         result.meta.key,
            "n_files":     result.meta.n_files,
            "n_chars":     result.meta.n_chars,
            "n_ctx":       result.meta.n_ctx,
            "elapsed_s":   result.elapsed_s,
            "cache_hit":   result.elapsed_s == 0.0,
            "model_ack":   result.ack,
        }
        return [TextContent(type="text", text=json.dumps(payload, indent=2))]

    if name == "ask":
        cfg = load_config()
        source = Path(arguments["path"]).expanduser().resolve()
        question = arguments.get("question", "").strip()
        if not question:
            return [TextContent(type="text", text="error: question is required")]
        answer, meta, just_digested = await asyncio.to_thread(
            engine_ask, cfg, source, question,
            max_tokens=arguments.get("max_tokens", 400),
            auto_digest=True,
        )
        prefix = "(digested on demand) " if just_digested else ""
        return [TextContent(type="text", text=prefix + answer)]

    if name == "list_caches":
        entries = cache.list_all()
        out = [
            {
                "source_path":  m.source_path,
                "key":          m.key,
                "n_files":      m.n_files,
                "n_chars":      m.n_chars,
                "n_ctx":        m.n_ctx,
                "model_path":   m.model_path,
                "created":      m.created_iso,
                "last_used":    m.last_used_iso,
            }
            for m in entries
        ]
        return [TextContent(type="text", text=json.dumps(out, indent=2))]

    if name == "drop_cache":
        source = Path(arguments["path"]).expanduser().resolve()
        deleted = await asyncio.to_thread(cache.drop, source)
        return [TextContent(type="text",
                            text=f"{'dropped' if deleted else 'no cache for'}: {source}")]

    if name == "status":
        try:
            cfg = load_config()
        except FileNotFoundError as e:
            return [TextContent(type="text", text=f"not configured: {e}")]
        entries = cache.list_all()
        info = {
            "version":      __version__,
            "config_path":  str(CONFIG_PATH),
            "model_path":   str(cfg.model_path),
            "n_ctx":        cfg.n_ctx,
            "n_gpu_layers": cfg.n_gpu_layers,
            "cache_count":  len(entries),
        }
        return [TextContent(type="text", text=json.dumps(info, indent=2))]

    if name == "list_subdirs":
        source = Path(arguments["path"]).expanduser().resolve()
        subdirs = await asyncio.to_thread(
            corpus.discover_subdirs, source,
            max_depth=arguments.get("max_depth"),
        )
        out = [
            {
                "rel_path":     d.rel_path,
                "n_files":      d.n_files,
                "n_chars":      d.n_chars,
                "depth":        d.depth,
                "is_cached":    d.is_cached,
                "cache_n_ctx":  d.cache_n_ctx,
            }
            for d in subdirs
        ]
        return [TextContent(type="text", text=json.dumps(out, indent=2))]

    if name == "digest_split":
        cfg = load_config()
        source = Path(arguments["path"]).expanduser().resolve()
        results = await asyncio.to_thread(
            engine_digest_subdirs, cfg, source,
            n_ctx=arguments.get("n_ctx"),
            max_depth=arguments.get("max_depth"),
            force=arguments.get("force", False),
        )
        out = []
        for r in results:
            entry: dict[str, object] = {"rel_path": r.rel_path}
            if r.error:
                entry["error"] = r.error
            elif r.result is None:
                entry["status"] = "cache_fresh"
            else:
                entry["status"] = "digested"
                entry["n_files"] = r.result.meta.n_files
                entry["n_chars"] = r.result.meta.n_chars
                entry["elapsed_s"] = r.result.elapsed_s
            out.append(entry)
        return [TextContent(type="text", text=json.dumps(out, indent=2))]

    return [TextContent(type="text", text=f"unknown tool: {name}")]


# ── Entry point ──────────────────────────────────────────────────

async def _run() -> None:
    async with stdio_server() as (read, write):
        await _server.run(read, write, _server.create_initialization_options())


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
