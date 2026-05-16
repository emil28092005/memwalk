"""Orchestrate: pull events from sources, format, feed to a memba Session."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from memba import Session

from .config import Config, read_last_update, write_last_update
from .snapshot import maybe_snapshot
from .sources import Event, bash as bash_src, git as git_src


def open_session(cfg: Config, *, verbose: bool = False) -> Session:
    """Open a memba Session pointing at memwalk's rolling state file."""
    return Session(
        model_path=str(cfg.model_path),
        session_id="current",
        state_dir=str(cfg.state_dir),
        n_gpu_layers=cfg.n_gpu_layers,
        n_ctx=cfg.n_ctx,
        chat_format="chatml",
        verbose=verbose,
    )


def collect_all(cfg: Config, since: datetime) -> dict[str, list[Event]]:
    """Pull events from every enabled source."""
    out: dict[str, list[Event]] = {}
    if cfg.git.scan_paths:
        out["git"] = git_src.collect(cfg.git.scan_paths, since)
    if cfg.bash.enabled:
        out["bash"] = bash_src.collect(
            cfg.bash.history_file, since, cfg.bash.session_gap_min
        )
    return out


def format_block(events_by_source: dict[str, list[Event]],
                 since: datetime, until: datetime) -> str:
    """Build a single human-readable text block for ingestion."""
    chunks: list[str] = [
        f"=== Activity from {since.date()} to {until.date()} ==="
    ]
    if events_by_source.get("git"):
        chunks.append(git_src.format_block(events_by_source["git"]))
    if events_by_source.get("bash"):
        chunks.append(bash_src.format_block(events_by_source["bash"]))
    return "\n\n".join(chunks)


def update(cfg: Config, *, verbose: bool = False) -> dict:
    """Ingest new events into the state and return a small summary dict."""
    now = datetime.now()
    since = read_last_update(cfg)
    if since is None:
        # First run: bootstrap window
        since = now - timedelta(days=cfg.git.bootstrap_days)

    events = collect_all(cfg, since)
    n_git = len(events.get("git", []))
    n_bash = len(events.get("bash", []))

    if n_git + n_bash == 0:
        return {"ingested": 0, "git": 0, "bash": 0, "since": since, "until": now}

    block = format_block(events, since, now)

    # Rotate daily snapshot BEFORE mutating state
    snapshotted = maybe_snapshot(cfg)

    sess = open_session(cfg, verbose=verbose)

    prompt = (
        "Below is a record of my recent work activity. Read it carefully, "
        "then in two short sentences describe (a) the dominant theme of "
        "this period and (b) one or two standout projects. I will ask "
        "specific follow-up questions in later turns.\n\n" + block
    )

    t0 = time.time()
    ack = sess.chat(prompt, max_tokens=120)
    elapsed = time.time() - t0

    sess.save()
    write_last_update(cfg, now)

    return {
        "ingested": n_git + n_bash,
        "git": n_git,
        "bash": n_bash,
        "since": since,
        "until": now,
        "ack": ack,
        "elapsed_s": elapsed,
        "snapshotted": snapshotted,
        "state_size": sess.state_size,
    }


def query(cfg: Config, question: str, max_tokens: int = 400,
          verbose: bool = False) -> str:
    """Load the current state and ask a question.

    The question is wrapped in a short framing prefix so the model
    switches out of any acknowledgement pattern carried by prior ingest
    turns and actually answers from the activity it absorbed.
    """
    if not cfg.state_path.exists():
        raise FileNotFoundError("No state yet — run `memwalk update` first.")
    sess = open_session(cfg, verbose=verbose)
    framed = (
        "Drawing on the work activity I shared with you earlier, please "
        f"answer this clearly and concretely:\n\n{question}"
    )
    return sess.chat(framed, max_tokens=max_tokens)
