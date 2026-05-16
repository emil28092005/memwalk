"""Thin orchestration over corpus + cache + memba Session.
Shared by the CLI and the MCP server so they behave identically.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from memba import Session

from . import cache, corpus
from .config import Config

# Prompt that frames the ingest call so the assistant turn stored in state
# is *substantive* (not "noted") — avoids the contextual inertia bug we
# hit in v0.1.
_INGEST_PROMPT = (
    "Below is the entire source of a codebase. Read all files carefully — "
    "I will ask specific questions about the code in later turns. After "
    "reading, briefly state which 2-3 files seem most central and what the "
    "project appears to do, in two short sentences."
)

# Wrapping prefix on every query so the model switches out of any
# acknowledgement pattern and engages with the loaded codebase.
_QUERY_FRAMING = (
    "Drawing on the source code I shared with you earlier, please answer "
    "this clearly and concretely:\n\n"
)


@dataclass(slots=True)
class DigestResult:
    meta: cache.CacheMeta
    elapsed_s: float
    ack: str
    char_rate: float


@dataclass(slots=True)
class SubDirDigestResult:
    rel_path: str
    result: DigestResult | None
    error: str | None = None


# ── digest ──────────────────────────────────────────────────────

def digest(
    cfg: Config,
    source_path: Path,
    *,
    n_ctx: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> DigestResult:
    """Ingest @source_path into a cached SSM state.

    If a fresh cache already exists and force is False, returns it
    without touching the model.
    """
    if not source_path.exists() or not source_path.is_dir():
        raise NotADirectoryError(source_path)

    n_ctx = n_ctx or cfg.n_ctx
    files = corpus.collect_files(source_path)
    if not files:
        raise RuntimeError(f"No source files found under {source_path}")

    mh = corpus.manifest_hash(files)
    existing = cache.load_meta(source_path)
    if existing and not force and cache.is_fresh(existing, mh):
        existing.touch()
        return DigestResult(meta=existing, elapsed_s=0.0, ack="(cache hit)",
                            char_rate=0.0)

    # Wipe stale cache state file so memba Session doesn't auto-load it
    if existing:
        cache.drop(source_path)

    text = corpus.build_corpus(source_path, files)
    n_chars = len(text)

    sess = Session(
        model_path=str(cfg.model_path),
        session_id=cache.session_id_for(source_path),
        state_dir=str(cache.state_dir()),
        n_gpu_layers=cfg.n_gpu_layers,
        n_ctx=n_ctx,
        chat_format="chatml",
        verbose=verbose,
    )

    t0 = time.time()
    ack = sess.chat(f"{_INGEST_PROMPT}\n\n{text}", max_tokens=160)
    elapsed = time.time() - t0
    sess.save()

    meta = cache.write_meta(
        source_path,
        manifest_hash=mh,
        n_files=len(files),
        n_chars=n_chars,
        n_ctx=n_ctx,
        model_path=str(cfg.model_path),
    )
    return DigestResult(meta=meta, elapsed_s=elapsed, ack=ack,
                        char_rate=n_chars / elapsed if elapsed > 0 else 0.0)


# ── ask ─────────────────────────────────────────────────────────

def ask(
    cfg: Config,
    source_path: Path,
    question: str,
    *,
    max_tokens: int = 400,
    auto_digest: bool = True,
    verbose: bool = False,
) -> tuple[str, cache.CacheMeta, bool]:
    """Load cached state for @source_path and ask a question.

    Returns (answer, meta, was_digested_now).

    If no fresh cache exists and auto_digest is True, runs digest first.
    """
    files = corpus.collect_files(source_path)
    if not files:
        raise RuntimeError(f"No source files found under {source_path}")
    mh = corpus.manifest_hash(files)

    meta = cache.load_meta(source_path)
    was_digested_now = False
    if meta is None or not cache.is_fresh(meta, mh):
        if not auto_digest:
            raise RuntimeError(
                f"No fresh cache for {source_path}. "
                "Run `memwalk digest` first, or pass auto_digest=True."
            )
        result = digest(cfg, source_path, verbose=verbose)
        meta = result.meta
        was_digested_now = True

    sess = Session(
        model_path=str(cfg.model_path),
        session_id=meta.key,
        state_dir=str(cache.state_dir()),
        n_gpu_layers=cfg.n_gpu_layers,
        n_ctx=meta.n_ctx,  # MUST match the n_ctx the cache was built at
        chat_format="chatml",
        verbose=verbose,
    )
    answer = sess.chat(_QUERY_FRAMING + question, max_tokens=max_tokens)
    meta.touch()
    return answer, meta, was_digested_now


def digest_subdirs(
    cfg: Config,
    source_path: Path,
    *,
    n_ctx: int | None = None,
    force: bool = False,
    verbose: bool = False,
) -> list[SubDirDigestResult]:
    """Discover immediate subdirectories and digest each independently."""
    subdirs = corpus.discover_subdirs(source_path)
    if not subdirs:
        return []

    results: list[SubDirDigestResult] = []
    for sub in subdirs:
        try:
            result = digest(cfg, sub.abs_path, n_ctx=n_ctx, force=force,
                            verbose=verbose)
            results.append(SubDirDigestResult(
                rel_path=sub.rel_path,
                result=None if result.elapsed_s == 0.0 else result,
            ))
        except Exception as e:
            results.append(SubDirDigestResult(
                rel_path=sub.rel_path,
                result=None,
                error=str(e),
            ))
    return results
