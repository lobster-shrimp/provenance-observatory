#!/usr/bin/env python3
"""Nightly observatory runner.

Contract (design decision T7): provenance-probe is consumed as a BLACK-BOX CLI,
never imported. We depend only on its documented surface — `assess`,
`monitor`'s exit-2 drift contract, `fingerprint_id`.

Per-target flow:
  1. gate: controls always; commercial only if OBSERVATORY_PROBE_COMMERCIAL=1
     AND authorized (no named vendor is touched before Gate 1)
  2. probe-count cap guard (U2)
  3. idempotency: skip if today's public artifact already exists
  4. `provenance-probe assess` (behavioral+deception OFF, latency ON) into a
     PRIVATE temp dir; retry once, else commit no-verdict{reason}
  5. keep the RAW bundle in a private staging area; commit only the neutral
     tier to data/<target>/<date>/verdict.json (two-tier split, T5)
  6. `monitor` raw current vs pinned baseline (in staging) → drift flag
  7. controls: assert expectation (positive family match / negative not-CN) —
     seeds the Gate-2 false-positive record

Two-tier boundary is enforced HERE: interpreted fields (score, warning,
tokenizer_match) live only in the private staging area, never in data/.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import verdict, signing, staging_sync, publish_policy  # noqa: E402
import advisory  # noqa: E402  (runner/ is on sys.path via __file__ dir)
import build_catalog  # noqa: E402
import build_registry  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))
STAGING_DIR = os.environ.get(
    "OBSERVATORY_STAGING_DIR",
    os.path.join(os.path.expanduser("~"), ".provenance-observatory-staging"))

# provenance-probe issues roughly this many requests per run with our layer set
# (tokenizer 20 + wire ~10 + latency latency_n). Used only for the cap guard;
# not imported from the engine to keep the black-box boundary (T7).
EST_TOKENIZER = 20
EST_WIRE = 10
DEFAULT_LATENCY_N = 12


def load_config(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def today_dir(name: str) -> str:
    return os.path.join(DATA_DIR, name, date.today().isoformat())


def staging_target_dir(name: str) -> str:
    d = os.path.join(STAGING_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def advisory_live() -> bool:
    """Dry-run gate (the non-negotiable safety default).

    `monitor.fingerprint()` hashes header_shape_hash + error_signature, so a CDN
    or gateway header change flips the fingerprint with NO model change. Turning
    public advisories on before the UNSTABLE damper is confirmed to swallow that
    wire noise would manufacture a FALSE public accusation — the project's
    cardinal sin. So drift always opens/updates a private DRAFT, but promotion to
    a numbered public MPA advisory (and thus the GitHub-issue alert) happens ONLY
    when OBSERVATORY_ADVISORY_LIVE=1. Default off.
    """
    return os.environ.get("OBSERVATORY_ADVISORY_LIVE", "0") == "1"


def latest_committed_verdict(name: str) -> dict | None:
    """The most recent committed public verdict.json for a target (by date)."""
    paths = sorted(glob.glob(os.path.join(DATA_DIR, name, "*", "verdict.json")))
    if not paths:
        return None
    try:
        with open(paths[-1]) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def seed_state_from_history(name: str) -> bool:
    """Migration seed: on a first run with an empty staging repo, initialize this
    target's baseline.json + state.json from the most recent committed
    verdict.json so day-1 does NOT re-seed drift from nothing (which would make
    every target look freshly-pinned every night). No-op once persisted state
    exists — never clobbers a real baseline. Returns True if it seeded."""
    sdir = staging_target_dir(name)
    base_path = os.path.join(sdir, "baseline.json")
    state_path = os.path.join(sdir, "state.json")
    if os.path.exists(base_path) and os.path.exists(state_path):
        return False   # already persisted (round-tripped from the private repo)
    rec = latest_committed_verdict(name)
    if not rec:
        return False
    seeded = False
    if not os.path.exists(base_path):
        with open(base_path, "w") as f:      # raw bundle monitor diffs against
            json.dump(rec, f)
        seeded = True
    if not os.path.exists(state_path):
        advisory.ensure_baseline(name, rec.get("fingerprint_id", ""))
        seeded = True
    return seeded


def write_pinned(name: str) -> None:
    """Public transparency artifact: the CURRENT pinned fingerprint_id only.

    A public pinned baseline is itself a transparency feature — anyone can see
    what fingerprint we are diffing against. It carries NO gated content (no raw
    token counts); the private baseline.json drives the actual diff."""
    state = advisory.load_state(name)
    pinned = state.pinned_baseline or ""
    d = os.path.join(DATA_DIR, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "pinned.json"), "w") as f:
        json.dump({"target": name, "fingerprint_id": pinned,
                   "pinned_at": date.today().isoformat()}, f, indent=2)


def promote_public_advisory(name: str, staging_id: str) -> None:
    """LIVE path only: assign an MPA number and write the public advisory record
    to data/advisories/ (where the site/API render it and the workflow's
    model-switch alert fires). Machine guard (P2b): a quarantine-worthy advisory
    (via_omniroute without calibration+disclosure, or a CONTRADICTED cross-check)
    is never numbered and auto-published. We inspect the DRAFT's stored evidence
    (which carries measurement_path/omniroute/cross_check) BEFORE promoting, so a
    quarantined draft consumes no MPA number."""
    draft = advisory._load_advisory(name, staging_id)
    ok, reason = publish_policy.is_publishable(draft.get("evidence") or {})
    if not ok:
        print(f"::warning title=Advisory quarantined::{name} {staging_id} — {reason}")
        return
    rec = advisory.promote(name, staging_id)
    adir = os.path.join(DATA_DIR, "advisories")
    os.makedirs(adir, exist_ok=True)
    with open(os.path.join(adir, f"{rec['advisory_id']}.json"), "w") as f:
        json.dump(rec, f, indent=2)
    print(f"  [advisory] {name}: PROMOTED {rec['advisory_id']} -> data/advisories/")


def already_ran(name: str) -> bool:
    d = today_dir(name)
    return os.path.exists(os.path.join(d, "verdict.json")) or \
        os.path.exists(os.path.join(d, "no-verdict.json"))


def should_probe(target: dict) -> tuple[bool, str]:
    kind = target.get("kind", "")
    if kind.startswith("control"):
        return (bool(target.get("authorized")), "control not authorized")
    # commercial
    if os.environ.get("OBSERVATORY_PROBE_COMMERCIAL") != "1":
        return (False, "commercial gate off (OBSERVATORY_PROBE_COMMERCIAL!=1)")
    if not target.get("authorized"):
        return (False, "commercial target authorized=false (Gate 1 not cleared)")
    return (True, "")


def est_probe_count(defaults: dict, target: dict | None = None) -> int:
    n = EST_TOKENIZER + EST_WIRE
    if "latency" in (defaults.get("layers") or []):
        n += DEFAULT_LATENCY_N
    if (target or {}).get("session_boundary", defaults.get("session_boundary", False)):
        n += 2 * (EST_TOKENIZER + EST_WIRE) + SESSION_GAP   # start + end snapshots + gap
    return n


# Web-app / platform adapter fields (api_style: template). These must be passed
# through to the engine or a web-app target can't be probed (it would fall back
# to an OpenAI-shaped request the app doesn't accept). Each is a valid
# provenance_probe Target field, so the engine builds it via Target(**cfg).
_WEBAPP_FIELDS = (
    "chat_path", "models_path", "cookie_env", "request_template",
    "response_text_path", "response_prompt_tokens_path", "response_model_path",
    "stream_mode", "stream_delta_path",
)


def write_probe_config(target: dict, cfg_path: str) -> None:
    """Map a targets.yaml entry to a provenance-probe Target config JSON."""
    t = {
        "name": target["name"],
        "base_url": target["base_url"],
        "model": target.get("model", ""),
        "api_style": target.get("api_style", "openai"),
        "authorized": bool(target.get("authorized")),
    }
    if target.get("auth_env"):
        t["auth_value_env"] = target["auth_env"]
    # Pass web-app template fields through when present (empty/None dropped so
    # non-webapp targets keep the engine's defaults).
    for k in _WEBAPP_FIELDS:
        v = target.get(k)
        if v not in (None, "", {}):
            t[k] = v
    with open(cfg_path, "w") as f:
        json.dump([t], f)


def run_assess(target: dict, defaults: dict) -> dict:
    """Shell out to provenance-probe assess. Returns the raw bundle.

    Layers: tokenizer + wire (always) + latency; behavioral and deception OFF
    (U1). --offline skips RDAP (controls are self-hosted). Retry once; the
    caller turns a second failure into a no-verdict artifact.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "cfg.json")
        write_probe_config(target, cfg)
        cmd = ["provenance-probe", "assess", "--config", cfg, "--out", tmp,
               "--no-behavioral", "--no-deception", "--offline"]
        if "latency" in (defaults.get("layers") or []):
            cmd += ["--latency", "--latency-n", str(DEFAULT_LATENCY_N)]
        # Probe randomization (evasion hardening): rotate the exact probe bytes.
        # The engine reference the workflow installs must be built for the same
        # seed, so rotating means rebuilding the reference at that seed too.
        seed = os.environ.get("OBSERVATORY_VARIANT_SEED", "0")
        if seed not in ("", "0"):
            cmd += ["--variant-seed", seed]
        last_err = ""
        for attempt in (1, 2):
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if r.returncode == 0:
                hits = sorted(glob.glob(os.path.join(tmp, f"{target['name']}_*.json")))
                if hits:
                    with open(hits[-1]) as f:
                        return json.load(f)
                last_err = "assess exited 0 but wrote no json"
            else:
                last_err = (r.stderr or r.stdout or "assess failed").strip()[:300]
        raise RuntimeError(last_err)


def check_drift(target_name: str, current: dict) -> tuple[bool, list]:
    """monitor raw current vs pinned baseline (both in private staging).

    First run seeds the baseline and reports no drift. monitor exits 2 on drift,
    0 on no-change (engine contract, tested upstream). Returns (drift, changes)
    where changes is monitor's list of detected field changes (evidence).
    """
    sdir = staging_target_dir(target_name)
    cur_path = os.path.join(sdir, "current.json")
    with open(cur_path, "w") as f:
        json.dump(current, f)
    base_path = os.path.join(sdir, "baseline.json")
    if not os.path.exists(base_path):
        with open(base_path, "w") as f:      # seed pinned baseline
            json.dump(current, f)
        return (False, [])
    diff_path = os.path.join(sdir, "monitor.json")
    r = subprocess.run(
        ["provenance-probe", "monitor", "--baseline", base_path, "--current", cur_path,
         "--json-out", diff_path],
        capture_output=True, text=True)
    if r.returncode not in (0, 2):
        raise RuntimeError(f"monitor failed ({r.returncode}): {r.stderr[:200]}")
    changes = []
    if os.path.exists(diff_path):
        with open(diff_path) as f:
            changes = (json.load(f) or {}).get("changes", [])
    return (r.returncode == 2, changes)


def check_control(target: dict, bundle: dict) -> dict | None:
    """Validate a control against its expectation. Seeds the Gate-2 FP record.
    Control results are about YOUR OWN endpoints, so they are publishable."""
    kind = target.get("kind", "")
    if not kind.startswith("control"):
        return None
    matches = bundle.get("tokenizer_match") or []
    top = matches[0] if matches else {}
    measured = bool((bundle.get("tokenizer") or {}).get("usable"))
    result = {"kind": kind, "top_model": top.get("model"),
              "top_score": top.get("score"), "top_origin": top.get("origin"),
              "measured": measured}
    if not measured:
        # The tokenizer layer returned nothing (429/5xx/usage suppressed). A
        # control that measured nothing neither passed nor failed — recording
        # it as PASS would let an unmeasured day count toward the published
        # 0-false-positive tally. pass=None means "not measured".
        result["pass"] = None
        return result
    if kind == "control-positive":
        want = target.get("expect_family")
        result["expected_family"] = want
        result["pass"] = bool(top.get("model") == want and (top.get("score") or 0) >= 0.9)
    elif kind == "control-negative":
        result["expect_not_origin"] = target.get("expect_not_origin")
        # PASS if the top match is not the forbidden origin (or confidence too low to call)
        result["pass"] = not (top.get("origin") == target.get("expect_not_origin")
                              and (top.get("score") or 0) >= 0.75)
    return result


_AUTH_STATUSES = ("401", "403")


def auth_failure_status(bundle: dict) -> str | None:
    """Return '401'/'403' when EVERY tokenizer probe failed with that auth status
    (and nothing was usable); None otherwise. Mixed or partial failures are
    left alone — those are real (if degraded) measurements."""
    tok = bundle.get("tokenizer") or {}
    if tok.get("usable"):
        return None
    errs = tok.get("errors") or {}
    if not errs:
        return None
    statuses = set()
    for msg in errs.values():
        m = re.search(r"HTTP (\d{3})", str(msg))
        if not m:
            return None
        statuses.add(m.group(1))
    if len(statuses) == 1 and statuses <= set(_AUTH_STATUSES):
        return statuses.pop()
    return None


def write_no_verdict(name: str, reason: str) -> None:
    d = today_dir(name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "no-verdict.json"), "w") as f:
        json.dump({"schema_version": verdict.SCHEMA_VERSION,
                   "target": name, "date": date.today().isoformat(),
                   "outcome": "no-verdict", "reason": reason}, f, indent=2)
    print(f"[no-verdict] {name}: {reason}")


SESSION_GAP = int(os.environ.get("OBSERVATORY_SESSION_GAP", "8"))


def session_boundary_enabled(target: dict, defaults: dict) -> bool:
    """Opt-in per target (or default): the boundary check costs extra probes, so
    it's off unless `session_boundary: true` is set on the target or defaults."""
    return bool(target.get("session_boundary", defaults.get("session_boundary", False)))


def run_session_boundary(target: dict) -> dict:
    """Shell out to `provenance-probe session` (black-box CLI). Detects a swap
    WITHIN one session. Returns the boundary-check JSON."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "cfg.json")
        write_probe_config(target, cfg)
        out = os.path.join(tmp, "s.json")
        cmd = ["provenance-probe", "session", "--config", cfg,
               "--gap-probes", str(SESSION_GAP), "--out", out, "--i-am-authorized"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode not in (0, 2):   # 2 = switch detected (like monitor)
            raise RuntimeError((r.stderr or r.stdout or "session failed").strip()[:200])
        with open(out) as f:
            return json.load(f)


def process_target(target: dict, defaults: dict, budget: dict) -> None:
    name = target["name"]
    ok, why = should_probe(target)
    if not ok:
        print(f"[skip] {name}: {why}")
        return
    if already_ran(name):
        print(f"[skip] {name}: today's artifact exists")
        return
    cap = target.get("per_run_probe_cap", defaults.get("per_run_probe_cap", 200))
    est = est_probe_count(defaults, target)
    if est > cap:
        write_no_verdict(name, f"per-run probe cap exceeded ({est}>{cap})")
        return

    try:
        bundle = run_assess(target, defaults)
    except Exception as e:
        write_no_verdict(name, f"assess failed after retry: {e}")
        return
    auth_fail = auth_failure_status(bundle)
    if auth_fail:
        # Every probe was rejected at the door (401/403). That measures OUR
        # credential, not the vendor's backend: publishing it as a verdict
        # mislabels an operator misconfiguration as "degraded vendor signal"
        # and seeds a bogus baseline fingerprint. Record a no-verdict instead.
        write_no_verdict(name, f"auth failed (HTTP {auth_fail}) — check the "
                               f"{target.get('auth_env') or target.get('cookie_env') or 'credential'} secret")
        return

    advisory.ensure_baseline(name, bundle.get("fingerprint_id", ""))
    changes: list = []
    try:
        bundle["_drift_seen"], changes = check_drift(name, bundle)
    except Exception as e:
        print(f"[warn] {name}: drift check skipped: {e}")
        bundle["_drift_seen"] = False

    public_record, gated_record = verdict.split(bundle, target_public=target.get("public", False))
    control = check_control(target, bundle)
    if control is not None:
        public_record["control_check"] = control   # neutral: about our own endpoint

    # Session-boundary check (P2): did the served model swap WITHIN one session?
    # The start/end fingerprints are neutral evidence; a swap opens an advisory.
    sb_switched = False
    if session_boundary_enabled(target, defaults):
        try:
            sb = run_session_boundary(target)
            sb_switched = bool(sb["boundary_switch"])
            public_record["session_boundary"] = {
                "start_fingerprint": sb["start_fingerprint"][:24],
                "end_fingerprint": sb["end_fingerprint"][:24],
                "switched": sb_switched, "confidence": sb["confidence"],
                "gap_probes": sb.get("gap_probes")}
        except Exception as e:
            print(f"[warn] {name}: session boundary skipped: {e}")

    out_dir = today_dir(name)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "verdict.json"), "w") as f:
        json.dump(public_record, f, indent=2)
    # interpreted tier stays private
    with open(os.path.join(staging_target_dir(name), f"{date.today().isoformat()}.gated.json"), "w") as f:
        json.dump(gated_record, f, indent=2)

    # On drift for a VENDOR target, open/append a draft advisory in staging.
    # Controls drifting is a control-health signal, not a vendor advisory.
    if bundle["_drift_seen"] and not target.get("kind", "").startswith("control"):
        evidence = {
            "verdict": (gated_record.get("score") or {}),
            "monitor_changes": changes,
            # Carry the provenance-of-the-measurement so the P2b quarantine guard
            # (publish_policy.is_publishable) can actually see it at promotion: a
            # via_omniroute / CONTRADICTED drift must never auto-publish.
            "measurement_path": public_record.get("measurement_path", "direct"),
        }
        if bundle.get("omniroute"):
            evidence["omniroute"] = bundle["omniroute"]
        if bundle.get("cross_check"):
            evidence["cross_check"] = bundle["cross_check"]
        summary = advisory.on_drift(name, bundle.get("fingerprint_id", ""), evidence,
                                    target_public=target.get("public", False))
        print(f"  [advisory] {name}: {summary.get('action')} "
              f"{summary.get('staging_id', '')}".rstrip())
        # DRAFT-only by default. Promote to a numbered PUBLIC advisory (and thus
        # trigger the GitHub-issue alert) ONLY when OBSERVATORY_ADVISORY_LIVE=1.
        sid = summary.get("staging_id")
        if summary.get("action") in ("opened", "appended") and sid:
            if advisory_live():
                promote_public_advisory(name, sid)
            else:
                print(f"::notice title=Drift DRAFT (dry-run)::{name} {sid} staged; "
                      f"OBSERVATORY_ADVISORY_LIVE!=1, no public advisory emitted")

    # Public transparency artifact: the current pinned fingerprint_id (only).
    write_pinned(name)

    # Intra-session model switch on a VENDOR target -> model-switch advisory.
    if sb_switched and not target.get("kind", "").startswith("control"):
        sbrec = public_record["session_boundary"]
        events = [{"turn": "session", "from": sbrec["start_fingerprint"][:8],
                   "to": sbrec["end_fingerprint"][:8], "kind": "boundary"}]
        s = advisory.on_model_switch(
            name, events, verdict={"boundary": True, "confidence": sbrec["confidence"]},
            summary=(f"served model changed within a single session "
                     f"({sbrec['start_fingerprint'][:8]} -> {sbrec['end_fingerprint'][:8]})"),
            severity="high", target_public=target.get("public", False))
        print(f"  [advisory] {name}: session-switch {s.get('action')} "
              f"{s.get('staging_id', '')}".rstrip())

    status = "SESSION-SWITCH" if sb_switched else ("DRIFT" if bundle["_drift_seen"] else "stable")
    ctl = ""
    if control:
        ctl = " control=" + ("NOT-MEASURED" if control["pass"] is None
                             else ("PASS" if control["pass"] else "FAIL"))
    print(f"[ok] {name}: {status}{ctl} → {out_dir}/verdict.json")
    if control and control["pass"] is False:
        print(f"  [FP-GATE] {name}: control expectation FAILED — {control}")


def process_agent_target(target: dict) -> None:
    """E2/E3: assess a captured agent run via the engine, drop the signed-ready
    record, and drive the advisory pipeline on any composition drift."""
    import agent_monitor
    name = agent_monitor.safe_name(target["name"])   # path-injection guard
    date_str = date.today().isoformat()

    ok, reason = should_probe(target)                # SAME auth/commercial gate as endpoints
    if not ok:
        agent_monitor.write_agent_record(DATA_DIR, name, date_str,
                                         {"kind": "agent", "no_verdict": reason}, "no-verdict.json")
        print(f">>> agent {name}: skipped ({reason})")
        return

    export = os.path.join(STAGING_DIR, f"{name}-agent.json")
    try:
        record = agent_monitor.run_agent_target(target["agent_trace"], export)
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError) as e:
        agent_monitor.write_agent_record(DATA_DIR, name, date_str,
                                         {"kind": "agent", "no_verdict": f"engine failed: {e}"},
                                         "no-verdict.json")
        print(f">>> agent {name}: no-verdict ({e})")
        return

    agent_monitor.write_agent_record(DATA_DIR, name, date_str, record)
    result = agent_monitor.monitor_agent(name, record, target_public=target.get("public", False))
    print(f">>> agent {name}: verdict={record.get('verdict',{}).get('label')} "
          f"fp={result['fingerprint'][:8]} advisory={result['advisory'].get('action')} "
          f"switch_in_run={result['switch_in_run']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=os.path.join(ROOT, "targets.yaml"))
    a = ap.parse_args()
    cfg = load_config(a.targets)
    defaults = cfg.get("defaults", {})
    budget = cfg.get("budget", {})
    # Pull the persisted drift state from the private staging repo (if
    # configured); otherwise fall back to local-only staging (no network). A
    # clone failure (bad PAT, repo unreachable) must NOT kill the whole run — fall
    # back to local-only so targets still get verdicts (no silent gaps). The
    # RuntimeError from staging_sync is already token-scrubbed.
    try:
        sync = staging_sync.clone_if_configured(STAGING_DIR)
    except Exception as e:
        print(f"[staging] clone failed, falling back to local-only: {e}")
        sync = {"enabled": False, "reason": "clone failed"}
    print(f"[staging] {sync.get('log', 'drift persistence OFF (clone failed); local-only')}")
    os.makedirs(STAGING_DIR, exist_ok=True)
    # Migration seed (persistence mode ONLY): initialize any target still missing
    # persisted state from its most recent committed verdict.json, so the FIRST
    # run against an empty private staging repo begins from the seed rather than
    # re-seeding from nothing. Skipped in local-only mode so the no-PAT fallback
    # keeps its current (inert) drift behavior exactly (AC #4).
    if sync.get("enabled"):
        for t in cfg.get("targets", []):
            if t.get("agent_trace"):
                continue
            try:
                if seed_state_from_history(t["name"]):
                    print(f"[staging] seeded {t['name']} baseline from committed history")
            except Exception as e:
                print(f"[warn] seed {t.get('name','?')}: {e}")
    for t in cfg.get("targets", []):
        try:
            if t.get("agent_trace"):          # E2: continuous agent monitoring
                process_agent_target(t)
            else:
                process_target(t, defaults, budget)
        except Exception as e:   # never let one target kill the run
            print(f"[error] {t.get('name','?')}: {e}")
    # Build the day's evidence manifest (root hash over all records). Signing
    # runs in CI where an OIDC identity exists; locally it degrades gracefully.
    mpath = signing.write_manifest(DATA_DIR, date.today().isoformat())
    sig = signing.sign_manifest(mpath)
    print(f"[manifest] {mpath} (signed={sig['signed']}: {sig['reason']})")
    # Regenerate + sign the public provider-attribution registry (from the probe's
    # corpus.py, via its CLI). Degrades cleanly if the installed probe predates it.
    reg = build_registry.build_signed_registry(DATA_DIR)
    print(f"[registry] built={reg.get('built')} verified={reg.get('verified')} "
          f"signed={reg.get('signed')}: {reg['reason']}")
    # Refresh + sign the public LLM-API catalog (models.dev x corpus.py, via the
    # probe CLI). Non-deterministic upstream, so a fail-closed non-empty gate — a
    # models.dev outage is a clean no-op, never an empty publish.
    cat = build_catalog.build_signed_catalog(DATA_DIR)
    print(f"[catalog] built={cat.get('built')} verified={cat.get('verified')} "
          f"signed={cat.get('signed')}: {cat['reason']}")
    # Push the updated drift state (baseline/state/drafts/counter) back to the
    # private staging repo so the next run diffs against it. Only when the clone
    # succeeded — never push a local-only/reseeded staging over the private repo.
    if sync.get("enabled"):
        push = staging_sync.push_if_configured(STAGING_DIR, f"staging: nightly {date.today().isoformat()}")
        print(f"[staging] {push['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
