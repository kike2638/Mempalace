import chromadb
import pytest

from mempalace import palace
from mempalace.backends.chroma import ChromaBackend, ChromaCollection
from mempalace.backends.postgres import (
    PostgresBackend,
    PostgresCollection,
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

    with pytest.raises(ValueError, match="embeddings and documents"):
        collection.add(documents=["doc"], ids=["id"], embeddings=[])


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
