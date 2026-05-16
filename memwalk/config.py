"""Minimal memwalk config — just model + inference defaults.

v0.2 dropped scan_paths / bash settings; codebase paths are passed
per-command instead, so config has no per-corpus knobs.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


HOME_DIR = Path.home() / ".memwalk"
CONFIG_PATH = HOME_DIR / "config.toml"

DEFAULT_MODEL_HINT = (
    "Recommended: NVIDIA Nemotron-3-Nano-4B-GGUF (hybrid Mamba-Transformer, "
    "1M-token training context).\n"
    "Download with: hf download nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF \\\n"
    "                NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf \\\n"
    "                --local-dir ~/.memwalk/models"
)


@dataclass
class Config:
    model_path: Path
    n_gpu_layers: int = -1
    n_ctx: int = 32768


def load_config() -> Config:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"No config at {CONFIG_PATH}. Run `memwalk init` first."
        )
    with CONFIG_PATH.open("rb") as f:
        data = tomllib.load(f)
    return Config(
        model_path=Path(data["model_path"]).expanduser(),
        n_gpu_layers=int(data.get("n_gpu_layers", -1)),
        n_ctx=int(data.get("n_ctx", 32768)),
    )


def write_config(cfg: Config) -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# memwalk config — edit by hand or rerun `memwalk init --force`",
        f'model_path   = "{cfg.model_path}"',
        f"n_gpu_layers = {cfg.n_gpu_layers}",
        f"n_ctx        = {cfg.n_ctx}",
    ]
    CONFIG_PATH.write_text("\n".join(lines) + "\n")
