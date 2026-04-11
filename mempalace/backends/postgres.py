"""Optional PostgreSQL-backed MemPalace collection adapter."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .base import BaseCollection

logger = logging.getLogger("mempalace.postgres")

EMBEDDING_DIM = 384
EMBEDDING_MODEL = "all-MiniLM-L6-v2"
VECTOR_INDEX_MIN_ROWS = 5_000

_embedder = None


def _load_psycopg2():
    try:
        import psycopg2
        from psycopg2 import sql
    except ImportError as exc:  # pragma: no cover - exercised by users without the extra.
        raise RuntimeError(
            "PostgreSQL backend requires optional dependencies. "
            'Install with: pip install "mempalace[postgres]"'
        ) from exc
    return psycopg2, sql


def _embed(texts: list[str]) -> list[list[float]]:
    """Embed texts for PostgreSQL vector search."""
    global _embedder
    if _embedder is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - exercised by users without the extra.
            raise RuntimeError(
                "PostgreSQL backend text queries require sentence-transformers. "
                'Install with: pip install "mempalace[postgres]"'
            ) from exc

        _embedder = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Loaded embedding model: %s", EMBEDDING_MODEL)

    vectors = _embedder.encode(texts, normalize_embeddings=True)
    return vectors.tolist()


def _vec_literal(vector: list[float]) -> str:
    """Convert a vector to a PostgreSQL vector/svec literal."""
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def _metadata_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class PostgresCollection(BaseCollection):
    """PostgreSQL collection adapter compatible with MemPalace's BaseCollection."""

    def __init__(self, dsn: str, table_name: str = "mempalace_drawers"):
        self.dsn = dsn
        self.table_name = table_name
        self._conn = None
        self._vec_type: Optional[str] = None
        self._table_am: Optional[str] = None
        self._index_am: Optional[str] = None
        self._setup_done = False

    def add(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict[str, Any]]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        """Add documents with optional caller-provided embeddings."""
        if len(documents) != len(ids):
            raise ValueError("documents and ids must have the same length")
        self._ensure_setup()
        if embeddings is None:
            embeddings = _embed(documents)
        if len(embeddings) != len(documents):
            raise ValueError("embeddings and documents must have the same length")

        conn = self._get_conn()
        cur = conn.cursor()
        for index, (doc_id, document) in enumerate(zip(ids, documents)):
            metadata = dict(metadatas[index]) if metadatas and index < len(metadatas) else {}
            wing = _metadata_value(metadata.pop("wing", ""))
            room = _metadata_value(metadata.pop("room", ""))
            embedding = _vec_literal(embeddings[index])

            if self._table_am == "sorted_heap":
                cur.execute(
                    self._sql.SQL("SELECT 1 FROM {} WHERE id = %s LIMIT 1").format(self._table_id),
                    (doc_id,),
                )
                if cur.fetchone():
                    continue
                cur.execute(
                    self._sql.SQL(
                        "INSERT INTO {} (wing, room, id, document, embedding, metadata) "
                        "VALUES (%s, %s, %s, %s, %s::{}, %s::jsonb)"
                    ).format(self._table_id, self._vec_type_sql),
                    (wing, room, doc_id, document, embedding, json.dumps(metadata)),
                )
            else:
                cur.execute(
                    self._sql.SQL(
                        "INSERT INTO {} (id, wing, room, document, embedding, metadata) "
                        "VALUES (%s, %s, %s, %s, %s::{}, %s::jsonb) "
                        "ON CONFLICT (id) DO NOTHING"
                    ).format(self._table_id, self._vec_type_sql),
                    (doc_id, wing, room, document, embedding, json.dumps(metadata)),
                )

        self._maybe_create_vector_index()

    def upsert(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: Optional[list[dict[str, Any]]] = None,
        embeddings: Optional[list[list[float]]] = None,
    ) -> None:
        self.delete(ids=ids)
        self.add(documents=documents, ids=ids, metadatas=metadatas, embeddings=embeddings)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self._ensure_setup()
        query_embeddings = kwargs.get("query_embeddings")
        query_texts = kwargs.get("query_texts")
        n_results = kwargs.get("n_results", 5)
        where = kwargs.get("where")

        if query_embeddings:
            query_embedding = query_embeddings[0]
        elif query_texts:
            query_embedding = _embed(query_texts[:1])[0]
        else:
            return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        where_sql, where_params = self._where_to_sql(where)
        where_clause = (
            self._sql.SQL("WHERE {}").format(where_sql) if where_sql else self._sql.SQL("")
        )
        embedding = _vec_literal(query_embedding)

        cur = self._get_conn().cursor()
        cur.execute(
            self._sql.SQL(
                "SELECT id, document, wing, room, metadata, embedding <=> %s::{} AS distance "
                "FROM {} {} "
                "ORDER BY embedding <=> %s::{} "
                "LIMIT %s"
            ).format(self._vec_type_sql, self._table_id, where_clause, self._vec_type_sql),
            [embedding, *where_params, embedding, int(n_results)],
        )
        rows = cur.fetchall()

        result_ids = []
        result_documents = []
        result_metadatas = []
        result_distances = []
        for row in rows:
            doc_id, document, wing, room, metadata, distance = row
            result_ids.append(doc_id)
            result_documents.append(document)
            result_metadatas.append(self._metadata_dict(wing, room, metadata))
            result_distances.append(float(distance))

        return {
            "ids": [result_ids],
            "documents": [result_documents],
            "metadatas": [result_metadatas],
            "distances": [result_distances],
        }

    def get(self, **kwargs: Any) -> dict[str, Any]:
        self._ensure_setup()
        ids = kwargs.get("ids")
        where = kwargs.get("where")
        limit = kwargs.get("limit")
        offset = kwargs.get("offset", 0)
        include = kwargs.get("include")

        clauses = []
        params: list[Any] = []
        if ids:
            placeholders = self._sql.SQL(", ").join(self._sql.Placeholder() for _ in ids)
            clauses.append(self._sql.SQL("id IN ({})").format(placeholders))
            params.extend(ids)
        if where:
            where_sql, where_params = self._where_to_sql(where)
            if where_sql:
                clauses.append(where_sql)
                params.extend(where_params)

        where_clause = (
            self._sql.SQL("WHERE {}").format(self._sql.SQL(" AND ").join(clauses))
            if clauses
            else self._sql.SQL("")
        )
        limit_clause = self._sql.SQL("LIMIT %s") if limit else self._sql.SQL("")
        offset_clause = self._sql.SQL("OFFSET %s") if offset else self._sql.SQL("")
        if limit:
            params.append(int(limit))
        if offset:
            params.append(int(offset))

        cur = self._get_conn().cursor()
        cur.execute(
            self._sql.SQL("SELECT id, document, wing, room, metadata FROM {} {} {} {}").format(
                self._table_id, where_clause, limit_clause, offset_clause
            ),
            params,
        )
        rows = cur.fetchall()

        result = {"ids": [row[0] for row in rows]}
        if include is None or "documents" in include:
            result["documents"] = [row[1] for row in rows]
        if include is None or "metadatas" in include:
            result["metadatas"] = [self._metadata_dict(row[2], row[3], row[4]) for row in rows]
        return result

    def delete(self, **kwargs: Any) -> None:
        self._ensure_setup()
        ids = kwargs.get("ids")
        where = kwargs.get("where")
        if not ids and not where:
            return

        clauses = []
        params: list[Any] = []
        if ids:
            placeholders = self._sql.SQL(", ").join(self._sql.Placeholder() for _ in ids)
            clauses.append(self._sql.SQL("id IN ({})").format(placeholders))
            params.extend(ids)
        if where:
            where_sql, where_params = self._where_to_sql(where)
            if where_sql:
                clauses.append(where_sql)
                params.extend(where_params)

        if not clauses:
            return
        cur = self._get_conn().cursor()
        cur.execute(
            self._sql.SQL("DELETE FROM {} WHERE {}").format(
                self._table_id, self._sql.SQL(" AND ").join(clauses)
            ),
            params,
        )

    def count(self) -> int:
        self._ensure_setup()
        cur = self._get_conn().cursor()
        cur.execute(self._sql.SQL("SELECT COUNT(*) FROM {}").format(self._table_id))
        return cur.fetchone()[0]

    @property
    def _sql(self):
        _psycopg2, sql = _load_psycopg2()
        return sql

    @property
    def _table_id(self):
        return self._sql.Identifier(self.table_name)

    @property
    def _vec_type_sql(self):
        if not self._vec_type:
            raise RuntimeError("PostgreSQL vector type was not detected")
        return self._sql.SQL(self._vec_type)

    def _get_conn(self):
        psycopg2, _sql = _load_psycopg2()
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = True
        return self._conn

    def _detect_extensions(self, *, create: bool = False) -> None:
        if self._vec_type:
            return

        cur = self._get_conn().cursor()
        cur.execute(
            "SELECT extname FROM pg_extension WHERE extname IN ('pg_sorted_heap', 'vector')"
        )
        installed = {row[0] for row in cur.fetchall()}

        if "pg_sorted_heap" in installed:
            self._vec_type = "svec"
            self._table_am = "sorted_heap"
            self._index_am = "sorted_hnsw"
        elif "vector" in installed:
            self._vec_type = "vector"
            self._table_am = "heap"
            self._index_am = "hnsw"
        elif create:
            for extension, vec_type, table_am, index_am in (
                ("pg_sorted_heap", "svec", "sorted_heap", "sorted_hnsw"),
                ("vector", "vector", "heap", "hnsw"),
            ):
                try:
                    cur.execute(
                        self._sql.SQL("CREATE EXTENSION IF NOT EXISTS {}").format(
                            self._sql.Identifier(extension)
                        )
                    )
                    self._vec_type = vec_type
                    self._table_am = table_am
                    self._index_am = index_am
                    break
                except Exception:
                    continue

        if not self._vec_type:
            raise RuntimeError(
                "PostgreSQL backend requires pgvector or pg_sorted_heap. "
                "Install one of them with CREATE EXTENSION before opening read-only collections."
            )

    def _table_exists(self) -> bool:
        cur = self._get_conn().cursor()
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = %s",
            (self.table_name,),
        )
        return cur.fetchone() is not None

    def _ensure_setup(self) -> None:
        if self._setup_done:
            return

        self._detect_extensions(create=True)
        cur = self._get_conn().cursor()
        self._create_table(cur)
        self._setup_done = True

    def _create_table(self, cur) -> None:
        if self._table_exists():
            return

        vec_type = self._sql.SQL("{}({})").format(
            self._vec_type_sql, self._sql.SQL(str(EMBEDDING_DIM))
        )
        if self._table_am == "sorted_heap":
            cur.execute(
                self._sql.SQL(
                    "CREATE TABLE {} ("
                    "wing text COLLATE \"C\" NOT NULL DEFAULT '', "
                    "room text COLLATE \"C\" NOT NULL DEFAULT '', "
                    "id text NOT NULL, "
                    "document text NOT NULL, "
                    "embedding {}, "
                    "metadata jsonb DEFAULT '{{}}', "
                    "PRIMARY KEY (wing, room, id)"
                    ") USING sorted_heap"
                ).format(self._table_id, vec_type)
            )
            cur.execute(
                self._sql.SQL("CREATE UNIQUE INDEX {} ON {} USING btree (id)").format(
                    self._sql.Identifier(f"{self.table_name}_id_idx"), self._table_id
                )
            )
        else:
            cur.execute(
                self._sql.SQL(
                    "CREATE TABLE {} ("
                    "id text PRIMARY KEY, "
                    "wing text NOT NULL DEFAULT '', "
                    "room text NOT NULL DEFAULT '', "
                    "document text NOT NULL, "
                    "embedding {}, "
                    "metadata jsonb DEFAULT '{{}}'"
                    ")"
                ).format(self._table_id, vec_type)
            )
            for column in ("wing", "room"):
                cur.execute(
                    self._sql.SQL("CREATE INDEX {} ON {} ({})").format(
                        self._sql.Identifier(f"{self.table_name}_{column}_idx"),
                        self._table_id,
                        self._sql.Identifier(column),
                    )
                )

        logger.info(
            "Created PostgreSQL collection %s (%s, %s)",
            self.table_name,
            self._table_am,
            self._vec_type,
        )

    def _maybe_create_vector_index(self) -> None:
        cur = self._get_conn().cursor()
        index_name = f"{self.table_name}_vec_idx"
        cur.execute("SELECT 1 FROM pg_indexes WHERE indexname = %s", (index_name,))
        if cur.fetchone():
            return

        if self.count() < VECTOR_INDEX_MIN_ROWS:
            return

        ops = "svec_cosine_ops" if self._vec_type == "svec" else "vector_cosine_ops"
        cur.execute(
            self._sql.SQL("CREATE INDEX {} ON {} USING {} (embedding {})").format(
                self._sql.Identifier(index_name),
                self._table_id,
                self._sql.SQL(self._index_am),
                self._sql.SQL(ops),
            )
        )

    def _where_to_sql(self, where: Optional[dict[str, Any]]):
        if not where:
            return None, []

        if "$and" in where:
            parts = []
            params = []
            for condition in where["$and"]:
                clause, clause_params = self._where_to_sql(condition)
                if clause:
                    parts.append(self._sql.SQL("({})").format(clause))
                    params.extend(clause_params)
            if not parts:
                return None, []
            return self._sql.SQL(" AND ").join(parts), params

        clauses = []
        params = []
        for key, value in where.items():
            if key.startswith("$"):
                continue
            if key in ("wing", "room"):
                clauses.append(self._sql.SQL("{} = %s").format(self._sql.Identifier(key)))
                params.append(_metadata_value(value))
            else:
                clauses.append(self._sql.SQL("metadata->>%s = %s"))
                params.extend([key, _metadata_value(value)])
        if not clauses:
            return None, []
        return self._sql.SQL(" AND ").join(clauses), params

    @staticmethod
    def _metadata_dict(wing: str, room: str, metadata: Any) -> dict[str, Any]:
        result = dict(metadata) if isinstance(metadata, dict) else {}
        result["wing"] = wing
        result["room"] = room
        return result


class PostgresBackend:
    """Factory for optional PostgreSQL collections."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._collections: dict[str, PostgresCollection] = {}

    def get_collection(self, palace_path: str, collection_name: str, create: bool = False):
        del palace_path  # PostgreSQL uses the configured DSN, not a local palace directory.
        if collection_name not in self._collections:
            collection = PostgresCollection(self.dsn, table_name=collection_name)
            if not create and not collection._table_exists():
                raise FileNotFoundError(f"PostgreSQL collection does not exist: {collection_name}")
            collection._detect_extensions(create=create)
            collection._setup_done = not create
            if create:
                collection._ensure_setup()
            self._collections[collection_name] = collection
        elif create:
            self._collections[collection_name]._ensure_setup()
        return self._collections[collection_name]
