"""Config & state-tracking for memwalk.

Layout:
    ~/.memwalk/
        config.toml         # user settings
        last_update.txt     # ISO timestamp of last successful update
        current.memb        # rolling state file
        snapshots/
            2026-05-16.memb
            ...
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


HOME_DIR = Path.home() / ".memwalk"

DEFAULT_MODEL_HINT = (
    "Recommended: NVIDIA Nemotron-3-Nano-4B (hybrid Mamba+Transformer).\n"
    "Download with: hf download nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF "
    "NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf --local-dir ~/.memwalk/models"
)


@dataclass
class GitConfig:
    scan_paths: list[Path] = field(default_factory=list)
    bootstrap_days: int = 30


@dataclass
class BashConfig:
    enabled: bool = True
    history_file: Path = Path.home() / ".bash_history"
    session_gap_min: int = 30


@dataclass
class Config:
    model_path: Path
    n_gpu_layers: int = -1
    n_ctx: int = 8192
    state_dir: Path = HOME_DIR
    git: GitConfig = field(default_factory=GitConfig)
    bash: BashConfig = field(default_factory=BashConfig)

    @property
    def config_path(self) -> Path:
        return HOME_DIR / "config.toml"

    @property
    def state_path(self) -> Path:
        return self.state_dir / "current.memb"

    @property
    def snapshots_dir(self) -> Path:
        return self.state_dir / "snapshots"

    @property
    def last_update_path(self) -> Path:
        return self.state_dir / "last_update.txt"


def load_config() -> Config:
    """Load config.toml from ~/.memwalk/ or raise FileNotFoundError."""
    path = HOME_DIR / "config.toml"
    if not path.exists():
        raise FileNotFoundError(
            f"No config at {path}. Run `memwalk init` first."
        )

    with path.open("rb") as f:
        data = tomllib.load(f)

    model_path = Path(data["model_path"]).expanduser()
    git_cfg = data.get("git", {})
    bash_cfg = data.get("bash", {})

    return Config(
        model_path=model_path,
        n_gpu_layers=int(data.get("n_gpu_layers", -1)),
        n_ctx=int(data.get("n_ctx", 8192)),
        state_dir=Path(data.get("state_dir", HOME_DIR)).expanduser(),
        git=GitConfig(
            scan_paths=[Path(p).expanduser() for p in git_cfg.get("scan_paths", [])],
            bootstrap_days=int(git_cfg.get("bootstrap_days", 30)),
        ),
        bash=BashConfig(
            enabled=bool(bash_cfg.get("enabled", True)),
            history_file=Path(bash_cfg.get("history_file",
                                           Path.home() / ".bash_history")).expanduser(),
            session_gap_min=int(bash_cfg.get("session_gap_min", 30)),
        ),
    )


def write_config(cfg: Config) -> None:
    """Persist config to ~/.memwalk/config.toml using a hand-rolled writer
    (Python stdlib has no TOML writer until 3.13+)."""
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.snapshots_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# memwalk config — edit by hand or rerun `memwalk init`",
        f'model_path   = "{cfg.model_path}"',
        f"n_gpu_layers = {cfg.n_gpu_layers}",
        f"n_ctx        = {cfg.n_ctx}",
        f'state_dir    = "{cfg.state_dir}"',
        "",
        "[git]",
        "scan_paths = [",
        *[f'    "{p}",' for p in cfg.git.scan_paths],
        "]",
        f"bootstrap_days = {cfg.git.bootstrap_days}",
        "",
        "[bash]",
        f"enabled         = {str(cfg.bash.enabled).lower()}",
        f'history_file    = "{cfg.bash.history_file}"',
        f"session_gap_min = {cfg.bash.session_gap_min}",
    ]
    cfg.config_path.write_text("\n".join(lines) + "\n")


def read_last_update(cfg: Config) -> datetime | None:
    path = cfg.last_update_path
    if not path.exists():
        return None
    try:
        return datetime.fromisoformat(path.read_text().strip())
    except ValueError:
        return None


def write_last_update(cfg: Config, ts: datetime) -> None:
    cfg.last_update_path.write_text(ts.isoformat())
