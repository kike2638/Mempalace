"""Extra tests for mempalace.config to cover remaining gaps."""

import json
import os

import pytest

from mempalace.config import MempalaceConfig


def test_config_bad_json(tmp_path):
    """Bad JSON in config file falls back to empty."""
    (tmp_path / "config.json").write_text("not json", encoding="utf-8")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.palace_path  # still returns default


def test_people_map_from_file(tmp_path):
    (tmp_path / "people_map.json").write_text(json.dumps({"bob": "Robert"}), encoding="utf-8")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {"bob": "Robert"}


def test_people_map_bad_json(tmp_path):
    (tmp_path / "people_map.json").write_text("bad", encoding="utf-8")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {}


def test_people_map_missing(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.people_map == {}


def test_topic_wings_default(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert isinstance(cfg.topic_wings, list)
    assert "emotions" in cfg.topic_wings


def test_hall_keywords_default(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert isinstance(cfg.hall_keywords, dict)
    assert "technical" in cfg.hall_keywords


def test_init_idempotent(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    cfg.init()
    cfg.init()  # second call should not overwrite
    with open(tmp_path / "config.json") as f:
        data = json.load(f)
    assert "palace_path" in data


def test_save_people_map(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    result = cfg.save_people_map({"alice": "Alice Smith"})
    assert result.exists()
    with open(result) as f:
        data = json.load(f)
    assert data["alice"] == "Alice Smith"


def test_env_mempal_palace_path(tmp_path):
    """MEMPAL_PALACE_PATH (legacy) should also work."""
    os.environ.pop("MEMPALACE_PALACE_PATH", None)
    os.environ["MEMPAL_PALACE_PATH"] = "/legacy/path"
    try:
        cfg = MempalaceConfig(config_dir=str(tmp_path))
        assert cfg.palace_path == "/legacy/path"
    finally:
        del os.environ["MEMPAL_PALACE_PATH"]


def test_collection_name_from_config(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"collection_name": "custom_col"}), encoding="utf-8"
    )
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.collection_name == "custom_col"


def test_backend_defaults_to_chroma(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.backend == "chroma"


def test_backend_accepts_postgres_alias_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_BACKEND", "postgresql")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.backend == "postgres"


def test_backend_rejects_unknown_value(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMPALACE_BACKEND", "sqlite")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    with pytest.raises(ValueError, match="Unsupported MemPalace backend"):
        cfg.backend


def test_postgres_dsn_prefers_env(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text(
        json.dumps({"postgres_dsn": "config-dsn"}), encoding="utf-8"
    )
    monkeypatch.setenv("MEMPALACE_POSTGRES_DSN", "env-dsn")
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    assert cfg.postgres_dsn == "env-dsn"
