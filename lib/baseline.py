"""Baseline lifecycle + UNSTABLE state machine (design decisions, eng review).

  STABLE ──drift (monitor exit 2)──▶ DRAFT ADVISORY OPEN ──maintainer close──▶ STABLE'
    │                                      │                (baseline ADVANCES to
    │                                      │                 advisory fingerprint)
    │            >=3 distinct fingerprints │
    │            OR >=3 alternations /14d  ▼
    └──7 stable runs, maintainer bless──  UNSTABLE
                                          (advisory auto-CLOSED,
                                           baseline NOT advanced)

CRITICAL (round-3 review bug): an UNSTABLE-triggered close does NOT advance the
baseline. The pinned baseline changes ONLY on a normal advisory close or the
post-stability blessing. `test_baseline.py` locks this.

This module is intentionally logic-only (no I/O) so it is unit-testable without
git or the network.
"""
from __future__ import annotations
from dataclasses import dataclass, field

UNSTABLE_DISTINCT_FINGERPRINTS = 3
UNSTABLE_ALTERNATIONS = 3
UNSTABLE_WINDOW_DAYS = 14
STABILITY_RUNS_TO_REBLESS = 7


@dataclass(frozen=True)
class TargetState:
    pinned_baseline: str | None          # fingerprint_id
    status: str = "STABLE"               # STABLE | DRAFT_OPEN | UNSTABLE
    open_advisory_id: str | None = None
    recent_fingerprints: tuple[str, ...] = field(default_factory=tuple)  # newest last


def on_drift(state: TargetState, current_fp: str) -> TargetState:
    """A run whose fingerprint differs from the pinned baseline."""
    history = (state.recent_fingerprints + (current_fp,))[-32:]
    if _is_unstable(history):
        # Auto-close any open draft; do NOT advance the baseline.
        return TargetState(pinned_baseline=state.pinned_baseline, status="UNSTABLE",
                           open_advisory_id=None, recent_fingerprints=history)
    if state.status == "DRAFT_OPEN":
        return TargetState(state.pinned_baseline, "DRAFT_OPEN",
                           state.open_advisory_id, history)  # dedup: append to open advisory
    return TargetState(state.pinned_baseline, "DRAFT_OPEN",
                       open_advisory_id="PENDING", recent_fingerprints=history)


def on_advisory_close(state: TargetState, advisory_fingerprint: str) -> TargetState:
    """Maintainer closes a NORMAL advisory → baseline advances."""
    return TargetState(pinned_baseline=advisory_fingerprint, status="STABLE",
                       open_advisory_id=None, recent_fingerprints=state.recent_fingerprints)


def on_stability_reblessing(state: TargetState, current_fp: str) -> TargetState:
    """After UNSTABLE, fingerprint held for STABILITY_RUNS_TO_REBLESS runs and
    the maintainer blesses a new baseline."""
    return TargetState(pinned_baseline=current_fp, status="STABLE",
                       open_advisory_id=None, recent_fingerprints=state.recent_fingerprints)


def _is_unstable(history: tuple[str, ...]) -> bool:
    window = history[-64:]
    if len(set(window)) >= UNSTABLE_DISTINCT_FINGERPRINTS:
        # crude proxy for "within window"; the runner passes a time-bounded slice
        return True
    alternations = sum(1 for a, b in zip(window, window[1:]) if a != b)
    return alternations >= UNSTABLE_ALTERNATIONS
