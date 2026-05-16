"""Codebase walker — produces a single ingest-ready text block + a stable
manifest hash for cache invalidation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

# Source-ish file extensions we read by default.  Override with --extensions.
DEFAULT_INCLUDE_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".pyi",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cxx",
    ".rs", ".go", ".java", ".kt", ".scala", ".swift",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".rb", ".php", ".cs", ".fs", ".ex", ".exs", ".erl", ".clj", ".cljs",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".toml", ".yaml", ".yml", ".json", ".xml", ".ini", ".cfg", ".conf",
    ".md", ".rst", ".txt",
    ".sql", ".graphql", ".proto",
    ".dockerfile", ".tf", ".hcl",
})

# Directories we never descend into.
DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    "__pycache__", "node_modules", "vendor", "third_party",
    ".venv", "venv", "env", ".env",
    "build", "dist", "target", "out", "bin", "obj",
    ".next", ".nuxt", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "coverage", ".coverage", "htmlcov",
    ".idea", ".vscode",
    "llama.cpp",  # common vendored ML dep — too big
})

# Glob patterns for files we always skip.
DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "*.gguf", "*.safetensors", "*.bin", "*.onnx", "*.pt", "*.pth",
    "*.so", "*.so.*", "*.dylib", "*.dll",
    "*.o", "*.a", "*.obj", "*.exe",
    "*.pyc", "*.pyo",
    "*.memb",
    "package-lock.json", "yarn.lock", "Cargo.lock", "uv.lock",
    "poetry.lock", "Pipfile.lock", "*.lock",
)

DEFAULT_MAX_FILE_BYTES: int = 64 * 1024


@dataclass(frozen=True, slots=True)
class CorpusFile:
    rel_path: str   # path relative to corpus root, forward slashes
    bytes: int
    mtime_ns: int
    text: str       # file content (UTF-8, replaced on errors)


def collect_files(
    root: Path,
    *,
    include_suffixes: frozenset[str] = DEFAULT_INCLUDE_SUFFIXES,
    exclude_dirs: frozenset[str] = DEFAULT_EXCLUDE_DIRS,
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> list[CorpusFile]:
    """Walk @root, return CorpusFile entries sorted by relative path."""
    out: list[CorpusFile] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in exclude_dirs for part in p.parts):
            continue
        if p.suffix and p.suffix not in include_suffixes:
            continue
        if not p.suffix and p.name.lower() not in {"dockerfile", "makefile"}:
            continue
        if any(p.match(pat) for pat in exclude_patterns):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > max_file_bytes:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = p.relative_to(root).as_posix()
        out.append(CorpusFile(
            rel_path=rel, bytes=st.st_size, mtime_ns=st.st_mtime_ns, text=text,
        ))
    out.sort(key=lambda f: f.rel_path)
    return out


def build_corpus(root: Path, files: list[CorpusFile]) -> str:
    """Format files into a single text block with a manifest at the top."""
    if not files:
        return ""
    n_chars = sum(len(f.text) for f in files)
    header = (
        f"=== CODEBASE: {root.name} ===\n"
        f"{len(files)} files, {n_chars:,} characters total.\n\n"
        f"File manifest:\n"
        + "\n".join(f"  {f.rel_path}" for f in files)
        + "\n"
    )
    bodies = "\n".join(
        f"\n=== {f.rel_path} ({f.bytes} bytes) ===\n{f.text}"
        for f in files
    )
    return header + bodies


def manifest_hash(files: list[CorpusFile]) -> str:
    """Stable SHA-256 over (rel_path, size, mtime_ns) tuples.
    Changes whenever any included file is added, removed, or modified.
    Returns 16 hex chars (enough for cache key uniqueness, easy to log)."""
    h = hashlib.sha256()
    for f in files:
        h.update(f"{f.rel_path}\0{f.bytes}\0{f.mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()[:16]
