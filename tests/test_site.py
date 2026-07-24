"""Pages renderer: renders from data/, withholds interpreted verdicts until a
promoted advisory exists, shows control checks, and never leaks raw verdict
labels for un-promoted targets."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "site"))
import build  # noqa: E402


def _write_verdict(data_dir, target, kind, rec):
    d = os.path.join(data_dir, target, date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    rec.setdefault("target", {"name": target, "kind": kind, "model": "m1"})
    with open(os.path.join(d, "verdict.json"), "w") as f:
        json.dump(rec, f)


def test_renders_neutral_and_withholds_interpreted(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "openrouter-neutral-endpoint", "aggregator",
                   {"schema_version": "0.1.0", "fingerprint_id": "abc123def456",
                    "drift_seen": False})
    outp = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(outp).read()
    assert "PROVENANCE OBSERVATORY" in doc
    assert "openrouter-neutral-endpoint" in doc
    # the raw fingerprint now lives on the per-target detail page (index shows
    # an evidence-bundle link in its place — the approved design)
    detail = os.path.join(os.path.dirname(outp), "t", "openrouter-neutral-endpoint.html")
    assert "abc123def456"[:12] in open(detail).read()
    # interpreted verdict withheld (no promoted advisory)
    assert "withheld" in doc
    assert "CONFIRMED" not in doc


def test_promoted_advisory_shows_verdict(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "openrouter-neutral-endpoint", "aggregator",
                   {"fingerprint_id": "fp1", "drift_seen": True})
    advdir = data / "advisories"
    advdir.mkdir(parents=True)
    with open(advdir / "a1.json", "w") as f:
        json.dump({"advisory_id": "MPA-2026-001",
                   "target": "openrouter-neutral-endpoint",
                   "promoted_at": "2026-08-25T00:00:00",
                   "verdict": {"provenance_risk": {"verdict": "CONFIRMED", "confidence": "high"},
                               "jurisdictional_risk": {"verdict": "LIKELY"}}}, f)
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-08-25T12:00:00")
    doc = open(out).read()
    assert "MPA-2026-001" in doc
    assert "CONFIRMED" in doc and "LIKELY" in doc


def test_cleared_target_shows_verdict_columns(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-qwen-known-answer", "control-positive",
                   {"fingerprint_id": "fpc", "drift_seen": False,
                    "verdict": {"provenance": "LIKELY", "jurisdiction": "UNLIKELY",
                                "confidence": "high"}})
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    # verdict badges rendered for the cleared target (not withheld)
    assert '<span class="badge' in doc
    assert "LIKELY" in doc and "UNLIKELY" in doc and "high" in doc


def test_control_check_rendered(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-qwen-known-answer", "control-positive",
                   {"fingerprint_id": "fpc", "drift_seen": False,
                    "control_check": {"kind": "control-positive", "pass": True}})
    out = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    assert "control: PASS" in doc


def test_empty_data_renders_placeholder(tmp_path):
    out = build.build(str(tmp_path / "data"), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    doc = open(out).read()
    assert "No probe data yet" in doc


# --- Assurance panel: live control accuracy + engine eval badge -------------

def test_control_accuracy_counts_fp_and_tp():
    records = {
        "neg": [("2026-07-24", {"control_check": {"kind": "control-negative", "pass": True}})],
        "pos": [("2026-07-24", {"control_check": {"kind": "control-positive", "pass": True}})],
        "neg2": [("2026-07-24", {"control_check": {"kind": "control-negative", "pass": False}})],
    }
    ca = build._control_accuracy(records)
    assert ca["neg_total"] == 2 and ca["neg_pass"] == 1 and ca["fp"] == 1
    assert ca["pos_total"] == 1 and ca["pos_pass"] == 1


def test_assurance_panel_shows_engine_eval_and_live_controls():
    records = {"pos": [("2026-07-24", {"control_check": {"kind": "control-positive", "pass": True}})]}
    engine_eval = {"passed": True, "matrix": {"FP": 0, "TP": 5, "TN": 11, "FN": 0},
                   "vocab_families_exercised": 11, "reference_families_total": 25,
                   "engine_commit": "abc1234", "generated": "2026-07-24"}
    html = build._assurance_panel(records, engine_eval)
    assert "Live control accuracy" in html and "Engine eval (hermetic)" in html
    assert "0 false positives" in html
    assert "11 of 25 reference families" in html
    assert "gate PASS" in html


def test_assurance_panel_degrades_without_engine_eval():
    html = build._assurance_panel({}, {})
    assert "engine eval summary not published yet" in html
    assert "no control runs yet" in html


def test_engine_eval_absent_is_empty(tmp_path):
    assert build._load_engine_eval(str(tmp_path)) == {}


# --- Per-target detail pages + drift timeline -------------------------------

def test_slug_is_url_safe():
    assert build._slug("control-openai-negative") == "control-openai-negative"
    assert build._slug("Weird/Name Co.") == "weird_name_co."


def test_detail_page_marks_fingerprint_change():
    records = [
        ("2026-07-20", {"fingerprint_id": "aaa", "target": {"kind": "aggregator"},
                        "tokenizer": {"usable": True}}),
        ("2026-07-21", {"fingerprint_id": "aaa", "target": {"kind": "aggregator"},
                        "tokenizer": {"usable": True}}),
        ("2026-07-22", {"fingerprint_id": "bbb", "target": {"kind": "aggregator"},
                        "tokenizer": {"usable": False}}),  # swap + usage suppressed
    ]
    html_out = build._detail_page("t1", records, None,
                                  now_iso="2026-07-24T00:00", probe_url="http://x")
    assert "3 run(s)" in html_out and "1 fingerprint change(s)" in html_out
    assert "changed" in html_out and "stable" in html_out
    assert "suppressed" in html_out               # tokenizer coverage shown
    assert "&larr; Observatory" in html_out       # back link


def test_detail_page_withholds_interpreted_for_ungated_target():
    records = [("2026-07-22", {"fingerprint_id": "x", "target": {"kind": "cn-direct"},
                               "tokenizer": {"usable": True}})]
    html_out = build._detail_page("deepseek-direct", records, None,
                                  now_iso="2026-07-24T00:00", probe_url="http://x")
    assert "withheld" in html_out                 # Gate-1: no leaked verdict


def test_build_writes_per_target_pages(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-openai-negative", "control-negative",
                   {"fingerprint_id": "fp1", "drift_seen": False,
                    "control_check": {"kind": "control-negative", "pass": True}})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00")
    page = out / "t" / "control-openai-negative.html"
    assert page.exists()
    assert "control-openai-negative" in page.read_text()
    # index links to it
    assert "t/control-openai-negative.html" in (out / "index.html").read_text()


# --- Coverage / degradation indicator ---------------------------------------

def test_coverage_full_vs_degraded():
    full = {"tokenizer": {"usable": True}, "headers": {"status": 200},
            "latency": {"p50": 1.0}, "network": {"addresses": ["1.2.3.4"]}}
    c = build._coverage(full)
    assert c["tokenizer_usable"] and not c["degraded"]
    assert set(c["layers"]) == {"network", "wire", "tokenizer", "latency"}

    degraded = {"tokenizer": {"usable": False}, "headers": {"status": 200}, "latency": {"p50": 1.0}}
    d = build._coverage(degraded)
    assert d["degraded"] is True and "tokenizer" not in d["layers"]


def test_coverage_badge_variants():
    assert "degraded" in build._coverage_badge({"tokenizer": {"usable": False}, "headers": {}})
    assert "full signal" in build._coverage_badge({"tokenizer": {"usable": True}})
    assert build._coverage_badge({"headers": {}}) == ""     # unknown -> silent


def test_coverage_note_warns_on_suppressed_usage():
    note = build._coverage_note({"tokenizer": {"usable": False}, "headers": {}, "latency": {}})
    assert "degraded coverage" in note
    assert "wire + latency only" in note


# --- functional footer, pages, evidence bundles, richer advisories ----------

def _seed_manifest(data_dir, date_str, signed=True):
    md = os.path.join(data_dir, "manifests")
    os.makedirs(md, exist_ok=True)
    with open(os.path.join(md, f"{date_str}.json"), "w") as f:
        json.dump({"schema_version": "0.1.0", "date": date_str,
                   "entries": {"t/x/verdict.json": "deadbeef"},
                   "manifest_root": "rootbeef" + "0" * 24}, f)
    if signed:
        open(os.path.join(md, f"{date_str}.json.cosign.bundle"), "w").write("{}")


def test_footer_has_working_links_not_dead_spans():
    f = build._footer()
    for href in ("methodology.html", "disclosure.html", "verify.html", "transparency-log.html"):
        assert f'href="{href}"' in f
    # the old dead <span>Methodology</span> form must be gone
    assert "<span>Methodology</span>" not in f


def test_build_generates_footer_pages_and_evidence(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "control-openai-negative", "control-negative",
                   {"fingerprint_id": "fp1", "drift_seen": False,
                    "control_check": {"kind": "control-negative", "pass": True}})
    from datetime import date as _d
    _seed_manifest(str(data), _d.today().isoformat())
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    for p in ("methodology.html", "disclosure.html", "verify.html",
              "transparency-log.html", "advisories.html"):
        assert (out / p).exists(), p
    # evidence bundle + its cosign signature copied and linkable
    assert (out / "evidence" / f"{_d.today().isoformat()}.json").exists()
    assert (out / "evidence" / f"{_d.today().isoformat()}.json.cosign.bundle").exists()
    idx = (out / "index.html").read_text()
    assert f'href="evidence/{_d.today().isoformat()}.json"' in idx      # row links to bundle
    assert "TRANSPARENCY LOG TREE HEAD: rootbeef" in idx                # bottom bar


def test_verify_and_transparency_pages_are_real():
    assert "cosign verify-blob" in build._verify_page()
    tp = build._transparency_page([{"date": "2026-07-24", "manifest_root": "abc123",
                                     "entries": {"x": "y"}, "signed": True}])
    assert "2026-07-24" in tp and "manifest_root" in tp


def test_advisory_severity_and_pages():
    assert build._severity_of({"severity": "high"}) == "high"
    assert build._severity_of({"evidence": {"monitor_changes": [{"severity": "critical"}]}}) == "high"
    assert build._severity_of({}) == "info"
    adv = {"advisory_id": "MPA-2026-001", "target": "x", "promoted_at": "2026-08-25",
           "summary": "endpoint swapped model", "severity": "high"}
    page = build._advisory_page(adv)
    assert "MPA-2026-001" in page and "HIGH" in page
    rail = build._advisories_rail({"x": adv})
    assert "VIEW ALL" in rail and "View advisory" in rail and "MPA-2026-001" in rail
