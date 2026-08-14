"""Announcements channel: release/method news in the RSS feed + API, kept DISTINCT
from the numbered MPA provenance advisories."""
import json
import os
import sys

from fastapi.testclient import TestClient

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from api import app as app_module  # noqa: E402
from api.store import Store  # noqa: E402
from lib import feed as _feed  # noqa: E402
from lib import records as _records  # noqa: E402


def _write_announcement(data_dir, aid, date, title, *, url=None):
    d = os.path.join(data_dir, "announcements")
    os.makedirs(d, exist_ok=True)
    rec = {"id": aid, "date": date, "title": title, "body": "body of " + title}
    if url:
        rec["url"] = url
    with open(os.path.join(d, f"{aid}.json"), "w") as f:
        json.dump(rec, f)


def test_load_announcements_sorted_newest_first(tmp_path):
    data = str(tmp_path)
    _write_announcement(data, "a1", "2026-08-01", "older")
    _write_announcement(data, "a2", "2026-08-14", "newer")
    got = _records.load_announcements(data)
    assert [a["title"] for a in got] == ["newer", "older"]


def test_load_announcements_empty_and_malformed(tmp_path):
    assert _records.load_announcements(str(tmp_path)) == []          # no dir -> empty
    d = os.path.join(str(tmp_path), "announcements")
    os.makedirs(d)
    open(os.path.join(d, "bad.json"), "w").write("{not json")
    open(os.path.join(d, "scalar.json"), "w").write("5")
    assert _records.load_announcements(str(tmp_path)) == []          # skipped, no crash


def test_feed_entries_include_announcement_distinct_from_advisories():
    ann = [{"title": "v0.28.0 on PyPI", "body": "shipped", "date": "2026-08-14",
            "url": "https://pypi.org/project/llm-provenance-probe/"}]
    adv = [{"advisory_id": "MPA-2026-001", "target": "deepseek-direct",
            "summary": "verdict change", "promoted_at": "2026-08-10"}]
    entries = _feed.entries_from(adv, [], ann)
    titles = [t for t, _, _ in entries]
    assert titles[0] == "Announcement: v0.28.0 on PyPI"             # news first
    assert any(t.startswith("MPA-2026-001") for t in titles)        # advisory still present + distinct
    # the announcement carries its URL in the description
    assert any("pypi.org/project/llm-provenance-probe" in d for _, d, _ in entries)


def test_store_and_api_announcements(tmp_path, monkeypatch):
    data = str(tmp_path / "data")
    _write_announcement(data, "rel-0.28.0", "2026-08-14", "0.28.0 released",
                        url="https://pypi.org/project/llm-provenance-probe/")
    monkeypatch.setattr(app_module, "store", Store(data))
    client = TestClient(app_module.app)
    r = client.get("/api/announcements")
    assert r.status_code == 200
    items = r.json()["items"]
    assert items and items[0]["title"] == "0.28.0 released"
    # and it shows up in the RSS feed, prefixed
    feed = client.get("/api/feed.xml").text
    assert "Announcement: 0.28.0 released" in feed


def test_committed_release_announcement_is_valid():
    """The real committed 0.28.0 announcement loads and is well-formed."""
    root = os.path.join(HERE, "..")
    anns = _records.load_announcements(os.path.join(root, "data"))
    rel = [a for a in anns if "0.28.0" in (a.get("title") or "")]
    assert rel, "expected the committed 0.28.0 release announcement"
    a = rel[0]
    assert a["date"] and a["url"].startswith("https://pypi.org/") and a.get("body")
