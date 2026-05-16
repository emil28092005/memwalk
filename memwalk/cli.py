"""memwalk CLI — typer entry point."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

from . import __version__
from .config import (
    DEFAULT_MODEL_HINT, HOME_DIR, BashConfig, Config, GitConfig,
    load_config, read_last_update, write_config,
)
from .ingest import open_session, query, update
from .snapshot import prune_old
from .sources import bash as bash_src
from .sources import git as git_src

cli     = typer.Typer(name="memwalk", help="Walk through your work memory.",
                      add_completion=False)
console = Console()


# ── init ──────────────────────────────────────────────────────────

@cli.command()
def init(
    model:        str = typer.Option(None, "--model", help="Path to GGUF model"),
    scan_path:    list[str] = typer.Option(None, "--scan",
                                            help="Directory to scan for git repos (repeatable)"),
    no_bash:     bool = typer.Option(False, "--no-bash", help="Disable bash history"),
    force:       bool = typer.Option(False, "--force", "-f",
                                      help="Overwrite existing config"),
) -> None:
    """Interactive (or flag-driven) setup. Writes ~/.memwalk/config.toml."""
    config_path = HOME_DIR / "config.toml"
    if config_path.exists() and not force:
        console.print(f"[yellow]Config already exists at {config_path}[/yellow]")
        console.print("Use --force to overwrite, or edit the file by hand.")
        raise typer.Exit(1)

    console.print(f"[bold cyan]memwalk init v{__version__}[/bold cyan]\n")

    # Model
    if model is None:
        console.print(DEFAULT_MODEL_HINT + "\n")
        model = Prompt.ask("Path to GGUF model")
    model_p = Path(model).expanduser()
    if not model_p.exists():
        console.print(f"[yellow]warning: {model_p} doesn't exist yet[/yellow]")

    # Scan paths
    if scan_path:
        scan_paths = [Path(p).expanduser() for p in scan_path]
    else:
        default = str(Path.home() / "Desktop/Coding")
        raw = Prompt.ask(
            "Directories to scan for git repos (comma-separated)",
            default=default,
        )
        scan_paths = [Path(p.strip()).expanduser() for p in raw.split(",") if p.strip()]

    cfg = Config(
        model_path=model_p,
        git=GitConfig(scan_paths=scan_paths),
        bash=BashConfig(enabled=not no_bash),
    )
    write_config(cfg)

    console.print(f"\n[green]✓[/green] Wrote {cfg.config_path}")
    console.print(f"[green]✓[/green] State dir: {cfg.state_dir}")
    console.print(f"\nNext: [bold]memwalk update[/bold]  to ingest the last 30 days")


# ── update ────────────────────────────────────────────────────────

@cli.command(name="update")
def update_cmd(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Ingest new git+bash events since last update."""
    cfg = load_config()
    with console.status("Ingesting…"):
        result = update(cfg, verbose=verbose)

    if result["ingested"] == 0:
        console.print(f"[dim]No new activity since {result['since'].strftime('%Y-%m-%d %H:%M')}[/dim]")
        return

    console.print(
        f"[green]✓[/green] Ingested {result['ingested']} events "
        f"([cyan]{result['git']}[/cyan] commits + "
        f"[cyan]{result['bash']}[/cyan] shell sessions) "
        f"in {result['elapsed_s']:.1f}s"
    )
    console.print(f"  Window: {result['since'].strftime('%Y-%m-%d %H:%M')} → "
                  f"{result['until'].strftime('%Y-%m-%d %H:%M')}")
    console.print(f"  State : {result['state_size']:,} bytes"
                  + ("  (daily snapshot taken)" if result["snapshotted"] else ""))


# ── ask ───────────────────────────────────────────────────────────

@cli.command()
def ask(
    question: str = typer.Argument(..., help="What to ask the model"),
    max_tokens: int = typer.Option(400, "--max-tokens"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Query the current state."""
    cfg = load_config()
    with console.status("Thinking…"):
        answer = query(cfg, question, max_tokens=max_tokens, verbose=verbose)
    console.print(answer)


# ── standup ───────────────────────────────────────────────────────

@cli.command()
def standup(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate a brief 'what I did + what's next' summary from recent activity."""
    cfg = load_config()
    q = (
        "Generate my daily standup notes. Cover: what I worked on yesterday "
        "(grouped by project), what I plan today based on the trajectory, and "
        "any blockers visible in the activity. Be concise — bullet points, "
        "no preamble."
    )
    with console.status("Thinking…"):
        answer = query(cfg, q, max_tokens=500, verbose=verbose)
    console.print("[bold cyan]Standup:[/bold cyan]\n")
    console.print(answer)


# ── status ────────────────────────────────────────────────────────

@cli.command()
def status() -> None:
    """Show config and state info."""
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    last = read_last_update(cfg)
    table = Table(show_header=False, box=None)
    table.add_row("[bold]config[/bold]",          str(cfg.config_path))
    table.add_row("[bold]model[/bold]",           str(cfg.model_path))
    table.add_row("[bold]state[/bold]",
                  f"{cfg.state_path}  ({cfg.state_path.stat().st_size:,} B)"
                  if cfg.state_path.exists() else f"{cfg.state_path}  (none)")
    table.add_row("[bold]scan paths[/bold]",      ", ".join(str(p) for p in cfg.git.scan_paths))
    table.add_row("[bold]bash[/bold]",            "on" if cfg.bash.enabled else "off")
    table.add_row("[bold]last update[/bold]",
                  last.strftime("%Y-%m-%d %H:%M") if last else "never")
    if cfg.snapshots_dir.exists():
        snaps = sorted(cfg.snapshots_dir.glob("*.memb"))
        table.add_row("[bold]snapshots[/bold]",
                      f"{len(snaps)} " + (f"(latest {snaps[-1].stem})" if snaps else ""))
    console.print(table)


# ── prune ─────────────────────────────────────────────────────────

@cli.command()
def prune(
    keep_days: int = typer.Option(90, "--keep-days", help="Snapshots older than this are deleted"),
) -> None:
    """Delete old daily snapshots."""
    cfg = load_config()
    n = prune_old(cfg, keep_days=keep_days)
    console.print(f"[dim]Deleted {n} snapshots older than {keep_days} days[/dim]")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
