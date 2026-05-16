"""memwalk CLI v0.2 — codebase exploration via cached SSM state."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from . import __version__, cache, corpus
from .config import (
    CONFIG_PATH, DEFAULT_MODEL_HINT, Config,
    load_config, write_config,
)
from .engine import ask as engine_ask
from .engine import digest as engine_digest
from .engine import digest_subdirs as engine_digest_subdirs

cli     = typer.Typer(
    name="memwalk",
    help="Ask AI about any codebase — local, cached, SSM-state-backed.",
    add_completion=False,
)
console = Console()


# ── init ──────────────────────────────────────────────────────────

@cli.command()
def init(
    model: str = typer.Option(None, "--model", help="Path to GGUF model"),
    n_ctx: int = typer.Option(32768, "--n-ctx", help="Inference context window"),
    gpu_layers: int = typer.Option(-1, "--gpu-layers", "-g"),
    force: bool = typer.Option(False, "--force", "-f"),
) -> None:
    """One-time setup. Writes ~/.memwalk/config.toml."""
    if CONFIG_PATH.exists() and not force:
        console.print(f"[yellow]Config already exists at {CONFIG_PATH}[/yellow]")
        console.print("Use --force to overwrite, or edit by hand.")
        raise typer.Exit(1)

    console.print(f"[bold cyan]memwalk init v{__version__}[/bold cyan]\n")
    if model is None:
        console.print(DEFAULT_MODEL_HINT + "\n")
        model = Prompt.ask("Path to GGUF model")

    cfg = Config(
        model_path=Path(model).expanduser(),
        n_gpu_layers=gpu_layers,
        n_ctx=n_ctx,
    )
    write_config(cfg)
    console.print(f"\n[green]✓[/green] {CONFIG_PATH}")
    console.print(
        f"\nNext: [bold]memwalk digest /path/to/repo[/bold] to ingest a codebase,\n"
        f"then  [bold]memwalk ask /path/to/repo \"...\"[/bold] to query."
    )


# ── digest ────────────────────────────────────────────────────────

@cli.command()
def digest(
    path: str = typer.Argument(..., help="Codebase root to ingest"),
    n_ctx: int = typer.Option(None, "--n-ctx",
                              help="Override config n_ctx for this digest"),
    force: bool = typer.Option(False, "--force", "-f",
                               help="Re-ingest even if a fresh cache exists"),
    split: bool = typer.Option(False, "--split", "-s",
                               help="Digest each immediate subdirectory independently"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Read all source files under PATH, build cached SSM state."""
    cfg = load_config()
    source = Path(path).expanduser().resolve()

    if split:
        with console.status(f"Discovering subdirectories in {source}…"):
            results = engine_digest_subdirs(cfg, source, n_ctx=n_ctx,
                                            force=force, verbose=verbose)
        if not results:
            console.print("[yellow]No digestable subdirectories found.[/yellow]")
            return
        for r in results:
            if r.error:
                console.print(f"[red]✗ {r.rel_path}: {r.error}[/red]")
            elif r.result is None:
                console.print(f"[dim]  {r.rel_path}: cache fresh[/dim]")
            else:
                m = r.result.meta
                console.print(
                    f"[green]✓[/green] {r.rel_path}: {m.n_files} files, "
                    f"{m.n_chars:,} chars in {r.result.elapsed_s:.1f}s"
                )
        return

    with console.status(f"Digesting {source}…"):
        result = engine_digest(cfg, source, n_ctx=n_ctx, force=force,
                               verbose=verbose)
    m = result.meta
    if result.elapsed_s == 0.0:
        console.print(f"[dim]Cache hit — already fresh ({m.n_files} files, "
                      f"{m.n_chars:,} chars).[/dim]")
        return
    console.print(
        f"[green]✓[/green] Digested {m.n_files} files, {m.n_chars:,} chars "
        f"in {result.elapsed_s:.1f}s ({result.char_rate:,.0f} char/s)"
    )
    console.print(f"  cache  : [dim]{m.state_path}[/dim]")
    ack = result.ack
    console.print(f"  model  : {ack[:140]}{'…' if len(ack) > 140 else ''}")


# ── ask ───────────────────────────────────────────────────────────

@cli.command()
def ask(
    path: str = typer.Argument(..., help="Codebase root (digest first or auto)"),
    question: str = typer.Argument(..., help="Natural-language question"),
    max_tokens: int = typer.Option(400, "--max-tokens"),
    no_auto_digest: bool = typer.Option(False, "--no-auto-digest",
                                        help="Fail instead of digesting if cache missing"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Query the cached codebase. Auto-digests if no cache exists."""
    cfg = load_config()
    source = Path(path).expanduser().resolve()
    with console.status("Thinking…"):
        answer, meta, just_digested = engine_ask(
            cfg, source, question,
            max_tokens=max_tokens,
            auto_digest=not no_auto_digest,
            verbose=verbose,
        )
    if just_digested:
        console.print(f"[dim](digested {meta.n_files} files / "
                      f"{meta.n_chars:,} chars on demand)[/dim]\n")
    console.print(answer)


# ── list ──────────────────────────────────────────────────────────

@cli.command("list")
def list_caches() -> None:
    """Show all cached codebases."""
    entries = cache.list_all()
    if not entries:
        console.print(f"[dim]No cached codebases yet. Try `memwalk digest <path>`.[/dim]")
        return
    table = Table(title="Cached codebases", show_lines=False)
    table.add_column("Source", style="cyan", overflow="fold")
    table.add_column("Files", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("n_ctx", justify="right")
    table.add_column("Last used")
    for m in entries:
        try:
            ts = datetime.fromisoformat(m.last_used_iso).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            ts = m.last_used_iso
        table.add_row(
            m.source_path,
            f"{m.n_files}",
            f"{m.n_chars:,}",
            f"{m.n_ctx:,}",
            ts,
        )
    console.print(table)


# ── list-subdirs ─────────────────────────────────────────────────

@cli.command("list-subdirs")
def list_subdirs(
    path: str = typer.Argument(..., help="Codebase root to inspect"),
) -> None:
    """Show immediate subdirectories with sizes and cache status."""
    source = Path(path).expanduser().resolve()
    subdirs = corpus.discover_subdirs(source)
    if not subdirs:
        console.print(f"[dim]No digestable subdirectories under {source}[/dim]")
        return
    table = Table(title=f"Subdirectories of {source.name}", show_lines=False)
    table.add_column("Directory", style="cyan")
    table.add_column("Files", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Cache", justify="center")
    for d in subdirs:
        cache_status = f"[green]cached[/green] (n_ctx={d.cache_n_ctx:,})" if d.is_cached else "[dim]none[/dim]"
        table.add_row(
            d.rel_path,
            f"{d.n_files}",
            f"{d.n_chars:,}",
            cache_status,
        )
    console.print(table)


# ── drop ──────────────────────────────────────────────────────────

@cli.command()
def drop(
    path: str = typer.Argument(..., help="Source dir whose cache to invalidate"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Invalidate cache for a codebase."""
    source = Path(path).expanduser().resolve()
    meta = cache.load_meta(source)
    if meta is None:
        console.print(f"[dim]No cache for {source}[/dim]")
        return
    if not yes and not typer.confirm(
        f"Drop cache for {meta.source_path} ({meta.n_files} files, "
        f"{meta.n_chars:,} chars)?"
    ):
        raise typer.Abort()
    deleted = cache.drop(source)
    console.print(f"[dim]{'Dropped' if deleted else 'Nothing to drop'}: {source}[/dim]")


# ── status ────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show config and cache summary."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    entries = cache.list_all()
    table = Table(show_header=False, box=None)
    table.add_row("[bold]config[/bold]",   str(CONFIG_PATH))
    table.add_row("[bold]model[/bold]",    str(cfg.model_path))
    table.add_row("[bold]n_ctx[/bold]",    f"{cfg.n_ctx:,}")
    table.add_row("[bold]gpu_layers[/bold]", str(cfg.n_gpu_layers))
    table.add_row("[bold]caches[/bold]",   f"{len(entries)} codebase(s)")
    console.print(table)
    if entries:
        console.print()
        list_caches()


# ── mcp ───────────────────────────────────────────────────────────

@cli.command()
def mcp() -> None:
    """Run as an MCP server for Claude Code / opencode / Hermes / etc."""
    from .mcp_server import main as mcp_main
    mcp_main()


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
