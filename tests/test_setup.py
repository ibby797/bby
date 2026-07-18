"""Tests for the first-time setup wizard."""

import yaml

from quantbot import cli


def _clean_env(monkeypatch):
    for var in ("T212_API_KEY", "T212_ENV", "T212_DRY_RUN",
                "QUANTBOT_ENV", "QUANTBOT_DRY_RUN", "QUANTBOT_BROKER"):
        monkeypatch.delenv(var, raising=False)


def test_setup_writes_config_with_key(tmp_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    rc = cli.main(["--config", str(config_path), "setup", "--key", "test-key-123"])
    assert rc == 0
    data = yaml.safe_load(config_path.read_text())
    assert data["api"]["key"] == "test-key-123"
    assert data["environment"] == "demo"   # safest defaults
    assert data["dry_run"] is True
    out = capsys.readouterr().out
    assert "Next steps" in out


def test_setup_preserves_existing_config(tmp_path, monkeypatch):
    _clean_env(monkeypatch)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("risk:\n  max_open_positions: 3\nwebhook_url: http://x\n")
    cli.main(["--config", str(config_path), "setup", "--key", "k2"])
    data = yaml.safe_load(config_path.read_text())
    assert data["api"]["key"] == "k2"
    assert data["risk"]["max_open_positions"] == 3   # untouched
    assert data["webhook_url"] == "http://x"


def test_setup_rejects_empty_key(tmp_path, monkeypatch, capsys):
    _clean_env(monkeypatch)
    monkeypatch.setattr("builtins.input", lambda *_: "")
    config_path = tmp_path / "config.yaml"
    rc = cli.main(["--config", str(config_path), "setup"])
    assert rc == 1
    assert not config_path.exists()
