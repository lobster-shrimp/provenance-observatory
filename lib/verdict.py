"""Two-tier verdict handling (design decision T5 / U1).

The observatory publishes MEASUREMENTS immediately but GATES ACCUSATIONS.

- Neutral evidence (token counts, headers, latency, fingerprint hash, the fact
  that drift occurred) is public the moment it's collected.
- The interpreted verdict (CONFIRMED/LIKELY jurisdiction + provenance labels
  and their `meaning` strings) is an accusation about a named operator. It is
  withheld until the responsible-disclosure window has run and the target is
  cleared for public accusatory verdicts (`public: true`).

This module is the single place that splits a provenance-probe bundle into the
public tier and the gated tier, so the split can never drift between the runner
and the site.
"""
from __future__ import annotations

SCHEMA_VERSION = "0.1.0"  # stable field names; bump on any breaking change

# Keys in a provenance-probe bundle that are neutral measurement, safe to
# publish immediately.
_NEUTRAL_KEYS = ("tokenizer", "headers", "errors", "streaming", "latency",
                 "network", "fingerprint_id", "timestamp", "target")

# Keys that carry an interpretation/accusation and are gated.
_INTERPRETED_KEYS = ("score", "user_warning", "tokenizer_match", "deception")


def split(bundle: dict, *, target_public: bool) -> tuple[dict, dict]:
    """Return (public_record, gated_record).

    public_record is always safe to commit to the public log. gated_record
    holds the interpreted verdict; it is only merged into the public feed
    after the disclosure window AND target_public is True.
    """
    public_record = {"schema_version": SCHEMA_VERSION}
    for k in _NEUTRAL_KEYS:
        if k in bundle:
            public_record[k] = bundle[k]
    # A neutral "something changed" flag is fine to publish; the interpretation
    # of WHAT changed is not.
    public_record["drift_seen"] = bool(bundle.get("_drift_seen"))

    # For a CLEARED target (public=true: our own controls, or a vendor a lawyer
    # has signed off on), the interpreted verdict IS publishable, so surface a
    # compact provenance/jurisdiction/confidence block in the public record. For
    # an un-cleared target this stays out (the site shows "withheld").
    if target_public and isinstance(bundle.get("score"), dict):
        s = bundle["score"]
        public_record["verdict"] = {
            "provenance": (s.get("provenance_risk") or {}).get("verdict"),
            "jurisdiction": (s.get("jurisdictional_risk") or {}).get("verdict"),
            "confidence": s.get("confidence"),
        }

    gated_record = {"schema_version": SCHEMA_VERSION}
    for k in _INTERPRETED_KEYS:
        if k in bundle:
            gated_record[k] = bundle[k]
    gated_record["publishable"] = bool(target_public)  # AND disclosure window (advisory.py)
    return public_record, gated_record
