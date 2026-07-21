#!/usr/bin/env python3
"""Nightly observatory runner.

Contract (design decision T7): provenance-probe is consumed as a BLACK-BOX CLI,
never imported. We depend only on its documented surface — `assess`,
`monitor`'s exit-2 drift contract, `fingerprint_id` — so engine refactors can't
silently break us, and a probe crash can't take down the runner.

Per-target flow (see docs/ARCHITECTURE.md):
  1. spend + probe-cap guard (U2)                          [TODO: real accounting]
  2. idempotency: skip if today's artifact already exists
  3. `provenance-probe assess` → map <name>_<ts>.json → data/<target>/<date>/
     - retry once, then commit no-verdict{reason}          [run-outcome policy]
  4. `provenance-probe monitor --baseline <pinned> --current <today>` → exit 2 = drift
  5. split into public (neutral) + gated (interpreted) via lib.verdict         [T5]
  6. on drift: hand to advisory pipeline (draft in staging)                    [T9]

This is a SKELETON. Sections marked TODO are the real build work; the
load-bearing decisions are wired so they can't be lost.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
from datetime import date

# lib is a sibling package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import verdict  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


def load_targets(path: str) -> dict:
    import yaml  # pyyaml; see requirements.txt
    with open(path) as f:
        return yaml.safe_load(f)


def today_dir(target_name: str) -> str:
    return os.path.join(DATA_DIR, target_name, date.today().isoformat())


def already_ran(target_name: str) -> bool:
    """Idempotency guard: a committed artifact for today means skip."""
    d = today_dir(target_name)
    return os.path.exists(os.path.join(d, "verdict.json")) or \
        os.path.exists(os.path.join(d, "no-verdict.json"))


def run_assess(target: dict, out_dir: str) -> dict | None:
    """Shell out to provenance-probe. Returns the parsed bundle, or None on failure.

    TODO: build a per-target config file for `assess --config`, pass
    --json-out to land the file deterministically, honor per_run_probe_cap and
    the behavioral=false / layers list, and enforce the monthly budget before
    dispatch. For now this documents the exact call shape.
    """
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["provenance-probe", "assess", "--config", "<per-target-config>",
           "--out", out_dir]  # behavioral OFF via config; latency per policy
    # TODO: subprocess.run(cmd, ...) with one retry, then no-verdict on failure.
    raise NotImplementedError(f"wire up: {' '.join(cmd)}")


def check_drift(baseline_path: str, current_path: str) -> bool:
    """monitor exits 2 on drift, 0 on no-change (engine contract, tested upstream)."""
    r = subprocess.run(
        ["provenance-probe", "monitor", "--baseline", baseline_path,
         "--current", current_path],
        capture_output=True, text=True)
    if r.returncode not in (0, 2):
        raise RuntimeError(f"monitor failed ({r.returncode}): {r.stderr[:200]}")
    return r.returncode == 2


def process_target(target: dict) -> None:
    name = target["name"]
    if already_ran(name):
        print(f"[skip] {name}: today's artifact exists")
        return
    out_dir = today_dir(name)
    # 1-3. assess with retry-then-no-verdict (TODO)
    bundle = run_assess(target, out_dir)
    bundle["_drift_seen"] = False  # set by check_drift against pinned baseline (TODO)
    # 5. two-tier split
    public_record, gated_record = verdict.split(bundle, target_public=target.get("public", False))
    with open(os.path.join(out_dir, "verdict.json"), "w") as f:
        json.dump(public_record, f, indent=2)
    # gated_record → private staging repo, not committed here (TODO: advisory.py)
    print(f"[ok] {name}: public record written to {out_dir}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--targets", default=os.path.join(os.path.dirname(__file__), "..", "targets.yaml"))
    a = ap.parse_args()
    cfg = load_targets(a.targets)
    for t in cfg.get("targets", []):
        try:
            process_target(t)
        except NotImplementedError as e:
            print(f"[scaffold] {t['name']}: {e}")
        except Exception as e:  # never let one target kill the run
            print(f"[error] {t['name']}: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
