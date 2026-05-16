"""GPU VRAM probing and adaptive n_ctx estimation."""

from __future__ import annotations

import shutil
import subprocess

_NEMOTRON_PROFILE: list[tuple[int, float]] = [
    (8192,  4.5),
    (16384, 5.5),
    (32768, 7.0),
    (65536, 9.5),
    (131072, 14.0),
    (262144, 22.0),
    (524288, 36.0),
]


def probe_free_vram_mb() -> int | None:
    """Return free VRAM in MiB, or None if no NVIDIA GPU detected."""
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.free",
                    "--format=csv,noheader,nounits",
                ],
                text=True,
                timeout=5,
            )
            first_line = out.strip().splitlines()[0].strip()
            return int(float(first_line))
        except Exception:
            pass

    try:
        import pynvml  # type: ignore[import-untyped]
        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        pynvml.nvmlShutdown()
        return info.free // (1024 * 1024)
    except Exception:
        pass

    return None


def estimate_max_n_ctx(free_vram_mb: int, *, headroom_mb: int = 1536) -> int:
    """Return the largest n_ctx from the profile that fits in free VRAM."""
    usable_mb = free_vram_mb - headroom_mb
    if usable_mb <= 0:
        return 8192

    best = 8192
    for n_ctx, needed_gb in _NEMOTRON_PROFILE:
        needed_mb = int(needed_gb * 1024)
        if needed_mb <= usable_mb:
            best = n_ctx
        else:
            break
    return best


def auto_n_ctx(preferred: int | None = None) -> int:
    """Return n_ctx to use: preferred if given, else GPU-adaptive."""
    if preferred is not None and preferred > 0:
        return preferred

    free_mb = probe_free_vram_mb()
    if free_mb is None:
        return 32768
    return estimate_max_n_ctx(free_mb)
