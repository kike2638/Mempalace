# Sync

Multi-device replication for MemPalace. Keep your palace synchronized across machines using a hub-and-spoke model over HTTP.

## Requirements

- **LanceDB backend** — sync requires LanceDB (the default since v4). ChromaDB palaces must be migrated first (`mempalace migrate`).
- **Server extras** — the hub needs `pip install mempalace[server]` (FastAPI + uvicorn).

## Architecture

Sync uses a hub-and-spoke model:

- **Hub** — a machine running `mempalace serve`. Acts as the central relay. Any spoke's changes flow through the hub to other spokes.
- **Spokes** — machines running `mempalace sync`. Push local changes to the hub, pull remote changes from it.

```
  Laptop A (spoke)         Server (hub)           Laptop B (spoke)
       │                      │                        │
       ├── push ─────────────►│                        │
       │                      ├── pull by B ──────────►│
       │                      │◄── push by B ──────────┤
       │◄── pull ─────────────┤                        │
```

### Identity

Each machine gets a unique **node_id** — a 12-character hex string generated on first use and persisted at `~/.mempalace/node_id`.

### Sequence numbers

Every write operation gets a monotonically increasing **seq** number (persisted at `~/.mempalace/seq`). The combination of `node_id + seq` uniquely identifies every write across all machines. Sequence allocation uses file locking for thread safety.

### Version vectors

A **version vector** tracks the highest `seq` seen from each node. When syncing, a spoke sends its version vector to the hub, and the hub returns only records with `seq` values higher than what the spoke has seen.

Version vectors are persisted at `<palace_path>/version_vector.json`.

### Conflict resolution

When the same drawer ID exists on both sides:

1. **Last-writer-wins** — the record with the later `updated_at` timestamp wins.
2. **Tiebreak** — on identical timestamps, the lexicographically higher `node_id` wins. This is arbitrary but deterministic — both sides reach the same conclusion without coordination.

New records (ID never seen locally) are accepted unconditionally.

## Server setup

Install server dependencies:

```bash
pip install mempalace[server]
```

Start the sync server:

```bash
mempalace serve --host 0.0.0.0 --port 7433
```

| Flag | Default | Description |
|------|---------|-------------|
| `--host` | `127.0.0.1` | Bind address. Use `0.0.0.0` to accept remote connections. |
| `--port` | `7433` | Port number. |

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Health check (`{"status": "ok"}`) |
| `GET` | `/sync/status` | Server's node_id, version vector, drawer count |
| `POST` | `/sync/push` | Receive a changeset from a client |
| `POST` | `/sync/pull` | Send records the client hasn't seen |

## Client usage

### One-time sync

```bash
mempalace sync --server http://homeserver:7433
```

This performs a full bidirectional sync:

1. **Push** — send records the server hasn't seen.
2. **Pull** — receive records the client hasn't seen, apply with conflict resolution.

### Continuous sync

```bash
mempalace sync --server http://homeserver:7433 --auto --interval 300
```

Repeats sync every 300 seconds (5 minutes). Runs until interrupted.

### Dry run

```bash
mempalace sync --server http://homeserver:7433 --dry-run
```

Shows what would be synced without making changes.

## Sync metadata

Every record written to the palace includes three sync fields:

| Field | Type | Description |
|-------|------|-------------|
| `node_id` | string | Which machine wrote this record |
| `seq` | integer | Monotonic counter on that machine |
| `updated_at` | string | ISO 8601 UTC timestamp |

These fields are stored as indexed LanceDB columns (`node_id`, `seq`) for efficient filtering during sync. The sync engine uses `WHERE node_id = ? AND seq > ?` queries to find only new records.

## Python API

```python
from mempalace.db import open_collection
from mempalace.sync import SyncEngine
from mempalace.sync_meta import NodeIdentity
from mempalace.sync_client import SyncClient

# Set up engine
col = open_collection("~/.mempalace/palace")
identity = NodeIdentity()
engine = SyncEngine(col, identity=identity, vv_path="path/to/version_vector.json")

# Manual push/pull
changeset = engine.get_changes_since(remote_vv)  # records to send
result = engine.apply_changes(changeset)          # apply received records

# HTTP client
client = SyncClient("http://homeserver:7433")
if client.is_reachable():
    result = client.sync(engine)  # full bidirectional sync
    print(f"Push: {result['push']['sent']} sent, {result['push']['accepted']} accepted")
    print(f"Pull: {result['pull']['received']} received, {result['pull']['accepted']} accepted")
```

## Security considerations

- Sync uses plain HTTP. For security over untrusted networks, use a reverse proxy with TLS or an SSH tunnel.
- The server binds to `127.0.0.1` by default. Use `--host 0.0.0.0` only on trusted networks.
- There is no authentication. Anyone who can reach the server can push and pull records.
- All data stays on your machines — no cloud services are involved.

## Migration from ChromaDB

Sync requires LanceDB. If your palace uses the ChromaDB backend:

```bash
mempalace migrate
```

This reads all drawers from ChromaDB, re-embeds them with the configured embedder, and writes them to a new LanceDB palace.
