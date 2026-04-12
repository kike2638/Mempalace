import os
import json
import tempfile
from mempalace.config import MempalaceConfig


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


def test_env_override():
    os.environ["MEMPALACE_PALACE_PATH"] = "/env/palace"
    cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
    assert cfg.palace_path == "/env/palace"
    del os.environ["MEMPALACE_PALACE_PATH"]


def test_init():
    tmpdir = tempfile.mkdtemp()
    cfg = MempalaceConfig(config_dir=tmpdir)
    cfg.init()
    assert os.path.exists(os.path.join(tmpdir, "config.json"))


def test_hooks_auto_save_default():
    cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
    assert cfg.hooks_auto_save is True


def test_hooks_auto_save_from_config():
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        json.dump({"hooks": {"auto_save": False}}, f)
    cfg = MempalaceConfig(config_dir=tmpdir)
    assert cfg.hooks_auto_save is False


def test_hooks_auto_save_env_override_false():
    os.environ["MEMPALACE_HOOKS_AUTO_SAVE"] = "false"
    try:
        cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
        assert cfg.hooks_auto_save is False
    finally:
        del os.environ["MEMPALACE_HOOKS_AUTO_SAVE"]


def test_hooks_auto_save_env_override_zero():
    os.environ["MEMPALACE_HOOKS_AUTO_SAVE"] = "0"
    try:
        cfg = MempalaceConfig(config_dir=tempfile.mkdtemp())
        assert cfg.hooks_auto_save is False
    finally:
        del os.environ["MEMPALACE_HOOKS_AUTO_SAVE"]


def test_hooks_auto_save_env_override_true():
    """Env var set to 'true' overrides config file even if config says false."""
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        json.dump({"hooks": {"auto_save": False}}, f)
    os.environ["MEMPALACE_HOOKS_AUTO_SAVE"] = "true"
    try:
        cfg = MempalaceConfig(config_dir=tmpdir)
        assert cfg.hooks_auto_save is True
    finally:
        del os.environ["MEMPALACE_HOOKS_AUTO_SAVE"]
