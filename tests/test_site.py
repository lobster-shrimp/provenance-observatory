"""Pages renderer: renders the complete work from data/ (measurements AND the
interpreted verdict, as collected — nothing withheld), shows control checks, and
falls back to an em-dash only when a record genuinely carries no verdict."""
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


def test_renders_measurements_and_no_verdict_em_dash(tmp_path):
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
    # this record genuinely carries no verdict: the row has empty data-prov and
    # shows an em-dash (never a fabricated CONFIRMED badge). Full transparency
    # never withholds a verdict that exists.
    assert 'data-prov=""' in doc
    assert 'data-prov="CONFIRMED"' not in doc
    assert '<span class="badge cn">CONFIRMED' not in doc


def _write_agent(data_dir, target, rec):
    d = os.path.join(data_dir, "agents", target, date.today().isoformat())
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "verdict.json"), "w") as f:
        json.dump(rec, f)


def test_agent_panel_shows_verdict_regardless_of_public(tmp_path):
    # Full transparency: every probed agent's per-model board and verdict are
    # shown, whether or not the record carries a public flag. Nothing withheld.
    data = tmp_path / "data"
    base = {"kind": "agent", "endpoint": "api.vendor.com",
            "verdict": {"label": "CONFIRMED", "provenance_verdict": "CONFIRMED",
                        "jurisdiction_verdict": "CONFIRMED"},
            "steps": [{"echoed_model": "vendor-x", "provenance": "CONFIRMED",
                       "jurisdiction": "CONFIRMED", "jurisdiction_basis": "PRC"}]}
    _write_agent(str(data), "agent-ungated", dict(base))                  # no public flag
    _write_agent(str(data), "agent-flagged", {**base, "public": True})
    doc = open(build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")).read()
    assert "Agent &amp; platform assessments" in doc
    assert "VERDICT WITHHELD" not in doc                                 # nothing is withheld
    assert "agent-ungated" in doc and "agent-flagged" in doc             # both boards shown
    assert "vendor-x" in doc                                             # model rows rendered
    assert "CONFIRMED" in doc                                            # verdict badge shown


def test_agent_panel_tolerates_missing_echoed_model(tmp_path):
    # a step with no echoed model id must not crash the renderer (html.escape(None)).
    data = tmp_path / "data"
    rec = {"kind": "agent", "endpoint": "api.vendor.com", "public": True,
           "verdict": {"label": "UNLIKELY", "provenance_verdict": "UNLIKELY",
                       "jurisdiction_verdict": "UNLIKELY"},
           "steps": [{"echoed_model": None, "provenance": "UNLIKELY",
                      "jurisdiction": "UNLIKELY"}]}
    _write_agent(str(data), "agent-noecho", rec)
    doc = open(build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-26T12:00:00")).read()
    assert "agent-noecho" in doc and "&mdash;" in doc   # em-dash fallback for the blank cell


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


def test_detail_page_shows_interpreted_verdict_as_collected():
    # Full transparency: the interpreted verdict is shown on the detail page as
    # collected, for any target — no Gate-1 gate, nothing withheld.
    records = [("2026-07-22", {"fingerprint_id": "x", "target": {"kind": "cn-direct"},
                               "tokenizer": {"usable": True},
                               "verdict": {"provenance": "CONFIRMED",
                                           "jurisdiction": "CONFIRMED",
                                           "confidence": "high"}})]
    html_out = build._detail_page("deepseek-direct", records, None,
                                  now_iso="2026-07-24T00:00", probe_url="http://x")
    assert "CONFIRMED" in html_out                # verdict shown, not withheld
    assert "VERDICT WITHHELD" not in html_out


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


# --- P2b: quarantine surfaced on the transparency log -----------------------

def test_transparency_page_surfaces_quarantined_records():
    manifests = [{"date": "2026-07-28", "manifest_root": "a" * 64, "entries": {"t1/x": "h"},
                  "quarantined": [{"path": "omni/2026-07-28/verdict.json",
                                   "reason": "via_omniroute record has no passing calibration"}]}]
    page = build._transparency_page(manifests)
    assert "Quarantined records (uncertified)" in page
    assert "no passing calibration" in page
    assert "omni/2026-07-28/verdict.json" in page


def test_transparency_page_no_quarantine_section_when_clean():
    manifests = [{"date": "2026-07-28", "manifest_root": "a" * 64, "entries": {"t1/x": "h"}}]
    page = build._transparency_page(manifests)
    assert "Quarantined records (uncertified)" not in page


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
    # the footer "API" link now points at the on-site data dictionary, not a dead
    # localhost API docs server
    assert 'href="data-dictionary.html"' in f
    assert "/api/docs" not in f and "127.0.0.1" not in f


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


# --- P2: interactive frontend (nav, controls, client-side filter data) ------

def test_nav_and_controls_present(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "some-aggregator", "aggregator",
                   {"fingerprint_id": "fp", "drift_seen": True, "tokenizer": {"usable": True}})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    idx = (out / "index.html").read_text()
    assert 'class="nav"' in idx and 'id="stbadge"' in idx           # STATUS badge
    assert 'href="about.html"' in idx and 'href="feed.xml"' in idx  # ABOUT + RSS
    # API link points at the real machine-readable records (data/ tree), and the
    # live-probe link is the hosted demo — no dead localhost links on the static site.
    assert "tree/main/data" in idx                                  # API -> data records
    assert "provenance-probe-513338163479.us-central1.run.app" in idx  # hosted live probe
    assert "127.0.0.1" not in idx                                   # no localhost links
    assert '<link rel="alternate" type="application/rss+xml"' in idx   # RSS autodiscovery
    assert 'class="controls"' in idx and 'id="q"' in idx            # SEARCH + filters
    assert 'id="vtable"' in idx and 'id="viewmore"' in idx          # table + VIEW MORE
    assert "<script>" in idx                                        # enhancement JS


def test_rows_carry_filter_data_attributes(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "some-aggregator", "aggregator",
                   {"fingerprint_id": "fp", "drift_seen": True, "tokenizer": {"usable": True},
                    "verdict": {"provenance": "CONFIRMED", "jurisdiction": "CN", "confidence": "high"}})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    idx = (out / "index.html").read_text()
    assert 'data-target="some-aggregator"' in idx
    assert 'data-kind="aggregator"' in idx
    assert 'data-prov="CONFIRMED"' in idx and 'data-juris="CN"' in idx
    assert 'data-drift="1"' in idx


def test_static_feed_and_about_generated(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "t1", "aggregator", {"fingerprint_id": "fp", "drift_seen": True})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    assert (out / "feed.xml").exists() and (out / "about.html").exists()
    from xml.dom import minidom
    minidom.parseString((out / "feed.xml").read_text())             # valid RSS
    assert "Drift: t1" in (out / "feed.xml").read_text()


def test_api_url_is_configurable(tmp_path, monkeypatch):
    # OBSERVATORY_API_URL still overrides the API link for anyone hosting a real
    # API; the link now uses the URL verbatim (no hardcoded /api/docs suffix) since
    # the default target is the static data-records tree, not a docs server.
    monkeypatch.setenv("OBSERVATORY_API_URL", "https://obs.example.org")
    data = tmp_path / "data"
    _write_verdict(str(data), "t1", "aggregator", {"fingerprint_id": "fp"})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    idx = (out / "index.html").read_text()
    assert 'href="https://obs.example.org"' in idx                  # nav link (verbatim)
    assert 'API="https://obs.example.org"' in idx                   # injected into JS


def test_default_links_are_public_not_localhost(tmp_path):
    # Regression guard for the static-site broken-link fix: the built index must
    # ship the hosted probe + the data-records API link and zero localhost links.
    data = tmp_path / "data"
    _write_verdict(str(data), "t1", "aggregator", {"fingerprint_id": "fp"})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    idx = (out / "index.html").read_text()
    assert "127.0.0.1" not in idx and "localhost" not in idx
    assert build.DEFAULT_PROBE_URL in idx
    assert build.DEFAULT_API_URL in idx


# --- P3: content pages + Rekor transparency surfacing -----------------------

def test_content_pages_generated_and_linked(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "t1", "aggregator", {"fingerprint_id": "fp"})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    for p in ("how-it-works.html", "faq.html", "data-dictionary.html"):
        assert (out / p).exists(), p
    idx = (out / "index.html").read_text()
    for href in ("how-it-works.html", "faq.html", "data-dictionary.html"):
        assert f'href="{href}"' in idx                       # footer Resources links
    assert "API &amp; data records" in idx                   # footer API link (-> data dictionary)


def test_rekor_index_parsed_and_surfaced():
    # real committed bundle carries a Rekor logIndex; it must be parsed + shown
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from lib import records
    ms = records.load_manifests(os.path.join(os.path.dirname(__file__), "..", "data"))
    signed = [m for m in ms if m.get("signed")]
    assert signed and any(isinstance(m.get("rekor_log_index"), int) for m in signed)
    tp = build._transparency_page(ms)
    assert "Rekor #" in tp and "search.sigstore.dev" in tp


def test_rekor_index_none_when_unsigned(tmp_path):
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from lib import records
    md = tmp_path / "data" / "manifests"
    md.mkdir(parents=True)
    (md / "2026-07-24.json").write_text(json.dumps(
        {"date": "2026-07-24", "entries": {}, "manifest_root": "r"}))
    ms = records.load_manifests(str(tmp_path / "data"))
    assert ms[0]["signed"] is False and ms[0]["rekor_log_index"] is None


# --- Policy pages (Security / Privacy / Terms) + security.txt ----------------

def test_policy_pages_generated_and_linked(tmp_path):
    data = tmp_path / "data"
    _write_verdict(str(data), "t1", "aggregator", {"fingerprint_id": "fp"})
    out = tmp_path / "out"
    build.build(str(data), str(out), now_iso="2026-07-24T00:00:00")
    for p in ("security.html", "privacy.html", "terms.html"):
        assert (out / p).exists(), p
    idx = (out / "index.html").read_text()
    for href in ("security.html", "privacy.html", "terms.html"):
        assert f'href="{href}"' in idx                       # footer Policies links
    # honest, accurate content (not fabricated legal claims)
    assert "not proof" in (out / "terms.html").read_text()
    assert "no cookies" in (out / "privacy.html").read_text().lower()
    assert "Reporting a vulnerability" in (out / "security.html").read_text()


def test_security_txt_generated(tmp_path):
    out = tmp_path / "out"
    build.build(str(tmp_path / "data"), str(out), now_iso="2026-07-24T00:00:00")
    st = out / ".well-known" / "security.txt"
    assert st.exists()
    body = st.read_text()
    assert body.startswith("Contact:") and "Expires:" in body


def test_advisory_page_renders_model_switch():
    adv = {"advisory_id": "MPA-2026-001", "target": "chat-z-ai-webapp",
           "promoted_at": "2026-07-25", "kind": "model_switch", "severity": "high",
           "summary": "served model switched to GLM (Zhipu)",
           "model_change_events": [{"turn": 7, "from": "Google Gemini",
                                    "to": "GLM (Zhipu)", "kind": "concession"}]}
    page = build._advisory_page(adv)
    assert "MPA-2026-001" in page and "HIGH" in page
    assert "mid-session model switch" in page
    assert "Google Gemini" in page and "GLM (Zhipu)" in page


def test_session_boundary_note_renders():
    switched = {"session_boundary": {"start_fingerprint": "aaaaaaaa11", "end_fingerprint": "bbbbbbbb22",
                                     "switched": True, "confidence": "full"}}
    h = build._session_boundary_note(switched)
    assert "Intra-session model switch" in h and "aaaaaaaa" in h and "bbbbbbbb" in h
    stable = {"session_boundary": {"start_fingerprint": "cccccccc", "end_fingerprint": "cccccccc",
                                   "switched": False, "confidence": "full"}}
    assert "stable across the session" in build._session_boundary_note(stable)
    assert build._session_boundary_note({}) == ""


# --- LLM-API catalog page (public running table) ----------------------------

def _write_catalog(data_dir, doc, *, signed=False):
    d = os.path.join(data_dir, "catalog")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "catalog.json")
    with open(p, "w") as f:
        json.dump(doc, f)
    if signed:
        open(p + ".cosign.bundle", "w").close()
    return p


_CAT = {
    "catalog_version": "1", "corpus_version": "2026.07.2",
    "provider_count": 2, "model_count": 3,
    "providers": [
        {"provider_id": "deepseek", "name": "DeepSeek",
         "api_url": "https://api.deepseek.com/v1", "api_host": "api.deepseek.com",
         "provenance": {"jurisdiction": "PRC", "kind": "prc", "measured": False},
         "models": [{"id": "deepseek-chat", "family": "deepseek", "context": 128000,
                     "cost_input": 0.27, "cost_output": 1.1, "open_weights": True,
                     "modalities_in": ["text"]}]},
        {"provider_id": "openai", "name": "OpenAI",
         "api_url": "https://api.openai.com/v1", "api_host": "api.openai.com",
         "provenance": {"jurisdiction": "US", "kind": "first-party"},
         "models": [{"id": "gpt-5", "context": 400000}, {"id": "gpt-5-mini"}]},
    ],
}


def _catalog_html(tmp_path, doc, *, signed=False):
    data = tmp_path / "data"
    _write_catalog(str(data), doc, signed=signed)
    outp = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    cat = open(os.path.join(os.path.dirname(outp), "catalog.html")).read()
    return cat, open(outp).read()


def test_catalog_page_shows_all_providers_and_links(tmp_path):
    cat, index = _catalog_html(tmp_path, _CAT)
    assert "DeepSeek" in cat and "api.deepseek.com" in cat        # PRC provider shown
    assert "gpt-5" in cat                                         # first-party NOW included
    assert "OpenAI" in cat
    assert "/api/catalog" in cat                                  # link to full signed data
    assert "snapshot (unsigned" in cat                           # honest unsigned badge
    assert 'catalog.html">Catalog' in index                      # nav link
    # both jurisdictions present in the embedded data
    assert '"PRC"' in cat and '"US"' in cat


def test_catalog_page_has_data_script_and_pagination(tmp_path):
    cat, _ = _catalog_html(tmp_path, _CAT)
    assert '<script id="catalog-data" type="application/json">' in cat
    assert 'id="cbody"' in cat                                    # paginated body target
    assert 'id="cprev"' in cat and 'id="cnext"' in cat           # pagination controls
    assert 'id="cinfo"' in cat                                    # "page X of Y" info
    assert 'id="cjur"' in cat and 'id="ckind"' in cat            # jurisdiction + kind filters
    assert "page '+(page+1)+' of '+pages" in cat                 # client renders page counter


def test_catalog_page_xss_escapes_hostile_external_data(tmp_path):
    # models.dev is external: a hostile provider name / model id must NOT reach the page
    # as live markup — neither in the embedded JSON script block nor the fallback rows.
    hostile = "</script><img src=x onerror=alert(1)>"
    doc = {
        "catalog_version": "1", "corpus_version": "x",
        "provider_count": 1, "model_count": 1,
        "providers": [
            {"provider_id": "evil", "name": hostile,
             "api_url": "https://evil.test", "api_host": "evil.test",
             "provenance": {"jurisdiction": "PRC", "kind": "prc"},
             "models": [{"id": hostile, "context": 1000}]},
        ],
    }
    cat, _ = _catalog_html(tmp_path, doc)
    # raw payload never present as live markup / a script-tag breakout
    assert hostile not in cat
    assert "<img src=x onerror=alert(1)>" not in cat
    assert "</script><img" not in cat
    # JSON script block carries it \\uXXXX-escaped (valid + lossless), not as raw tags
    assert "\\u003c/script\\u003e\\u003cimg" in cat
    # fallback server-rendered row carries it html-entity-escaped
    assert "&lt;/script&gt;&lt;img" in cat


def test_catalog_page_signed_badge(tmp_path):
    cat, _ = _catalog_html(tmp_path, _CAT, signed=True)
    assert ">signed<" in cat


def test_catalog_page_empty_state(tmp_path):
    data = tmp_path / "data"
    os.makedirs(str(data), exist_ok=True)
    outp = build.build(str(data), str(tmp_path / "out"), now_iso="2026-07-21T12:00:00")
    cat = open(os.path.join(os.path.dirname(outp), "catalog.html")).read()
    assert "No catalog has been published yet" in cat


def test_catalog_page_tolerates_malformed_and_escapes_counts(tmp_path):
    # External data can be malformed; the site build must not crash, and the summary
    # counts must be escaped like every other external field.
    bad = {
        "catalog_version": "1", "corpus_version": "x",
        "provider_count": "<b>x</b>", "model_count": 1,
        "providers": [
            {"name": 123, "api_host": 456,
             "provenance": {"jurisdiction": "PRC", "kind": "prc"},
             "models": "not-a-list"},                        # truthy non-list -> skipped, no crash
            {"name": "OkCo", "api_host": "h.example",
             "provenance": {"kind": "prc", "jurisdiction": "PRC"},
             "models": [{"id": 789}]},                       # numeric id -> coerced to str
            "not-a-dict",                                    # non-dict provider -> skipped
        ],
    }
    cat, _ = _catalog_html(tmp_path, bad)                    # must not raise
    assert "OkCo" in cat and "789" in cat                    # coerced + rendered
    assert "<b>x</b>" not in cat                             # provider_count escaped
    assert "&lt;b&gt;x&lt;/b&gt;" in cat
