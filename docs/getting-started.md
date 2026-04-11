# Getting started

## Prerequisites

- Python 3.9 or later
- ~200 MB disk space for LanceDB and embedding model

## Installation

```bash
pip install mempalace
```

This installs MemPalace and its dependencies (`lancedb`, `onnxruntime`, `tokenizers`, `pyyaml`). No API keys or accounts needed.

On first run, the ONNX embedding model (~87 MB) is downloaded and cached at `~/.cache/mempalace/`.

Optional extras:

```bash
pip install mempalace[chroma]     # legacy ChromaDB backend (for migration)
pip install mempalace[gpu]         # GPU embeddings via sentence-transformers
pip install mempalace[server]      # sync server (FastAPI + uvicorn)
```

To install from source:

```bash
git clone https://github.com/milla-jovovich/mempalace.git
cd mempalace
pip install -e .
```

## Create your first palace

### 1. Initialize

Point `init` at a project directory. It scans the folder structure to detect rooms (topics) and optionally detects people and project names from file contents.

```bash
mempalace init ~/projects/myapp
```

This creates:

- `~/.mempalace/config.json` — global configuration
- `~/.mempalace/palace/` — the LanceDB vector store (default location)
- `~/projects/myapp/entities.json` — detected people and projects (if any found)

The `init` command is interactive — it asks you to confirm detected entities. Use `--yes` to auto-accept everything.

### 2. Mine project files

```bash
mempalace mine ~/projects/myapp
```

This scans the directory for code, docs, markdown, text, and config files. Each file is chunked by paragraph and stored as drawers in the palace. Files matching `.gitignore` patterns are skipped by default.

The wing name defaults to the directory name (`myapp`). Override with `--wing`:

```bash
mempalace mine ~/projects/myapp --wing my-web-app
```

### 3. Mine conversations

If you have conversation exports from Claude, ChatGPT, Slack, or Codex:

```bash
mempalace mine ~/chats/claude-sessions/ --mode convos --wing myapp
```

Conversation mining chunks by exchange pair (one user message + one assistant response). MemPalace auto-detects the format — see [mining.md](mining.md) for supported formats.

### 4. Search

```bash
mempalace search "why did we switch to GraphQL"
```

Filter by wing or room:

```bash
mempalace search "auth decisions" --wing myapp
mempalace search "pricing" --wing myapp --room billing
```

### 5. Check what's stored

```bash
mempalace status
```

Shows total drawers, wings, and rooms in your palace.

## Connect to your AI assistant

MemPalace is most useful when your AI assistant can access it directly via MCP.

### Claude Code

```bash
claude mcp add mempalace -- python -m mempalace.mcp_server
```

Restart Claude Code, then ask it anything about your past work. It calls `mempalace_search` automatically.

### Gemini CLI

```bash
gemini mcp add mempalace /path/to/python -m mempalace.mcp_server --scope user
```

Use the absolute path to your Python binary if using a virtual environment.

### Other MCP-compatible tools

Start the server directly:

```bash
python -m mempalace.mcp_server
```

The MCP server communicates via JSON-RPC over stdin/stdout. See [mcp-server.md](mcp-server.md) for the full tool reference.

### Local models (no MCP support)

For models that don't speak MCP, generate a context file:

```bash
mempalace wake-up > context.txt
```

Paste the contents into your model's system prompt. This gives it ~600–900 tokens of identity and key facts. For specific queries, search on demand:

```bash
mempalace search "auth decisions" > results.txt
```

## Next steps

- [Mining guide](mining.md) — conversation formats, general extraction, splitting mega-files
- [MCP server](mcp-server.md) — full tool reference, integration patterns
- [Knowledge graph](knowledge-graph.md) — tracking facts that change over time
- [Configuration](configuration.md) — customizing paths, wings, and identity
- [Sync](sync.md) — multi-device replication
- [Architecture](architecture.md) — how the palace model works
