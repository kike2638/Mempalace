import chromadb
import pytest

from mempalace import palace
import mempalace.backends.postgres as postgres_mod
from mempalace.backends.chroma import ChromaBackend, ChromaCollection
from mempalace.backends.postgres import (
    PostgresBackend,
    PostgresCollection,
    VECTOR_INDEX_CHECK_INTERVAL_ROWS,
    _metadata_value,
    _vec_literal,
)


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def add(self, **kwargs):
        self.calls.append(("add", kwargs))

    def upsert(self, **kwargs):
        self.calls.append(("upsert", kwargs))

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {"kind": "query"}

    def get(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"kind": "get"}

    def delete(self, **kwargs):
        self.calls.append(("delete", kwargs))

    def count(self):
        self.calls.append(("count", {}))
        return 7


def test_chroma_collection_delegates_methods():
    fake = _FakeCollection()
    collection = ChromaCollection(fake)

    collection.add(documents=["d"], ids=["1"], metadatas=[{"wing": "w"}])
    collection.upsert(documents=["u"], ids=["2"], metadatas=[{"room": "r"}])
    assert collection.query(query_texts=["q"]) == {"kind": "query"}
    assert collection.get(where={"wing": "w"}) == {"kind": "get"}
    collection.delete(ids=["1"])
    assert collection.count() == 7

    assert fake.calls == [
        ("add", {"documents": ["d"], "ids": ["1"], "metadatas": [{"wing": "w"}]}),
        ("upsert", {"documents": ["u"], "ids": ["2"], "metadatas": [{"room": "r"}]}),
        ("query", {"query_texts": ["q"]}),
        ("get", {"where": {"wing": "w"}}),
        ("delete", {"ids": ["1"]}),
        ("count", {}),
    ]


def test_chroma_backend_create_false_raises_without_creating_directory(tmp_path):
    palace_path = tmp_path / "missing-palace"

    with pytest.raises(FileNotFoundError):
        ChromaBackend().get_collection(
            str(palace_path),
            collection_name="mempalace_drawers",
            create=False,
        )

    assert not palace_path.exists()


def test_chroma_backend_create_true_creates_directory_and_collection(tmp_path):
    palace_path = tmp_path / "palace"

    collection = ChromaBackend().get_collection(
        str(palace_path),
        collection_name="mempalace_drawers",
        create=True,
    )

    assert palace_path.is_dir()
    assert isinstance(collection, ChromaCollection)

    client = chromadb.PersistentClient(path=str(palace_path))
    client.get_collection("mempalace_drawers")


def test_vec_literal_formats_postgres_vector_literal():
    assert _vec_literal([1.0, 0.5, -0.25]) == "[1.00000000,0.50000000,-0.25000000]"


def test_metadata_value_matches_json_style_booleans():
    assert _metadata_value(True) == "true"
    assert _metadata_value(False) == "false"
    assert _metadata_value(7) == "7"


def test_postgres_backend_create_false_does_not_setup_missing_table(monkeypatch):
    calls = []

    def fake_detect(self, *, create=False):
        calls.append(("detect", create))
        self._vec_type = "vector"

    def fake_table_exists(self):
        calls.append(("exists", self.table_name))
        return False

    def fake_ensure_setup(self):
        calls.append(("ensure_setup", self.table_name))

    monkeypatch.setattr(PostgresCollection, "_detect_extensions", fake_detect)
    monkeypatch.setattr(PostgresCollection, "_table_exists", fake_table_exists)
    monkeypatch.setattr(PostgresCollection, "_ensure_setup", fake_ensure_setup)

    backend = PostgresBackend("postgresql://example")
    with pytest.raises(FileNotFoundError):
        backend.get_collection("/ignored", "mempalace_drawers", create=False)

    assert calls == [("exists", "mempalace_drawers")]


def test_postgres_backend_create_true_sets_up_collection(monkeypatch):
    calls = []

    def fake_detect(self, *, create=False):
        calls.append(("detect", create))
        self._vec_type = "vector"

    def fake_table_exists(self):
        calls.append(("exists", self.table_name))
        return True

    def fake_ensure_setup(self):
        calls.append(("ensure_setup", self.table_name))

    monkeypatch.setattr(PostgresCollection, "_detect_extensions", fake_detect)
    monkeypatch.setattr(PostgresCollection, "_table_exists", fake_table_exists)
    monkeypatch.setattr(PostgresCollection, "_ensure_setup", fake_ensure_setup)

    backend = PostgresBackend("postgresql://example")
    collection = backend.get_collection("/ignored", "mempalace_drawers", create=True)

    assert isinstance(collection, PostgresCollection)
    assert calls == [("detect", True), ("ensure_setup", "mempalace_drawers")]


def test_postgres_collection_validates_add_lengths(monkeypatch):
    collection = PostgresCollection("postgresql://example")
    monkeypatch.setattr(PostgresCollection, "_ensure_setup", lambda self: None)

    with pytest.raises(ValueError, match="documents and ids"):
        collection.add(documents=["doc"], ids=[])

    with pytest.raises(ValueError, match="metadatas and documents"):
        collection.add(documents=["doc"], ids=["id"], metadatas=[])

    with pytest.raises(ValueError, match="embeddings and documents"):
        collection.add(documents=["doc"], ids=["id"], embeddings=[])


def test_postgres_where_supports_or_and_field_operators():
    collection = PostgresCollection("postgresql://example")

    clause, params = collection._where_to_sql({"$or": [{"wing": "a"}, {"room": {"$ne": "b"}}]})
    assert clause is not None
    assert params == ["a", "b"]

    clause, params = collection._where_to_sql({"source_file": {"$in": ["a.py", "b.py"]}})
    assert clause is not None
    assert params == ["source_file", "a.py", "b.py"]


def test_postgres_where_rejects_unsupported_filters():
    collection = PostgresCollection("postgresql://example")

    with pytest.raises(ValueError, match="Unsupported PostgreSQL where operator"):
        collection._where_to_sql({"$gt": 3})

    with pytest.raises(ValueError, match="Unsupported PostgreSQL where field operator"):
        collection._where_to_sql({"source_mtime": {"$gt": 1}})


def test_postgres_get_and_delete_reject_empty_ids(monkeypatch):
    collection = PostgresCollection("postgresql://example")
    monkeypatch.setattr(PostgresCollection, "_ensure_setup", lambda self: None)

    with pytest.raises(ValueError, match="non-empty list in get"):
        collection.get(ids=[])

    with pytest.raises(ValueError, match="non-empty list in get"):
        collection.get(ids=[], where={"wing": "work"})

    with pytest.raises(ValueError, match="non-empty list in delete"):
        collection.delete(ids=[])

    with pytest.raises(ValueError, match="non-empty list in delete"):
        collection.delete(ids=[], where={"wing": "work"})


def test_postgres_upsert_embeds_before_opening_write_cursor(monkeypatch):
    calls = []
    collection = PostgresCollection("postgresql://example")

    def fake_embed(documents):
        calls.append(("embed", documents))
        raise RuntimeError("embed failed")

    monkeypatch.setattr(PostgresCollection, "_ensure_setup", lambda self: calls.append("setup"))
    monkeypatch.setattr(PostgresCollection, "_get_conn", lambda self: calls.append("get_conn"))
    monkeypatch.setattr(postgres_mod, "_embed", fake_embed)

    with pytest.raises(RuntimeError, match="embed failed"):
        collection.upsert(documents=["doc"], ids=["id"])

    assert calls == ["setup", ("embed", ["doc"])]


def test_postgres_upsert_uses_batch_on_conflict_without_delete(monkeypatch):
    events = []

    class FakeCursor:
        def execute(self, query, params=None):
            events.append(("execute", query, params))

    class FakeConnection:
        def cursor(self):
            events.append("cursor")
            return FakeCursor()

    conn = FakeConnection()
    collection = PostgresCollection("postgresql://example")
    collection._vec_type = "vector"
    collection._table_am = "heap"
    collection._index_am = "hnsw"
    collection._setup_done = True

    monkeypatch.setattr(PostgresCollection, "_get_conn", lambda self: conn)
    monkeypatch.setattr(
        PostgresCollection,
        "_maybe_create_vector_index",
        lambda self, **kwargs: events.append(("index", kwargs)),
    )
    monkeypatch.setattr(
        PostgresCollection,
        "delete",
        lambda self, **kwargs: (_ for _ in ()).throw(AssertionError("delete must not run")),
    )

    collection.upsert(
        documents=["old", "new"],
        ids=["same", "same"],
        metadatas=[{"wing": "w1"}, {"wing": "w2"}],
        embeddings=[[0.1] * 384, [0.2] * 384],
    )

    assert events[0] == "cursor"
    _execute, _query, params = events[1]
    assert _execute == "execute"
    assert params[0] == ["same"]
    assert params[1] == ["w2"]
    assert params[3] == ["new"]
    assert events[2] == ("index", {"inserted_rows": 1})


def test_postgres_vector_index_check_is_throttled(monkeypatch):
    events = []

    class FakeCursor:
        def execute(self, query, params=None):
            events.append(("execute", params))

        def fetchone(self):
            return (1,)

    class FakeConnection:
        def cursor(self):
            events.append("cursor")
            return FakeCursor()

    collection = PostgresCollection("postgresql://example")
    collection._rows_since_index_check = 0
    monkeypatch.setattr(PostgresCollection, "_get_conn", lambda self: FakeConnection())

    collection._maybe_create_vector_index(inserted_rows=1)
    assert events == []

    collection._maybe_create_vector_index(inserted_rows=VECTOR_INDEX_CHECK_INTERVAL_ROWS)
    assert events == ["cursor", ("execute", (collection.table_name + "_vec_idx",))]
    assert collection._vector_index_ready is True


def test_postgres_estimated_count_uses_catalog_stats_and_local_floor(monkeypatch):
    events = []

    class FakeCursor:
        def execute(self, query, params=None):
            events.append(("execute", params))

        def fetchone(self):
            return (42,)

    class FakeConnection:
        def cursor(self):
            events.append("cursor")
            return FakeCursor()

    collection = PostgresCollection("postgresql://example")
    collection._local_row_estimate = 100
    monkeypatch.setattr(PostgresCollection, "_get_conn", lambda self: FakeConnection())

    assert collection._estimated_count() == 100
    assert events == ["cursor", ("execute", (collection.table_name,))]


def test_palace_defaults_to_chroma_backend(monkeypatch):
    class FakeBackend:
        def __init__(self):
            self.calls = []

        def get_collection(self, palace_path, collection_name, create):
            self.calls.append((palace_path, collection_name, create))
            return "chroma-collection"

    fake = FakeBackend()
    monkeypatch.delenv("MEMPALACE_BACKEND", raising=False)
    monkeypatch.setattr(palace, "_DEFAULT_BACKEND", fake)
    palace._POSTGRES_BACKENDS.clear()

    result = palace.get_collection("/tmp/palace", "drawers", create=False)

    assert result == "chroma-collection"
    assert fake.calls == [("/tmp/palace", "drawers", False)]


def test_palace_postgres_backend_requires_dsn(monkeypatch):
    monkeypatch.setenv("MEMPALACE_BACKEND", "postgres")
    monkeypatch.delenv("MEMPALACE_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("MEMPALACE_PG_DSN", raising=False)
    palace._POSTGRES_BACKENDS.clear()

    with pytest.raises(RuntimeError, match="no DSN"):
        palace.get_collection("/ignored", "drawers", create=False)


def test_palace_postgres_backend_is_selected_by_env(monkeypatch):
    import mempalace.backends.postgres as postgres_mod

    created = []

    class FakePostgresBackend:
        def __init__(self, dsn):
            created.append(dsn)
            self.calls = []

        def get_collection(self, palace_path, collection_name, create):
            self.calls.append((palace_path, collection_name, create))
            return "postgres-collection"

    monkeypatch.setenv("MEMPALACE_BACKEND", "postgres")
    monkeypatch.setenv("MEMPALACE_POSTGRES_DSN", "postgresql://example")
    monkeypatch.setattr(postgres_mod, "PostgresBackend", FakePostgresBackend)
    palace._POSTGRES_BACKENDS.clear()

    result = palace.get_collection("/ignored", "drawers", create=True)

    assert result == "postgres-collection"
    assert created == ["postgresql://example"]
