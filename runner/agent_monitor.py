"""Continuous agent monitoring (E2) + agent-level advisories (E3).

Runs an authorized agent through the engine on a schedule, computes a stable
AGENT FINGERPRINT (the model composition of the run), and feeds the SAME
disclosure pipeline the endpoint targets use — `advisory.on_drift` opens a draft
when tonight's agent looks different from the pinned baseline, and the intra-run
model switches travel as evidence. No new crypto, no new advisory machinery: an
agent advisory is a numbered MPA just like an endpoint one.

    engine `agent-trace <trace> --export rec.json`  ->  rec (verdict + steps)
        agent_fingerprint(rec)  ->  advisory.on_drift(name, fp, evidence)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))   # runner/ on path (repo convention)
import advisory  # noqa: E402

_SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
AGENT_TRACE_TIMEOUT = 300     # seconds — a hung engine call can't stall the nightly run


def safe_name(name: str) -> str:
    """Reject target names that could escape the data/staging tree (`/`, `..`) or
    break the manifest glob. Path-injection guard."""
    if not isinstance(name, str) or not _SAFE_NAME.match(name):
        raise ValueError(f"unsafe target name {name!r}: use [A-Za-z0-9._-] only")
    return name


def agent_fingerprint(record: dict) -> str:
    """Stable hash of the run's MODEL COMPOSITION — the per-step (kind, echoed
    model, jurisdiction basis) sequence plus the overall verdict label. Changes
    when the agent starts running a different model, routing differently, or
    egressing to a different jurisdiction; ignores volatile per-run text."""
    steps = record.get("steps") or []
    sig = [[s.get("kind"), s.get("echoed_model"), s.get("jurisdiction_basis")] for s in steps]
    label = (record.get("verdict") or {}).get("label")
    canon = json.dumps({"steps": sig, "verdict": label}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode()).hexdigest()


def run_agent_target(trace_path: str, export_path: str) -> dict:
    """Invoke the engine as a black-box CLI to assess a captured agent run and
    return the exported evidence record. Raises on a hard engine failure."""
    r = subprocess.run(
        ["provenance-probe", "agent-trace", trace_path, "--export", export_path],
        capture_output=True, text=True, timeout=AGENT_TRACE_TIMEOUT)
    if r.returncode not in (0, 2):      # 2 = a switch was detected (alert, not error)
        raise RuntimeError(f"agent-trace failed ({r.returncode}): {r.stderr[:200]}")
    with open(export_path) as f:
        return json.load(f)


def monitor_agent(target_name: str, record: dict, *, target_public: bool) -> dict:
    """Fingerprint the agent run and drive the advisory pipeline. First run seeds
    the baseline (no drift); a later run with a different composition opens a
    draft. Intra-run model switches ride along as evidence."""
    fp = agent_fingerprint(record)
    verdict = record.get("verdict") or {}
    evidence = {
        "kind": "agent",
        "verdict_label": verdict.get("label"),
        "provenance_verdict": verdict.get("provenance_verdict"),
        "jurisdiction_verdict": verdict.get("jurisdiction_verdict"),
        "steps": len(record.get("steps") or []),
        "model_switches": verdict.get("model_switches") or [],
    }
    state = advisory.load_state(target_name)
    if not state.pinned_baseline:               # first sight -> seed, no advisory
        advisory.ensure_baseline(target_name, fp)
        summary = {"action": "seeded", "target": target_name}
    elif fp != state.pinned_baseline:           # composition changed -> drift advisory
        summary = advisory.on_drift(target_name, fp, evidence, target_public=target_public)
    else:
        summary = {"action": "no-change", "target": target_name}
    return {"fingerprint": fp, "advisory": summary,
            "switch_in_run": bool(evidence["model_switches"])}


def write_agent_record(data_dir: str, target_name: str, date_str: str, record: dict,
                       filename: str = "verdict.json") -> str:
    """Drop the evidence record where the daily cosign+Rekor manifest signs it."""
    d = os.path.join(data_dir, "agents", safe_name(target_name), date_str)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "verdict.json")
    with open(path, "w") as f:
        json.dump(record, f, indent=2, sort_keys=True)
    return path
