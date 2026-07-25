"""API: Store query logic (temp data) + FastAPI endpoints (real committed data).

Verifies filtering/pagination/search, two-tier withholding (no gated leak),
target history + 404s, status counts, and a valid RSS feed + OpenAPI.
"""
import json
import os
import sys
from datetime import date, timedelta
from xml.dom import minidom

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from api import app as app_module  # noqa: E402
from api.store import Store  # noqa: E402


def _seed(data_dir, target, kind, rec, *, public=False):
    d = os.path.join(data_dir, target, date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    rec.setdefault("target", {"name": target, "kind": kind, "model": "m1"})
    with open(os.path.join(d, "verdict.json"), "w") as f:
        json.dump(rec, f)


def _store_with(tmp_path):
    data = tmp_path / "data"
    _seed(str(data), "control-openai-negative", "control-negative",
          {"fingerprint_id": "fpA", "drift_seen": False,
           "tokenizer": {"usable": True}, "headers": {"status": 200},
           "control_check": {"kind": "control-negative", "pass": True}})
    _seed(str(data), "some-aggregator", "aggregator",
          {"fingerprint_id": "fpB", "drift_seen": True,
           "tokenizer": {"usable": False}, "headers": {"status": 200},
           "verdict": {"provenance": "CONFIRMED", "jurisdiction": "CN", "confidence": "high"}})
    md = os.path.join(str(data), "manifests")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, f"{date.today().isoformat()}.json"), "w") as f:
        json.dump({"date": date.today().isoformat(), "entries": {"x": "y"},
                   "manifest_root": "r" * 40}, f)
    return Store(str(data))


# --- Store logic ------------------------------------------------------------

def test_verdicts_list_and_pagination(tmp_path):
    s = _store_with(tmp_path)
    out = s.verdicts(limit=1, offset=0)
    assert out["total"] == 2 and len(out["items"]) == 1 and out["limit"] == 1
    assert s.verdicts(offset=1)["items"][0]["target"] != out["items"][0]["target"]


def test_filter_by_kind_and_drift(tmp_path):
    s = _store_with(tmp_path)
    assert {i["target"] for i in s.verdicts(kind="aggregator")["items"]} == {"some-aggregator"}
    assert {i["target"] for i in s.verdicts(drift=True)["items"]} == {"some-aggregator"}
    assert {i["target"] for i in s.verdicts(drift=False)["items"]} == {"control-openai-negative"}


def test_two_tier_withholding(tmp_path):
    s = _store_with(tmp_path)
    by = {i["target"]: i for i in s.verdicts()["items"]}
    # cleared target (has verdict block) exposes interpreted fields
    assert by["some-aggregator"]["withheld"] is False
    assert by["some-aggregator"]["provenance"] == "CONFIRMED"
    # un-cleared target withholds — no gated leak
    assert by["control-openai-negative"]["withheld"] is True
    assert by["control-openai-negative"]["provenance"] is None


def test_coverage_degraded_flag(tmp_path):
    s = _store_with(tmp_path)
    by = {i["target"]: i for i in s.verdicts()["items"]}
    assert by["some-aggregator"]["coverage"]["degraded"] is True     # usage suppressed
    assert by["control-openai-negative"]["coverage"]["degraded"] is False


def test_target_detail_and_missing(tmp_path):
    s = _store_with(tmp_path)
    t = s.target("some-aggregator")
    assert t and t["history"] and t["evidence"]["manifest_root"] == "r" * 40
    assert s.target("nope") is None


def test_status_counts(tmp_path):
    s = _store_with(tmp_path)
    st = s.status()
    assert st["monitored_targets"] == 2 and st["active_aggregators"] == 1
    assert st["drift_events"] == 1 and st["transparency_log_tree_head"] == "r" * 40


def test_search(tmp_path):
    s = _store_with(tmp_path)
    assert {h["target"] for h in s.search("aggregator")} == {"some-aggregator"}
    assert s.search("") == []


# --- FastAPI endpoints (over the real committed data/) ----------------------

client = TestClient(app_module.app)


def test_status_endpoint():
    r = client.get("/api/status")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_verdicts_endpoint_shape():
    r = client.get("/api/verdicts?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert {"total", "limit", "offset", "items"} <= set(body)


def test_target_404():
    assert client.get("/api/targets/does-not-exist").status_code == 404


def test_manifests_and_feed_and_docs():
    assert client.get("/api/manifests").status_code == 200
    feed = client.get("/api/feed.xml")
    assert feed.status_code == 200 and "application/rss+xml" in feed.headers["content-type"]
    minidom.parseString(feed.content)                       # valid XML
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/", follow_redirects=False).status_code in (307, 302)


# --- P4: rate limiting + SSE stream -----------------------------------------

def test_rate_limit_returns_429(monkeypatch):
    app_module._hits.clear()
    monkeypatch.setattr(app_module, "RATE_LIMIT", 2)
    try:
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/status").status_code == 200
        assert client.get("/api/status").status_code == 429      # 3rd over the limit
    finally:
        app_module._hits.clear()


def test_docs_exempt_from_rate_limit(monkeypatch):
    app_module._hits.clear()
    monkeypatch.setattr(app_module, "RATE_LIMIT", 1)
    try:
        for _ in range(3):
            assert client.get("/api/openapi.json").status_code == 200   # never 429
    finally:
        app_module._hits.clear()


def test_sse_stream_emits_status_event():
    app_module._hits.clear()
    # ?once=1 keeps the stream finite so the test can't hang on the interval loop
    r = client.get("/api/stream?once=1")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "event: status" in r.text and "data:" in r.text
    app_module._hits.clear()


# --- transcript integration: model-switch endpoints -------------------------

def test_model_changes_endpoint():
    r = client.get("/api/model-changes")
    assert r.status_code == 200 and "items" in r.json()


def test_status_has_model_switch_alerts():
    assert "model_switch_alerts" in client.get("/api/status").json()


def test_store_target_includes_transcript(tmp_path):
    import json as _json
    from datetime import date as _date
    d = tmp_path / "data" / "chat-z-ai-webapp" / _date.today().isoformat()
    d.mkdir(parents=True)
    (d / "transcript.json").write_text(_json.dumps(
        {"model_change_events": [{"turn": 7, "from": "Google Gemini", "to": "GLM (Zhipu)",
                                  "kind": "concession"}],
         "distinct_identities": ["Google Gemini"], "verdict": {"withheld": True}}))
    from api.store import Store
    s = Store(str(tmp_path / "data"))
    t = s.target("chat-z-ai-webapp")            # transcript-only target still resolves
    assert t and t["model_change_events"][0]["to"] == "GLM (Zhipu)"
    assert s.status()["model_switch_alerts"] == 1
    assert s.model_switches()[0]["target"] == "chat-z-ai-webapp"
