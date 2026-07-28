"""Transcript integration: full-transparency ingest (measurements AND the
interpreted verdict, as collected), shared reader, and site + API surfacing of
mid-session model switches."""
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


def test_ingest_split_publishes_verdict_regardless_of_public():
    # Full transparency: the interpreted verdict is published as collected,
    # whether or not the target is flagged public. Nothing is withheld.
    ungated = ingest.split(RESULT, target="chat-z-ai-webapp", public=False)
    assert ungated["event_count"] == 1
    assert ungated["model_change_events"][0]["to"] == "GLM (Zhipu)"
    assert ungated["verdict"]["misrepresentation"] is True
    assert ungated["verdict"]["severity"] == "critical"
    flagged = ingest.split(RESULT, target="cleared", public=True)
    assert flagged["verdict"]["misrepresentation"] is True
    assert flagged["verdict"]["severity"] == "critical"


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


def test_first_party_misrepresentation_transcript_still_publishes(tmp_path):
    # The intentional feature: a first-party misrepresentation finding (no proxy
    # data) is NOT quarantined by the policy — it publishes under full transparency.
    data = tmp_path / "data"
    rec = ingest.split(RESULT, target="x", public=False)
    rec["verdict"] = {"misrepresentation": True, "severity": "critical", "finding": "..."}
    _write_transcript(str(data), "chat-z-ai-webapp", rec)
    assert "chat-z-ai-webapp" in records.load_transcripts(str(data))


def test_proxy_or_contradicted_transcript_is_quarantined(tmp_path):
    # CRITICAL (Claude): a transcript carrying CONTRADICTED / uncalibrated proxy
    # data must NOT bypass the policy by living in transcript.json.
    data = tmp_path / "data"
    bad = ingest.split(RESULT, target="x", public=False)
    bad["cross_check"] = {"state": "CONTRADICTED"}
    _write_transcript(str(data), "sneaky-target", bad)
    assert "sneaky-target" not in records.load_transcripts(str(data))


def test_site_surfaces_model_switch(tmp_path):
    data = tmp_path / "data"
    _write_transcript(str(data), "chat-z-ai-webapp", ingest.split(RESULT, target="x", public=False))
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-25T00:00:00")
    idx = (out / "index.html").read_text()
    assert "MODEL-SWITCH ALERTS" in idx and "Model-switch alerts" in idx
    assert "t/chat-z-ai-webapp.html" in idx
    # transcript-only target gets a detail page with the events AND the
    # interpreted verdict, published as collected (nothing withheld)
    detail = (out / "t" / "chat-z-ai-webapp.html").read_text()
    assert "Session model-change events" in detail
    assert "Google Gemini" in detail and "GLM (Zhipu)" in detail
    assert "MISREPRESENTATION" in detail
    assert "verdict withheld" not in detail


def test_model_change_section_shows_verdict_when_public():
    rec = ingest.split(RESULT, target="cleared", public=True)
    html = build._model_change_section(rec)
    assert "MISREPRESENTATION" in html and "critical" in html
