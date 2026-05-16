# memwalk

> Walk through your work memory.

A local-first CLI that watches your git activity (and optionally your shell
history) and feeds it into a Mamba-based LLM via persistent state.  You can
then ask in plain English what you were doing last week, why you started that
branch, or generate a standup from yesterday's commits — without your data
ever leaving the machine.

Built on **[memba](https://github.com/emil28092005/Memba)** for state
persistence and **[NVIDIA Nemotron-3-Nano-4B](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF)**
(or any other GGUF SSM/hybrid model) for inference.

## Status

v0.1 — alpha.  Works end-to-end on Linux for the maintainer; APIs and on-disk
format may change.

## Install

```bash
# Until memba is on PyPI, install both editable from local clones:
pip install -e /path/to/Memba
pip install -e /path/to/memwalk
```

Make sure you have a GGUF Mamba-2 or hybrid model.  Recommended:

```bash
hf download nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF \
    NVIDIA-Nemotron3-Nano-4B-Q4_K_M.gguf \
    --local-dir ~/.memwalk/models
```

## Quickstart

```bash
memwalk init                                  # interactive setup
memwalk update                                # ingest last 30 days of git+bash
memwalk standup                               # auto-generate daily standup
memwalk ask "What was I working on last week?"
memwalk status
```

## What it actually does

`memwalk update` walks your configured git repos and (optionally) your bash
history, formats new events into a readable activity block, and feeds that
into the SSM model.  The model's hidden state — a fixed ~85 MB blob — is
saved to `~/.memwalk/current.memb` via memba.

`memwalk ask` and `memwalk standup` load that state and query it.  The model
recalls themes, projects, and trajectory across processes and reboots.

## Use from an agent (MCP)

memwalk ships an MCP server so Claude Code / opencode / any MCP-aware
agent can query your memory as native tools.

```bash
memwalk mcp     # starts a stdio MCP server
```

Tools exposed: `ask(question)`, `standup()`, `status()`, `update()`.
The Session loads lazily on the first call that needs it, then stays in
memory — first call ~2 s, subsequent calls <500 ms.

### Configure Claude Code

Easiest way (Claude Code CLI):

```bash
claude mcp add memwalk -- memwalk mcp
```

Or by hand, in `~/.claude/mcp_servers.json` (path may vary by version):

```json
{
  "mcpServers": {
    "memwalk": {
      "command": "memwalk",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Code. Tools appear as `mcp__memwalk__ask`,
`mcp__memwalk__standup`, etc.

### Configure opencode

opencode uses its own MCP block in `opencode.json`:

```json
{
  "mcpServers": {
    "memwalk": { "command": "memwalk", "args": ["mcp"] }
  }
}
```

## Layout

```
~/.memwalk/
├── config.toml
├── current.memb              ← rolling state
├── last_update.txt
└── snapshots/
    └── 2026-05-16.memb       ← daily snapshot before each update
```

## License

MIT.
