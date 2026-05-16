"""Git event source — walks configured paths for repos and collects commits."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import Event


def find_repos(roots: list[Path]) -> list[Path]:
    """Return all distinct git repositories under the given roots (non-recursive
    one level deep, plus the root itself if it's a repo)."""
    repos: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            continue
        candidates: list[Path] = []
        if (root / ".git").exists():
            candidates.append(root)
        for entry in root.iterdir():
            if entry.is_dir() and (entry / ".git").exists():
                candidates.append(entry)
        for c in candidates:
            real = c.resolve()
            if real not in seen:
                seen.add(real)
                repos.append(c)
    return repos


def collect(roots: list[Path], since: datetime) -> list[Event]:
    """Collect commits across @roots that landed after @since."""
    since_iso = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    events: list[Event] = []

    for repo in find_repos(roots):
        try:
            out = subprocess.check_output(
                [
                    "git", "-C", str(repo), "log",
                    f"--since={since_iso}",
                    "--no-merges",
                    "--date=iso-strict",
                    "--pretty=format:%H%x09%ad%x09%an%x09%s",
                ],
                text=True, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            continue

        for line in out.splitlines():
            parts = line.split("\t", 3)
            if len(parts) != 4:
                continue
            sha, iso, author, subject = parts
            try:
                ts = datetime.fromisoformat(iso)
            except ValueError:
                continue
            events.append(Event(
                source="git",
                ts=ts,
                summary=f"[{repo.name}] {subject}",
                detail=f"commit {sha[:10]} by {author}",
            ))

    return events


def format_block(events: list[Event]) -> str:
    """Group commits by repo (extracted from `[name] subject`) into a readable block."""
    if not events:
        return ""

    by_repo: dict[str, list[Event]] = {}
    for e in events:
        # Strip leading "[repo] " prefix to recover repo name
        repo = e.summary.split("]", 1)[0].lstrip("[") if e.summary.startswith("[") else "unknown"
        by_repo.setdefault(repo, []).append(e)

    parts: list[str] = ["git activity:"]
    for repo, items in sorted(by_repo.items()):
        items.sort(key=lambda x: x.ts)
        parts.append(f"\n  {repo} ({len(items)} commits):")
        for e in items:
            date = e.ts.strftime("%Y-%m-%d")
            subject = e.summary.split("] ", 1)[-1]
            parts.append(f"    {date}  {subject}")
    return "\n".join(parts)
