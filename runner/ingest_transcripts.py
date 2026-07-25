#!/usr/bin/env python3
"""Ingest captured session transcripts and record mid-session model switches.

Operator drops a captured conversation at:
    transcripts/<target>/<name>.json         # JSON [{role,content}] or 'Speaker: text'
    transcripts/<target>/origin.txt          # optional: 'CN' or 'nonCN' (hard evidence)

For each, this shells out to the engine (black-box CLI, T7):
    provenance-probe transcript <file> --true-origin <origin> --out <tmp>
and writes a two-tier record to data/<target>/<date>/transcript.json:
  - NEUTRAL (always published): turns analyzed, distinct identities, the
    model-change events themselves (what the model said its identity was, and
    when it switched).
  - INTERPRETED (withheld unless the target is public/cleared): the
    misrepresentation verdict + finding text.

The engine's `transcript` exits 2 on a switch/misrepresentation (like `monitor`),
so 0 and 2 are both success here.
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

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))
TRANSCRIPTS_DIR = os.environ.get("OBSERVATORY_TRANSCRIPTS_DIR", os.path.join(ROOT, "transcripts"))
SCHEMA_VERSION = "0.1.0"


def _origin(target_dir: str) -> str | None:
    p = os.path.join(target_dir, "origin.txt")
    if os.path.exists(p):
        v = open(p).read().strip()
        return v if v in ("CN", "nonCN") else None
    return None


def analyze_file(conv_path: str, origin: str | None) -> dict:
    """Run the engine transcript analyzer on one file; return its JSON result."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "r.json")
        cmd = ["provenance-probe", "transcript", conv_path, "--out", out]
        if origin:
            cmd += ["--true-origin", origin]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode not in (0, 2):
            raise RuntimeError(f"transcript analyzer failed ({r.returncode}): {r.stderr[:200]}")
        with open(out) as f:
            return json.load(f)


def split(result: dict, *, target: str, public: bool) -> dict:
    """Two-tier record. Events are neutral; the verdict is interpreted."""
    corr = result.get("correlation") or {}
    rec = {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "turns_analyzed": result.get("turns_analyzed", 0),
        "distinct_identities": result.get("distinct_identities", []),
        "model_change_events": result.get("model_change_events", []),
        "event_count": len(result.get("model_change_events", [])),
    }
    if public:
        rec["verdict"] = {"misrepresentation": bool(corr.get("misrepresentation")),
                          "severity": corr.get("severity"), "finding": corr.get("finding")}
    else:
        rec["verdict"] = {"withheld": True}
    return rec


def ingest(transcripts_dir: str = TRANSCRIPTS_DIR, data_dir: str = DATA_DIR,
           *, public_targets: set[str] | None = None, today: str | None = None) -> list[str]:
    public_targets = public_targets or set()
    today = today or date.today().isoformat()
    written = []
    if not os.path.isdir(transcripts_dir):
        return written
    for target_dir in sorted(glob.glob(os.path.join(transcripts_dir, "*"))):
        if not os.path.isdir(target_dir):
            continue
        target = os.path.basename(target_dir)
        origin = _origin(target_dir)
        convs = sorted(glob.glob(os.path.join(target_dir, "*.json")))
        if not convs:
            continue
        # newest conversation file drives the day's record
        try:
            result = analyze_file(convs[-1], origin)
        except Exception as e:  # noqa: BLE001 — never let one target abort the run
            print(f"[warn] transcript ingest {target}: {e}")
            continue
        rec = split(result, target=target, public=(target in public_targets))
        out_dir = os.path.join(data_dir, target, today)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "transcript.json")
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        written.append(path)
        n = rec["event_count"]
        print(f"[ok] {target}: {n} model-change event(s) -> {path}")
    return written


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcripts", default=TRANSCRIPTS_DIR)
    ap.add_argument("--data", default=DATA_DIR)
    ap.add_argument("--public", default="", help="comma-separated public/cleared target names")
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    pub = {t.strip() for t in a.public.split(",") if t.strip()}
    ingest(a.transcripts, a.data, public_targets=pub, today=a.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
