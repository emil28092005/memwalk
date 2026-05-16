"""Daily snapshot rotation for the rolling state file."""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta

from .config import Config


def maybe_snapshot(cfg: Config) -> bool:
    """If current.memb exists and no snapshot for today, copy it. Returns True if snapshotted."""
    if not cfg.state_path.exists():
        return False

    today = datetime.now().strftime("%Y-%m-%d")
    target = cfg.snapshots_dir / f"{today}.memb"
    if target.exists():
        return False

    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg.state_path, target)
    return True


def prune_old(cfg: Config, keep_days: int = 90) -> int:
    """Delete snapshots older than @keep_days. Returns number deleted."""
    if not cfg.snapshots_dir.exists():
        return 0
    cutoff = (datetime.now() - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    deleted = 0
    for snap in cfg.snapshots_dir.glob("*.memb"):
        if snap.stem < cutoff:
            snap.unlink()
            deleted += 1
    return deleted
