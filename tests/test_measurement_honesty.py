"""Measurement honesty (readiness review 2026-09-16).

Three ways the log could say more than it measured, all seen in production data:

  1. A target whose every probe was rejected 401 (operator key absent/stale) was
     published as a verdict ("degraded vendor signal") for 44 straight days.
     -> the runner must record a no-verdict naming the credential instead.
  2. A negative control whose tokenizer layer measured nothing (429 for 8 days)
     was recorded control_check.pass=True and counted toward the site's
     "0 false positives" badge.
     -> pass=None ("not measured"), and the site tally must not count it clean.
  3. The same captured transcript was re-ingested every night as new, today-dated
     evidence (52 identical signed records for one 2026-07-25 capture).
     -> ingest must skip a capture it has already recorded.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "runner"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
import run  # noqa: E402
import ingest_transcripts  # noqa: E402
import build  # noqa: E402


def _bundle(usable, errors=None, matches=None):
    return {"tokenizer": {"usable": usable, "errors": errors or {}},
            "tokenizer_match": matches or []}


# --- 1. auth failure is a no-verdict, not a verdict ------------------------

def test_all_401_is_an_auth_failure():
    b = _bundle(False, {p: "no usage field (HTTP 401)" for p in ("a", "b", "c")})
    assert run.auth_failure_status(b) == "401"


def test_all_403_is_an_auth_failure():
    b = _bundle(False, {"a": "no usage field (HTTP 403)"})
    assert run.auth_failure_status(b) == "403"


def test_rate_limit_is_not_an_auth_failure():
    b = _bundle(False, {"a": "no usage field (HTTP 429)", "b": "no usage field (HTTP 429)"})
    assert run.auth_failure_status(b) is None


def test_mixed_statuses_are_a_real_degraded_measurement():
    b = _bundle(False, {"a": "no usage field (HTTP 401)", "b": "no usage field (HTTP 500)"})
    assert run.auth_failure_status(b) is None


def test_usable_tokenizer_never_flagged_even_with_stray_401():
    b = _bundle(True, {"a": "no usage field (HTTP 401)"})
    assert run.auth_failure_status(b) is None


def test_process_target_writes_no_verdict_on_auth_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(run, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(run, "STAGING_DIR", str(tmp_path / "staging"))
    monkeypatch.setattr(run, "run_assess", lambda t, d: _bundle(
        False, {p: "no usage field (HTTP 401)" for p in ("cjk_dense", "ws_runs")}))
    target = {"name": "anthropic-frontier", "kind": "control-negative", "authorized": True,
              "auth_env": "ANTHROPIC_API_KEY", "base_url": "https://x", "model": "m"}
    run.process_target(target, {}, {})
    d = tmp_path / "data" / "anthropic-frontier" / run.date.today().isoformat()
    assert not (d / "verdict.json").exists()
    nv = json.loads((d / "no-verdict.json").read_text())
    assert nv["outcome"] == "no-verdict"
    assert "HTTP 401" in nv["reason"] and "ANTHROPIC_API_KEY" in nv["reason"]


# --- 2. an unmeasured control is neither pass nor fail ---------------------

NEG = {"name": "control-openai-negative", "kind": "control-negative", "expect_not_origin": "CN"}
POS = {"name": "control-deepseek-positive", "kind": "control-positive", "expect_family": "DeepSeek-V3"}


def test_negative_control_unmeasured_is_none_not_pass():
    cc = run.check_control(NEG, _bundle(False, {"a": "no usage field (HTTP 429)"}))
    assert cc["pass"] is None and cc["measured"] is False


def test_positive_control_unmeasured_is_none_not_fail():
    cc = run.check_control(POS, _bundle(False, {"a": "no usage field (HTTP 429)"}))
    assert cc["pass"] is None and cc["measured"] is False


def test_measured_controls_still_pass_and_fail():
    ok = run.check_control(NEG, _bundle(True, matches=[{"model": "OpenAI-o200k", "origin": "US", "score": 1.0}]))
    assert ok["pass"] is True and ok["measured"] is True
    bad = run.check_control(NEG, _bundle(True, matches=[{"model": "Qwen3", "origin": "CN", "score": 0.95}]))
    assert bad["pass"] is False
    caught = run.check_control(POS, _bundle(True, matches=[{"model": "DeepSeek-V3", "origin": "CN", "score": 0.97}]))
    assert caught["pass"] is True


def test_site_tally_does_not_count_unmeasured_control_as_clean():
    # pass=None is what the runner now writes for an unmeasured control
    ca = build._control_accuracy(
        {"n": [("d", {"control_check": {"kind": "control-negative", "pass": None}})]})
    assert ca["neg_total"] == 0 and ca["fp"] == 0 and ca["unmeasured"] == 1


def test_site_tally_counts_measured_controls():
    ca = build._control_accuracy({
        "n": [("d", {"control_check": {"kind": "control-negative", "pass": True}})],
        "p": [("d", {"control_check": {"kind": "control-positive", "pass": True}})],
    })
    assert ca == {"neg_total": 1, "neg_pass": 1, "fp": 0, "pos_total": 1, "pos_pass": 1, "unmeasured": 0}


def test_site_control_label_for_unmeasured():
    assert build._control_state({"pass": None}) == ("unmeasured", "NOT MEASURED")
    assert build._control_state({"pass": True}) == ("pass", "PASS")
    assert build._control_state({"pass": False}) == ("fail", "FAIL")


def test_assurance_panel_names_unmeasured_controls():
    html = build._assurance_panel(
        {"n": [("d", {"control_check": {"kind": "control-negative", "pass": None}})]}, {})
    assert "not measured" in html and "0 false positives" not in html


# --- 3. a transcript is ingested once ---------------------------------------

def test_transcript_ingested_once_then_skipped(tmp_path, monkeypatch):
    tdir = tmp_path / "transcripts" / "chat-z-ai-webapp"
    tdir.mkdir(parents=True)
    (tdir / "2026-07-25-session.json").write_text(json.dumps([{"role": "user", "content": "hi"}]))
    (tdir / "origin.txt").write_text("CN")
    calls = []

    def fake_analyze(path, origin):
        calls.append(path)
        return {"turns_analyzed": 1, "distinct_identities": ["Google Gemini"],
                "model_change_events": [], "correlation": {}}
    monkeypatch.setattr(ingest_transcripts, "analyze_file", fake_analyze)

    data = tmp_path / "data"
    w1 = ingest_transcripts.ingest(str(tmp_path / "transcripts"), str(data), today="2026-09-15")
    w2 = ingest_transcripts.ingest(str(tmp_path / "transcripts"), str(data), today="2026-09-16")
    assert len(w1) == 1 and w2 == []          # second night: skipped
    assert len(calls) == 1
    rec = json.loads((data / "chat-z-ai-webapp" / "2026-09-15" / "transcript.json").read_text())
    assert rec["source"]["file"] == "2026-07-25-session.json"
    assert rec["source"]["ingested_on"] == "2026-09-15"
    assert len(rec["source"]["sha256"]) == 64


def test_new_capture_is_ingested(tmp_path, monkeypatch):
    tdir = tmp_path / "transcripts" / "t"
    tdir.mkdir(parents=True)
    (tdir / "2026-07-25-session.json").write_text(json.dumps([{"role": "user", "content": "hi"}]))
    monkeypatch.setattr(ingest_transcripts, "analyze_file", lambda p, o: {
        "turns_analyzed": 1, "distinct_identities": [], "model_change_events": [], "correlation": {}})
    data = tmp_path / "data"
    ingest_transcripts.ingest(str(tmp_path / "transcripts"), str(data), today="2026-09-15")
    (tdir / "2026-09-16-session.json").write_text(json.dumps([{"role": "user", "content": "new"}]))
    w = ingest_transcripts.ingest(str(tmp_path / "transcripts"), str(data), today="2026-09-16")
    assert len(w) == 1


def test_changed_origin_is_a_new_claim(tmp_path):
    tdir = tmp_path / "transcripts" / "t"
    tdir.mkdir(parents=True)
    conv = tdir / "s.json"
    conv.write_text("[]")
    a = ingest_transcripts._source_meta(str(conv), None)
    b = ingest_transcripts._source_meta(str(conv), "CN")
    assert a["sha256"] != b["sha256"]
