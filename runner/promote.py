#!/usr/bin/env python3
"""Maintainer action: promote a staged draft advisory to a numbered public one.

Assigns MPA-YYYY-NNN and writes the public record to data/advisories/<id>.json
(where the site + /api/advisories render it). Full transparency: promotion is
unconditional — no public/Gate-1 gate and no disclosure-window delay. The
--force-window flag is retained for compatibility and is now a no-op.

    python runner/promote.py <target> --latest [--force-window]
    python runner/promote.py <target> --staging-id <id>
    python runner/promote.py --list <target>
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import advisory  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))


def _drafts(target: str) -> list[dict]:
    d = os.path.join(advisory._target_dir(target), "advisories")
    out = []
    for p in sorted(glob.glob(os.path.join(d, "*.json"))):
        with open(p) as f:
            out.append(json.load(f))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target")
    ap.add_argument("--staging-id", default=None)
    ap.add_argument("--latest", action="store_true", help="promote the newest draft")
    ap.add_argument("--list", action="store_true", help="list staged drafts and exit")
    ap.add_argument("--force-window", action="store_true",
                    help="retained for compatibility; no-op under full transparency")
    ap.add_argument("--data", default=DATA_DIR)
    a = ap.parse_args()

    drafts = _drafts(a.target)
    if a.list or (not a.staging_id and not a.latest):
        for d in drafts:
            print(f"  {d['staging_id']}  status={d['status']}  public={d.get('public')}  "
                  f"advisory_id={d.get('advisory_id')}")
        return 0

    sid = a.staging_id
    if a.latest:
        openish = [d for d in drafts if not d.get("advisory_id")]
        if not openish:
            print("no un-promoted drafts")
            return 1
        sid = openish[-1]["staging_id"]

    rec = advisory.promote(a.target, sid, force_window=a.force_window)
    adir = os.path.join(a.data, "advisories")
    os.makedirs(adir, exist_ok=True)
    out = os.path.join(adir, f"{rec['advisory_id']}.json")
    with open(out, "w") as f:
        json.dump(rec, f, indent=2)
    print(f"promoted {sid} -> {rec['advisory_id']} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
