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
import subprocess
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import verdict  # noqa: E402
import advisory  # noqa: E402  (runner/ is on sys.path via __file__ dir)

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


def est_probe_count(defaults: dict) -> int:
    n = EST_TOKENIZER + EST_WIRE
    if "latency" in (defaults.get("layers") or []):
        n += DEFAULT_LATENCY_N
    return n


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
    result = {"kind": kind, "top_model": top.get("model"),
              "top_score": top.get("score"), "top_origin": top.get("origin")}
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


def write_no_verdict(name: str, reason: str) -> None:
    d = today_dir(name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "no-verdict.json"), "w") as f:
        json.dump({"schema_version": verdict.SCHEMA_VERSION,
                   "target": name, "date": date.today().isoformat(),
                   "outcome": "no-verdict", "reason": reason}, f, indent=2)
    print(f"[no-verdict] {name}: {reason}")


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
    if est_probe_count(defaults) > cap:
        write_no_verdict(name, f"per-run probe cap exceeded ({est_probe_count(defaults)}>{cap})")
        return

    try:
        bundle = run_assess(target, defaults)
    except Exception as e:
        write_no_verdict(name, f"assess failed after retry: {e}")
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
        }
        summary = advisory.on_drift(name, bundle.get("fingerprint_id", ""), evidence,
                                    target_public=target.get("public", False))
        print(f"  [advisory] {name}: {summary.get('action')} "
              f"{summary.get('staging_id', '')}".rstrip())

    status = "DRIFT" if bundle["_drift_seen"] else "stable"
    ctl = f" control={'PASS' if control['pass'] else 'FAIL'}" if control else ""
    print(f"[ok] {name}: {status}{ctl} → {out_dir}/verdict.json")
    if control and not control["pass"]:
        print(f"  [FP-GATE] {name}: control expectation FAILED — {control}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=os.path.join(ROOT, "targets.yaml"))
    a = ap.parse_args()
    cfg = load_config(a.targets)
    defaults = cfg.get("defaults", {})
    budget = cfg.get("budget", {})
    os.makedirs(STAGING_DIR, exist_ok=True)
    for t in cfg.get("targets", []):
        try:
            process_target(t, defaults, budget)
        except Exception as e:   # never let one target kill the run
            print(f"[error] {t.get('name','?')}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
