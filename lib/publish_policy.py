"""Publication policy — what the signer will and won't put in the signed log.

Full transparency publishes the complete work, but the SIGNED manifest carries a
stronger claim ("this is our certified verdict"). Two kinds of record must NOT
enter it unattended:

  - a `via_omniroute` measurement WITHOUT a passing calibration + routing
    disclosure. Measuring through a router that injects a hidden ~2000-token
    system prompt is a proxy measurement; signing it as a verdict would launder
    it as first-party. It may only be signed once the running OmniRoute version
    has been shown to preserve the tokenizer shape (calibration passed) AND the
    router metadata is disclosed alongside.
  - a CONTRADICTED router-vs-fingerprint cross-check. That is an accusation
    about a named third party and must go to human review, never auto-publish.

Such records are QUARANTINED: excluded from the signed manifest and listed with
a reason in the manifest's `quarantined` block (and a sidecar quarantine.json),
so they stay VISIBLE (transparency) but UNCERTIFIED (accuracy). Everything else
signs normally. This is the observatory-side enforcement of the calibration gate
and CONTRADICTED-quarantine that provenance-probe produces (P2a).
"""
from __future__ import annotations

CONTRADICTED = "CONTRADICTED"


def _is_contradicted(record: dict) -> bool:
    """CONTRADICTED in EITHER the nested omniroute.cross_check OR a top-level
    cross_check quarantines the record — a non-contradicted value in one location
    must not mask a CONTRADICTED value in the other (Codex adversarial, HIGH)."""
    for cc in ((record.get("omniroute") or {}).get("cross_check"), record.get("cross_check")):
        if isinstance(cc, dict) and cc.get("state") == CONTRADICTED:
            return True
    return False


def _is_via_omniroute(record: dict) -> bool:
    """A record is a proxy measurement if it SAYS so OR carries router evidence.
    Inferring from the omniroute block closes the laundering path where a real
    proxy record simply omits/renames measurement_path (Codex adversarial, HIGH)."""
    return record.get("measurement_path") == "via_omniroute" or bool(record.get("omniroute"))


def is_publishable(record: dict) -> tuple[bool, str]:
    """Return (signable, reason). reason is '' when signable, else why it is
    quarantined. A non-dict record is treated as unsignable, not crashed."""
    if not isinstance(record, dict):
        return False, "record is not a JSON object."

    # A CONTRADICTED cross-check is quarantined regardless of measurement path —
    # it is an accusation, never an auto-published verdict.
    if _is_contradicted(record):
        return False, ("router-vs-fingerprint cross-check is CONTRADICTED — quarantined "
                       "for human review; the observatory never auto-publishes a "
                       "'router misrepresents its model' claim about a named third party.")

    if not _is_via_omniroute(record):
        mp = record.get("measurement_path", "direct")
        if mp == "direct":
            return True, ""
        return False, f"unknown measurement_path {mp!r} — not signable."

    # Proxy measurement (explicit measurement_path OR an omniroute evidence block).
    omni = record.get("omniroute") or {}
    if not (omni.get("router_headers") or omni.get("router_claim")):
        return False, ("via_omniroute record lacks routing disclosure (no x-omniroute-* "
                       "headers or router claim) — cannot certify a proxy measurement "
                       "without disclosing what routed it.")
    if (omni.get("calibration") or {}).get("passed") is not True:   # strict: True, not truthy
        return False, ("via_omniroute record has no passing calibration for the running "
                       "OmniRoute version — a proxy measurement through an injected hidden "
                       "prompt can't be signed as a first-party verdict.")
    return True, ""


def partition(records: list[tuple[str, dict]]) -> tuple[list[str], list[dict]]:
    """Split [(rel_path, record), ...] into (signable_paths, quarantined).

    quarantined is a list of {"path", "reason"} dicts, sorted by path.
    """
    signable: list[str] = []
    quarantined: list[dict] = []
    for rel, rec in records:
        ok, reason = is_publishable(rec)
        if ok:
            signable.append(rel)
        else:
            quarantined.append({"path": rel, "reason": reason})
    return signable, sorted(quarantined, key=lambda q: q["path"])
