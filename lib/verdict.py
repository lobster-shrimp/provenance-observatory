"""Verdict record assembly — FULL-TRANSPARENCY posture.

The observatory publishes its complete work as collected: the measurements AND
the interpreted verdict, together, so consumers can see exactly how every
verdict was reached and judge confidence for themselves. There is no
withholding tier and no disclosure-window delay — accuracy is served by
transparency plus the accuracy safeguards elsewhere (known-answer + negative
controls, a published false-positive rate, per-verdict confidence labels, and
prominent corrections/retractions), not by hiding evidence.

`split()` is retained as the single record-assembly point so the runner and the
site never drift. It now returns a fully-populated public record; the second
tuple element is kept only for backward compatibility with existing callers.
"""
from __future__ import annotations

SCHEMA_VERSION = "0.1.0"  # stable field names; bump on any breaking change

# Measurement evidence.
_NEUTRAL_KEYS = ("tokenizer", "headers", "errors", "streaming", "latency",
                 "network", "fingerprint_id", "timestamp", "target")

# Interpretation of that evidence. Published alongside it (full transparency),
# not withheld — a verdict without its supporting work is less trustworthy, not
# more.
_INTERPRETED_KEYS = ("score", "user_warning", "tokenizer_match", "deception")


def split(bundle: dict, *, target_public: bool = True) -> tuple[dict, dict]:
    """Return (public_record, gated_record).

    Full transparency: the public record carries every measurement AND the
    interpreted verdict. `target_public` is accepted for call-site compatibility
    but no longer withholds anything — nothing is gated.
    """
    public_record = {"schema_version": SCHEMA_VERSION}
    for k in _NEUTRAL_KEYS + _INTERPRETED_KEYS:
        if k in bundle:
            public_record[k] = bundle[k]
    public_record["drift_seen"] = bool(bundle.get("_drift_seen"))

    if isinstance(bundle.get("score"), dict):
        s = bundle["score"]
        public_record["verdict"] = {
            "provenance": (s.get("provenance_risk") or {}).get("verdict"),
            "jurisdiction": (s.get("jurisdictional_risk") or {}).get("verdict"),
            "confidence": s.get("confidence"),
        }

    # Backward-compatible second element; mirrors the public record now that
    # nothing is withheld.
    gated_record = dict(public_record)
    gated_record["publishable"] = True
    return public_record, gated_record
