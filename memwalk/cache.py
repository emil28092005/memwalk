"""Per-directory cached SSM state.

Each cached codebase has two sidecar files under CACHE_DIR:

    cache/<key>.memb        — the memba state file (binary)
    cache/<key>.json        — metadata (source path, manifest hash, stats…)

`<key>` is derived from the absolute source path so the same directory
always maps to the same files.  The manifest hash inside the metadata
detects whether the source has changed since the cache was built.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

CACHE_DIR = Path.home() / ".memwalk/cache"


# ── Metadata model ───────────────────────────────────────────────

@dataclass(slots=True)
class CacheMeta:
    key: str
    source_path: str        # absolute path to the source dir
    manifest_hash: str      # current files' fingerprint at digest time
    n_files: int
    n_chars: int
    n_ctx: int
    model_path: str
    created_iso: str
    last_used_iso: str

    @property
    def state_path(self) -> Path:
        return CACHE_DIR / f"{self.key}.memb"

    @property
    def meta_path(self) -> Path:
        return CACHE_DIR / f"{self.key}.json"

    def touch(self) -> None:
        self.last_used_iso = datetime.now().isoformat(timespec="seconds")
        self.save()

    def save(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.meta_path.write_text(json.dumps(asdict(self), indent=2))


# ── Key derivation ───────────────────────────────────────────────

def cache_key(source_path: Path) -> str:
    """Short, path-stable cache key (16 hex chars).  Same dir → same key."""
    abs_path = str(source_path.resolve())
    return hashlib.sha256(abs_path.encode("utf-8")).hexdigest()[:16]


# ── Read ─────────────────────────────────────────────────────────

def load_meta(source_path: Path) -> CacheMeta | None:
    """Return the cache meta for @source_path, or None if not cached."""
    key = cache_key(source_path)
    meta_file = CACHE_DIR / f"{key}.json"
    if not meta_file.exists():
        return None
    try:
        data = json.loads(meta_file.read_text())
        return CacheMeta(**data)
    except (json.JSONDecodeError, TypeError):
        return None


def is_fresh(meta: CacheMeta, current_manifest: str) -> bool:
    """True iff cache file is intact and manifest hash unchanged."""
    return (meta.state_path.exists()
            and meta.manifest_hash == current_manifest)


def list_all() -> list[CacheMeta]:
    """Return every cached entry on disk, newest-used first."""
    if not CACHE_DIR.exists():
        return []
    entries: list[CacheMeta] = []
    for meta_file in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(meta_file.read_text())
            entries.append(CacheMeta(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    entries.sort(key=lambda m: m.last_used_iso, reverse=True)
    return entries


# ── Write / delete ───────────────────────────────────────────────

def write_meta(
    source_path: Path,
    *,
    manifest_hash: str,
    n_files: int,
    n_chars: int,
    n_ctx: int,
    model_path: str,
) -> CacheMeta:
    key = cache_key(source_path)
    now = datetime.now().isoformat(timespec="seconds")
    meta = CacheMeta(
        key=key,
        source_path=str(source_path.resolve()),
        manifest_hash=manifest_hash,
        n_files=n_files,
        n_chars=n_chars,
        n_ctx=n_ctx,
        model_path=str(model_path),
        created_iso=now,
        last_used_iso=now,
    )
    meta.save()
    return meta


def drop(source_path: Path) -> bool:
    """Remove cache for @source_path. Returns True if anything was deleted."""
    key = cache_key(source_path)
    deleted = False
    for ext in (".memb", ".json"):
        p = CACHE_DIR / f"{key}{ext}"
        if p.exists():
            p.unlink()
            deleted = True
    return deleted


# ── memba session naming convention ──────────────────────────────
#
# We want memba's `Session` to write to / read from `cache/<key>.memb`.
# memba builds its filename as `<state_dir>/<session_id>.memb`, so:
#
#     Session(session_id=key, state_dir=CACHE_DIR, ...)
#
# already gives us the right path.  Helpers below just compute key.

def session_id_for(source_path: Path) -> str:
    return cache_key(source_path)


def state_dir() -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR
