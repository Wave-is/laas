"""Unit tests for app updates, engine updates, and backups."""
from src import app_updates, engine_updates, backup


def test_app_updates_semver_compare():
    assert app_updates.compare("0.1.0", "0.1.0") == 0
    assert app_updates.compare("0.1.1", "0.1.0") == 1
    assert app_updates.compare("0.1.0", "0.2.0") == -1
    assert app_updates.compare("0.2.0-beta.1", "0.2.0-beta.2") == -1
    assert app_updates.compare("0.2.0-beta.2", "0.2.0") == -1
    assert app_updates.compare("0.2.0", "0.2.0-beta.1") == 1


def test_app_updates_check_mock():
    releases = [
        {"tag_name": "v0.3.0", "name": "0.3.0", "html_url": "https://example.com/0.3.0", "draft": False, "published_at": "2026-09-01T00:00:00Z"},
        {"tag_name": "v0.2.0", "name": "0.2.0", "html_url": "https://example.com/0.2.0", "draft": False, "published_at": "2026-08-01T00:00:00Z"},
    ]
    res = app_updates.check(current="0.2.0", fetch=lambda url: releases)
    assert res["Success"] is True
    assert res["Release"]["version"] == "0.3.0"

    res_latest = app_updates.check(current="0.3.0", fetch=lambda url: releases)
    assert res_latest["Success"] is True
    assert res_latest["Release"] is None


def test_backup_redact_and_manifest(tmp_path):
    data = {
        "api_key": "secret123",
        "nested": {"token": "my-secret-token", "regular": "hello"},
    }
    redacted, found = backup.redact(data)
    assert redacted["api_key"] == backup.REDACTED
    assert redacted["nested"]["token"] == backup.REDACTED
    assert redacted["nested"]["regular"] == "hello"
    assert "api_key" in found


def test_engine_updates_version_parsers():
    llama_out = "version: 0.4.0-dev (build 3500, commit a1b2c3d)"
    parsed = engine_updates.parse_llama_version(llama_out)
    assert parsed["build"] == 3500
    assert parsed["commit"] == "a1b2c3d"

    swap_out = "version: v255 (7761aa1), built at 2026-01-01"
    parsed_swap = engine_updates.parse_swap_version(swap_out)
    assert parsed_swap["build"] == 255
    assert parsed_swap["commit"] == "7761aa1"
