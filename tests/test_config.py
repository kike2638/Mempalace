import json
import os
import tempfile

import pytest
from mempalace.config import MempalaceConfig, sanitize_name, _default_config_dir


def _set_home(monkeypatch, home):
    """Point HOME and USERPROFILE at ``home`` so Path.home() is consistent
    on both POSIX and Windows.
    """
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))


def test_default_config():
    cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
    assert "palace" in cfg.palace_path
    assert cfg.collection_name == "mempalace_drawers"


def test_config_from_file():
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        json.dump({"palace_path": "/custom/palace"}, f)
    cfg = MempalaceConfig(config_dir=tmpdir)
    assert cfg.palace_path == "/custom/palace"


def test_env_override(monkeypatch):
    monkeypatch.setenv("MEMPALACE_PALACE_PATH", "/env/palace")
    cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
    assert cfg.palace_path == "/env/palace"


def test_init():
    tmpdir = tempfile.mkdtemp()
    cfg = MempalaceConfig(config_dir=tmpdir)
    cfg.init()
    assert os.path.exists(os.path.join(tmpdir, "config.json"))


# --- sanitize_name ---


def test_sanitize_name_ascii():
    assert sanitize_name("hello") == "hello"


def test_sanitize_name_latvian():
    assert sanitize_name("Jānis") == "Jānis"


def test_sanitize_name_cjk():
    assert sanitize_name("太郎") == "太郎"


def test_sanitize_name_cyrillic():
    assert sanitize_name("Алексей") == "Алексей"


def test_sanitize_name_rejects_leading_underscore():
    with pytest.raises(ValueError):
        sanitize_name("_foo")


def test_sanitize_name_rejects_path_traversal():
    with pytest.raises(ValueError):
        sanitize_name("../etc/passwd")


def test_sanitize_name_rejects_empty():
    with pytest.raises(ValueError):
        sanitize_name("")


# --- XDG Base Directory ---


def test_default_config_dir_uses_xdg_when_set(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == xdg / "mempalace"

    cfg = MempalaceConfig()
    assert cfg.palace_path == str(xdg / "mempalace" / "palace")


def test_default_config_dir_falls_back_to_dot_config(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _set_home(monkeypatch, fake_home)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == fake_home / ".config" / "mempalace"


def test_legacy_mempalace_dir_respected_for_backcompat(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    legacy = fake_home / ".mempalace"
    legacy.mkdir()
    # A marker file is required so that legacy detection distinguishes a real
    # install from an empty directory left behind by some other tool.
    (legacy / "config.json").write_text("{}")
    xdg = tmp_path / "xdg"
    xdg.mkdir()

    _set_home(monkeypatch, fake_home)
    # XDG is set but legacy takes precedence so existing installs keep working.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == legacy

    cfg = MempalaceConfig()
    assert cfg.palace_path == str(legacy / "palace")


def test_empty_legacy_dir_does_not_hijack_xdg(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    # Empty ~/.mempalace must not be treated as a real install.
    (fake_home / ".mempalace").mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()

    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == xdg / "mempalace"


def test_bare_palace_dir_does_not_trigger_legacy(monkeypatch, tmp_path):
    # A bare ~/.mempalace/palace directory (without an actual ChromaDB
    # store inside) should not be treated as a legacy install -- some
    # other tool may have created the directory.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    legacy = fake_home / ".mempalace"
    legacy.mkdir()
    (legacy / "palace").mkdir()
    xdg = tmp_path / "xdg"
    xdg.mkdir()

    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == xdg / "mempalace"


def test_palace_with_chromadb_triggers_legacy(monkeypatch, tmp_path):
    # A ~/.mempalace/palace directory that actually contains the ChromaDB
    # store counts as a real legacy install even without config.json.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    legacy = fake_home / ".mempalace"
    legacy.mkdir()
    palace = legacy / "palace"
    palace.mkdir()
    (palace / "chroma.sqlite3").write_bytes(b"")
    xdg = tmp_path / "xdg"
    xdg.mkdir()

    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == legacy


def test_mempalace_config_dir_env_overrides_everything(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    # Create a legacy dir with a marker that would otherwise win.
    legacy = fake_home / ".mempalace"
    legacy.mkdir()
    (legacy / "config.json").write_text("{}")
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    override = tmp_path / "override"
    override.mkdir()

    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("MEMPALACE_CONFIG_DIR", str(override))

    assert _default_config_dir() == override

    cfg = MempalaceConfig()
    assert cfg.palace_path == str(override / "palace")


def test_empty_xdg_config_home_falls_back_to_dot_config(monkeypatch, tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _set_home(monkeypatch, fake_home)
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    assert _default_config_dir() == fake_home / ".config" / "mempalace"

    monkeypatch.setenv("XDG_CONFIG_HOME", "   ")
    assert _default_config_dir() == fake_home / ".config" / "mempalace"


def test_relative_xdg_config_home_is_ignored(monkeypatch, tmp_path):
    # Per XDG spec, relative paths in XDG_CONFIG_HOME are invalid.
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _set_home(monkeypatch, fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/path")
    monkeypatch.delenv("MEMPALACE_CONFIG_DIR", raising=False)

    assert _default_config_dir() == fake_home / ".config" / "mempalace"


def test_init_writes_xdg_aware_palace_path(tmp_path):
    cfg = MempalaceConfig(config_dir=str(tmp_path))
    cfg.init()
    with open(tmp_path / "config.json") as f:
        written = json.load(f)
    assert written["palace_path"] == str(tmp_path / "palace")
