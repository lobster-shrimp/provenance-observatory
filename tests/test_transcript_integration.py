"""Transcript integration: two-tier ingest, shared reader, site + API surfacing
of mid-session model switches."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
import ingest_transcripts as ingest  # noqa: E402
import build  # noqa: E402
from lib import records  # noqa: E402

RESULT = {
    "turns_analyzed": 5,
    "distinct_identities": ["Google Gemini"],
    "model_change_events": [{"turn": 7, "from": "Google Gemini", "to": "GLM (Zhipu)",
                             "kind": "concession"}],
    "correlation": {"misrepresentation": True, "severity": "critical",
                    "finding": "MATERIAL MISREPRESENTATION."},
}


def test_ingest_split_two_tier():
    withheld = ingest.split(RESULT, target="chat-z-ai-webapp", public=False)
    assert withheld["event_count"] == 1
    assert withheld["model_change_events"][0]["to"] == "GLM (Zhipu)"   # neutral fact published
    assert withheld["verdict"] == {"withheld": True}                   # interpreted withheld
    cleared = ingest.split(RESULT, target="cleared", public=True)
    assert cleared["verdict"]["misrepresentation"] is True
    assert cleared["verdict"]["severity"] == "critical"


def _write_transcript(data_dir, target, rec):
    d = os.path.join(data_dir, target, date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "transcript.json"), "w") as f:
        json.dump(rec, f)


def test_load_transcripts_reader(tmp_path):
    data = tmp_path / "data"
    _write_transcript(str(data), "chat-z-ai-webapp", ingest.split(RESULT, target="x", public=False))
    tx = records.load_transcripts(str(data))
    assert "chat-z-ai-webapp" in tx
    assert tx["chat-z-ai-webapp"]["event_count"] == 1


def test_site_surfaces_model_switch(tmp_path):
    data = tmp_path / "data"
    _write_transcript(str(data), "chat-z-ai-webapp", ingest.split(RESULT, target="x", public=False))
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-25T00:00:00")
    idx = (out / "index.html").read_text()
    assert "MODEL-SWITCH ALERTS" in idx and "Model-switch alerts" in idx
    assert "t/chat-z-ai-webapp.html" in idx
    # transcript-only target still gets a detail page with the events + withheld verdict
    detail = (out / "t" / "chat-z-ai-webapp.html").read_text()
    assert "Session model-change events" in detail
    assert "Google Gemini" in detail and "GLM (Zhipu)" in detail
    assert "verdict withheld" in detail


def test_model_change_section_shows_verdict_when_public():
    rec = ingest.split(RESULT, target="cleared", public=True)
    html = build._model_change_section(rec)
    assert "MISREPRESENTATION" in html and "critical" in html
