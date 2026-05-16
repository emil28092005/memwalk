# memwalk

> Ask AI about any codebase — local, cached, SSM-state-backed.

`memwalk` reads an entire codebase into a Mamba-based LLM via persistent
state, so subsequent questions answer in <1 s without re-reading anything.
The state is byte-portable (via [memba](https://github.com/emil28092005/Memba))
and cached per-directory by file manifest hash, so re-asking is free until
the source changes.

Built on **memba** for state persistence and
**[NVIDIA Nemotron-3-Nano-4B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF)**
(hybrid Mamba-2 + Transformer, 1M training context) for inference.

## What makes this different from Cursor / Cody / Aider

| Tool         | Approach                       | Whole-repo question      |
|--------------|--------------------------------|--------------------------|
| Cursor       | Embed chunks, retrieve at Q    | Fragmented context       |
| Cody         | BM25 + dense embeddings (RAG)  | Pre-indexed, retrieved   |
| Aider        | Symbol-level repo map          | Signatures only          |
| **memwalk**  | **Read everything once, cache the SSM state** | Holistic answer; <1s re-asks |

SSM state is **fixed-size** (Mamba's defining property), so even a 1M-token
codebase compresses into a constant-size file (~85 MB at our settings).
Reload is millisecond-scale — re-asking a freshly-cached repo costs no
model inference until you ask the next question.

## Status

v0.2 — alpha. Works for the maintainer end-to-end; APIs and on-disk format
may shift.

## Install

memba is not on PyPI yet, so install via git:

```bash
pip install git+https://github.com/emil28092005/memwalk.git
# (pulls memba @ main as a transitive git dep)
```

Or from a local clone:

```bash
pip install -e ~/Desktop/Coding/memwalk
```

Make sure you have a GGUF Mamba-2 / hybrid model. Recommended:

```bash
hf download nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF \
    NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf \
    --local-dir ~/.memwalk/models
```

## Quickstart

```bash
memwalk init                                  # one-time: set model path
memwalk digest ~/Desktop/Coding/myrepo        # first time: read everything (~10s)
memwalk ask    ~/Desktop/Coding/myrepo "How does auth work?"   # <1s
memwalk ask    ~/Desktop/Coding/myrepo "Which file owns the migration logic?"
memwalk list                                  # show all cached codebases
memwalk drop   ~/Desktop/Coding/myrepo        # invalidate cache
memwalk status                                # config + cache summary
```

`memwalk ask` auto-digests on first use, so the explicit `digest` step is
optional. The cache is invalidated automatically when any source file
changes (mtime / size).

## Use from an AI agent (MCP)

```bash
memwalk mcp     # starts a stdio MCP server
```

Tools: `digest(path)`, `ask(path, question)`, `list_caches()`,
`drop_cache(path)`, `status()`.

### Claude Code

```bash
claude mcp add memwalk -- memwalk mcp
```

Or by hand in your MCP config:

```json
{
  "mcpServers": {
    "memwalk": { "command": "memwalk", "args": ["mcp"] }
  }
}
```

### opencode / Hermes / other MCP clients

Same shape — they all consume `{"command": "memwalk", "args": ["mcp"]}`.

After the agent connects it sees `mcp__memwalk__digest`,
`mcp__memwalk__ask`, etc. Typical flow:

> User: *"What changed in the migrations folder of my CU\_Points repo this month?"*
>
> Agent: calls `mcp__memwalk__ask(path="~/Desktop/Coding/AI/CU_Points",
> question="...")`. memwalk auto-digests if needed, returns answer.

## What does it actually do well?

Validated on memba's own codebase (13 files, ~63 K chars):

- Listed every header field of the state file format **in order**
- Explained the architectural reason for the `eval+sample` rewrite
- Identified which side of the C/Python boundary writes the MEMB trailer
- Listed all CLI subcommands accurately
- Suggested correct file path + approach for adding a new command

Recall is **descriptive-strong** — facts that are in the source. It is not
a substitute for a real debugger or a code generator. For complex
reasoning over small snippets, a bigger code-tuned model is still better.

## Limits

- **Single-shot context, not chunked retrieval.** Whole corpus must fit in
  `n_ctx` (default 32 K tokens ≈ ~120 K chars). Bigger repos: bump `n_ctx`,
  use a beefier GPU, or filter `INCLUDE_SUFFIXES` in `corpus.py`.
- **No code-aware filtering yet** — every text file under the root is
  read. Use `.gitignore`-style filtering in v0.3.
- **No GPU-less mode tested** — should work on CPU but slow.

## License

MIT.
