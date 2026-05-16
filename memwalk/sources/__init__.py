"""Event sources — each module exposes `collect(since: datetime) -> list[Event]`."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Event:
    """A timestamped, human-readable activity record."""
    source: str         # "git", "bash", "obsidian", ...
    ts: datetime
    summary: str        # one-line description used in ingestion blocks
    detail: str = ""    # optional longer body
