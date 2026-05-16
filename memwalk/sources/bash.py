"""
Bash history source.

Parses ~/.bash_history, handling the optional HISTTIMEFORMAT prefix
(`#<unix_ts>\\n<command>` blocks).  Filters out noise (short / dupe / common
navigation / secret-looking lines) and groups remaining commands into
sessions separated by a configurable idle gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import Event

# Commands too short or too common to be worth remembering
_SKIP_EXACT = {
    "ls", "ll", "la", "l", "cd", "cd -", "pwd", "clear", "exit", "fg", "bg",
    "jobs", "history", "reset",
}
_SKIP_PREFIX = ("ls ", "cd ", "cat ", "less ", "tail ", "head ", "echo ",
                "which ", "whereis ", "type ", "man ", "help ")

# Lines that look like they leak credentials — never ingest
_SECRET_PATTERNS = [
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|access[_-]?token|bearer)\s*[=:]"),
    re.compile(r"(?i)\b(aws|gcp|gh|github|hf|huggingface|openai|anthropic)[_-]?(token|key)"),
    re.compile(r"(?i)sk-[a-z0-9-]{20,}"),  # OpenAI/Anthropic API keys
    re.compile(r"(?i)ghp_[a-z0-9]{30,}"),  # GitHub PATs
]


def _is_noise(cmd: str) -> bool:
    if len(cmd) < 3:
        return True
    if cmd in _SKIP_EXACT:
        return True
    for p in _SKIP_PREFIX:
        if cmd.startswith(p) and len(cmd) < 25:
            return True
    return False


def _looks_secret(cmd: str) -> bool:
    return any(p.search(cmd) for p in _SECRET_PATTERNS)


@dataclass
class _Raw:
    ts: datetime | None
    cmd: str


def _parse_history(path: Path) -> list[_Raw]:
    """Read history file, returning entries with timestamps where available."""
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    entries: list[_Raw] = []
    pending_ts: datetime | None = None
    for line in lines:
        if not line:
            continue
        if line.startswith("#") and line[1:].strip().isdigit():
            # HISTTIMEFORMAT marker
            try:
                pending_ts = datetime.fromtimestamp(int(line[1:].strip()))
            except (ValueError, OSError):
                pending_ts = None
            continue
        entries.append(_Raw(ts=pending_ts, cmd=line.strip()))
        pending_ts = None
    return entries


def collect(
    history_file: Path,
    since: datetime,
    session_gap_min: int = 30,
) -> list[Event]:
    """Return cleaned bash events newer than @since, grouped into sessions."""
    raw = _parse_history(history_file)
    if not raw:
        return []

    # Filter
    clean: list[_Raw] = []
    prev_cmd: str | None = None
    for entry in raw:
        cmd = entry.cmd
        if not cmd or cmd.startswith("#"):
            continue
        if _is_noise(cmd) or _looks_secret(cmd):
            continue
        if cmd == prev_cmd:
            continue
        if entry.ts is not None and entry.ts < since:
            continue
        clean.append(entry)
        prev_cmd = cmd

    if not clean:
        return []

    # Group by session (gap > session_gap_min minutes starts a new one).
    # Commands without timestamps are attributed to the previous session.
    events: list[Event] = []
    session: list[_Raw] = []
    last_ts: datetime | None = None
    fallback_ts = since  # used when entries have no timestamps

    def flush_session(items: list[_Raw]) -> None:
        if not items:
            return
        ts = next((it.ts for it in items if it.ts is not None), fallback_ts)
        commands = [it.cmd for it in items]
        summary = f"shell session ({len(commands)} cmds): {commands[0][:60]}"
        detail = "\n".join(f"  $ {c}" for c in commands[:30])
        if len(commands) > 30:
            detail += f"\n  … and {len(commands)-30} more"
        events.append(Event(source="bash", ts=ts, summary=summary, detail=detail))

    gap = session_gap_min * 60
    for entry in clean:
        if entry.ts is not None and last_ts is not None:
            if (entry.ts - last_ts).total_seconds() > gap:
                flush_session(session)
                session = []
        session.append(entry)
        if entry.ts is not None:
            last_ts = entry.ts
    flush_session(session)
    return events


def format_block(events: list[Event]) -> str:
    if not events:
        return ""
    parts = ["shell activity:"]
    for e in events:
        date = e.ts.strftime("%Y-%m-%d %H:%M")
        parts.append(f"\n  [{date}] {e.summary}")
        if e.detail:
            parts.append(e.detail)
    return "\n".join(parts)
