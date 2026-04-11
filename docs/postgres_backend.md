# PostgreSQL Backend

MemPalace uses ChromaDB by default. The PostgreSQL backend is optional and is
intended for larger, long-lived, or team/server deployments where a local
Chroma directory is not the right storage boundary.

The backend supports two database extension paths:

- `pg_sorted_heap` (recommended): uses `sorted_heap`, `svec`, and `sorted_hnsw`.
- `pgvector` (fallback): uses a regular heap table, `vector`, and `hnsw`.

When both extensions are available in the target database, MemPalace prefers
`pg_sorted_heap`.

## Install MemPalace Dependencies

```bash
pip install "mempalace[postgres]"
```

The PostgreSQL extra installs the Python driver and text embedding dependency.
If your application always passes embeddings directly, only the driver is used
on that path, but text queries still require the full extra.

## Install `pg_sorted_heap`

Requirements:

- PostgreSQL 17 or 18.
- `pg_config` for the PostgreSQL version you want to use.
- Standard PGXS build tools (`make`, compiler toolchain, PostgreSQL server
  development files).
- Database privileges to run `CREATE EXTENSION`.

Automated helper (from a source checkout of the MemPalace repository):

```bash
scripts/install_pg_backend.sh --dsn "postgresql://mempalace_user@localhost:5432/mempalace"
```

The helper clones `https://github.com/skuznetsov/pg_sorted_heap.git`, runs
`make`, runs `make install`, verifies the installed control/library files, and
then creates the extension in the database if `--dsn` is supplied.

Use an explicit PostgreSQL installation when multiple versions are installed:

```bash
scripts/install_pg_backend.sh \
  --pg-config /usr/lib/postgresql/18/bin/pg_config \
  --dsn "postgresql://mempalace_user@localhost:5432/mempalace"
```

Build from an existing checkout instead of cloning:

```bash
scripts/install_pg_backend.sh \
  --source /path/to/pg_sorted_heap \
  --dsn "postgresql://mempalace_user@localhost:5432/mempalace"
```

Manual installation:

```bash
git clone https://github.com/skuznetsov/pg_sorted_heap.git
cd pg_sorted_heap
make
make install
psql "postgresql://mempalace_user@localhost:5432/mempalace" \
  -c "CREATE EXTENSION IF NOT EXISTS pg_sorted_heap;"
```

If `make install` needs elevated permissions for your PostgreSQL installation,
run the helper with `--sudo`, or run the manual `make install` step with the
appropriate privilege escalation for your environment.

## Fallback: Install `pgvector`

If `pg_sorted_heap` is not installed but `pgvector` is available, MemPalace will
fall back to `pgvector` automatically.

Example:

```bash
psql "postgresql://mempalace_user@localhost:5432/mempalace" \
  -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

Use `pg_sorted_heap` when you can, because it gives MemPalace the intended
sorted storage plus planner-integrated `sorted_hnsw` path. Use `pgvector` when
you need a simpler managed-database setup or cannot install custom extensions.

## Configure MemPalace

Environment variables:

```bash
export MEMPALACE_BACKEND=postgres
export MEMPALACE_POSTGRES_DSN="postgresql://mempalace_user@localhost:5432/mempalace"

# optional, defaults to mempalace_drawers
export MEMPALACE_COLLECTION_NAME=mempalace_drawers
```

Equivalent `~/.mempalace/config.json`:

```json
{
  "backend": "postgres",
  "postgres_dsn": "postgresql://mempalace_user@localhost:5432/mempalace",
  "collection_name": "mempalace_drawers"
}
```

Then use MemPalace normally:

```bash
mempalace mine ~/projects/myapp
mempalace search "why did we change the auth flow"
```

## Verify The Backend

Check the selected PostgreSQL extension:

```sql
SELECT extname
FROM pg_extension
WHERE extname IN ('pg_sorted_heap', 'vector')
ORDER BY extname;
```

For a `pg_sorted_heap` collection, the MemPalace table should use
`sorted_heap`:

```sql
SELECT am.amname
FROM pg_class c
JOIN pg_am am ON am.oid = c.relam
WHERE c.relname = 'mempalace_drawers';
```

Expected:

```text
sorted_heap
```

For fallback `pgvector`, the table access method is the regular heap access
method and the `vector` extension should be present.

## Operational Notes

- The PostgreSQL backend creates the collection table on first write when
  `create=True`.
- For `pg_sorted_heap`, MemPalace stores drawers with primary key
  `(wing, room, id)` so wing/room locality is preserved in the sorted table.
- Vector indexes are created lazily after the collection reaches the backend's
  index threshold; small collections use exact vector ordering.
- ChromaDB remains the zero-config default and is still the benchmarked
  raw-mode path in the public README.
