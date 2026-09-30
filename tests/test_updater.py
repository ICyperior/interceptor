"""Tests for the GitHub update check."""

import io
import json
import time

import pytest

from utils import updater


class _Response(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub API: set `github['release']` / `github['tags']`; None means the call fails."""
    state = {"release": None, "tags": None}
    settings = {}

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        payload = state["release"] if url.endswith("/releases/latest") else state["tags"]
        if payload is None:
            raise updater.URLError("offline")
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(updater, "urlopen", fake_urlopen)
    monkeypatch.setattr(updater, "get_setting", settings.get)
    monkeypatch.setattr(updater, "set_setting", settings.__setitem__)
    monkeypatch.setattr(updater.config, "VERSION", "2.33.66", raising=False)
    monkeypatch.setattr(updater.config, "UPDATE_CHECK_ENABLED", True, raising=False)
    state["settings"] = settings
    return state


def _release(tag):
    return {"tag_name": tag, "html_url": f"https://example/{tag}", "body": "notes", "published_at": "", "name": tag}


def test_newer_tag_beats_stale_release(github):
    # The last GitHub release is older than this install, but newer versions were tagged
    github["release"] = _release("v2.33.47")
    github["tags"] = [{"name": "v2.33.73"}, {"name": "v2.33.75"}, {"name": "v2.33.9"}, {"name": "not-a-version"}]
    result = updater.check_for_updates(force=True)
    assert result["latest_version"] == "2.33.75"
    assert result["update_available"] is True
    assert result["release_url"].endswith("/releases/tag/v2.33.75")


def test_release_used_when_it_is_newest(github):
    github["release"] = _release("v2.34.0")
    github["tags"] = [{"name": "v2.33.75"}]
    result = updater.check_for_updates(force=True)
    assert result["latest_version"] == "2.34.0"
    assert result["release_notes"] == "notes"


def test_tags_alone_work_when_releases_fail(github):
    github["tags"] = [{"name": "v2.33.70"}]
    assert updater.check_for_updates(force=True)["latest_version"] == "2.33.70"


def test_stale_cached_latest_is_not_shown(github):
    # Cache written by an older build that only looked at releases/latest
    github["settings"].update(
        {updater.CACHE_KEY_LATEST_VERSION: "2.33.47", updater.CACHE_KEY_LAST_CHECK: str(time.time())}
    )
    assert updater.get_update_status()["checked"] is False
    # A normal (non-forced) check ignores the stale cache and asks GitHub again
    github["tags"] = [{"name": "v2.33.75"}]
    result = updater.check_for_updates()
    assert result["latest_version"] == "2.33.75"
    assert result["cached"] is False
