"""Publication policy (P2b): the signer refuses to certify a via_omniroute record
without a passing calibration + routing disclosure, and never auto-publishes a
CONTRADICTED cross-check."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import publish_policy as P  # noqa: E402


def _via(*, calibrated=True, disclosed=True, state="CORROBORATED"):
    omni = {"cross_check": {"state": state}}
    if disclosed:
        omni["router_headers"] = {"model": "oc/deepseek-v4", "provider": "oc"}
        omni["router_claim"] = "oc/deepseek-v4"
    omni["calibration"] = {"passed": calibrated}
    return {"measurement_path": "via_omniroute", "omniroute": omni}


def test_direct_record_is_publishable():
    ok, reason = P.is_publishable({"measurement_path": "direct", "fingerprint_id": "x"})
    assert ok and reason == ""


def test_record_without_measurement_path_defaults_direct():
    ok, _ = P.is_publishable({"fingerprint_id": "x"})     # legacy record, no field
    assert ok


def test_via_omniroute_calibrated_and_disclosed_is_publishable():
    ok, reason = P.is_publishable(_via(calibrated=True, disclosed=True))
    assert ok and reason == ""


def test_via_omniroute_uncalibrated_is_quarantined():
    ok, reason = P.is_publishable(_via(calibrated=False))
    assert ok is False
    assert "calibration" in reason.lower()


def test_via_omniroute_without_disclosure_is_quarantined():
    ok, reason = P.is_publishable(_via(disclosed=False, calibrated=True))
    # No router headers/claim -> no disclosure. (calibration block alone isn't disclosure.)
    rec = _via(disclosed=False, calibrated=True)
    rec["omniroute"] = {"calibration": {"passed": True}, "cross_check": {"state": "CORROBORATED"}}
    ok, reason = P.is_publishable(rec)
    assert ok is False and "disclosure" in reason.lower()


def test_contradicted_is_quarantined_regardless_of_path():
    # Even a calibrated, disclosed record is quarantined if the cross-check is a
    # mismatch — that's an accusation, never auto-published.
    ok, reason = P.is_publishable(_via(state="CONTRADICTED"))
    assert ok is False and "CONTRADICTED" in reason


def test_contradicted_on_direct_record_also_quarantined():
    rec = {"measurement_path": "direct", "cross_check": {"state": "CONTRADICTED"}}
    ok, reason = P.is_publishable(rec)
    assert ok is False


def test_unknown_measurement_path_is_quarantined():
    ok, reason = P.is_publishable({"measurement_path": "smoke_signals"})
    assert ok is False and "unknown" in reason.lower()


def test_non_dict_record_is_not_signable():
    ok, reason = P.is_publishable(["not", "a", "record"])
    assert ok is False


# --- Codex round-2 hardening regressions ------------------------------------

def test_omniroute_block_without_measurement_path_is_treated_as_proxy():
    # HIGH (Codex): a real proxy record that omits measurement_path must NOT be
    # laundered as direct — the omniroute block forces via_omniroute handling.
    rec = {"fingerprint_id": "x", "omniroute": {
        "router_headers": {"model": "oc/deepseek-v4"}, "router_claim": "oc/deepseek-v4",
        "calibration": {"passed": False}, "cross_check": {"state": "INCONCLUSIVE"}}}
    ok, reason = P.is_publishable(rec)
    assert ok is False and "calibration" in reason.lower()


def test_toplevel_contradicted_not_masked_by_nested_inconclusive():
    # HIGH (Codex): a non-contradicted nested cross_check must not hide a
    # top-level CONTRADICTED (or vice versa).
    rec = {"measurement_path": "via_omniroute",
           "omniroute": {"router_claim": "x", "calibration": {"passed": True},
                         "cross_check": {"state": "INCONCLUSIVE"}},
           "cross_check": {"state": "CONTRADICTED"}}
    ok, reason = P.is_publishable(rec)
    assert ok is False and "CONTRADICTED" in reason


def test_calibration_passed_must_be_boolean_true():
    # MEDIUM (Codex): a truthy non-bool ("true"/1) must NOT satisfy the gate.
    for truthy in ("true", 1, "yes"):
        rec = _via(calibrated=True)
        rec["omniroute"]["calibration"]["passed"] = truthy
        ok, _ = P.is_publishable(rec)
        assert ok is False


def test_router_provider_alone_is_not_disclosure():
    # MEDIUM (Codex): provider alone isn't routing evidence; need headers/claim.
    rec = {"measurement_path": "via_omniroute", "omniroute": {
        "router_provider": "oc", "calibration": {"passed": True},
        "cross_check": {"state": "CORROBORATED"}}}
    ok, reason = P.is_publishable(rec)
    assert ok is False and "disclosure" in reason.lower()


def test_promote_guard_basis_advisory_evidence():
    # HIGH (Claude): the promote path guards on is_publishable(rec["evidence"]).
    # A first-party model_switch advisory evidence publishes; one carrying a
    # CONTRADICTED cross-check is refused.
    ok, _ = P.is_publishable({"kind": "model_switch", "verdict": {"misrepresentation": True}})
    assert ok is True
    ok, reason = P.is_publishable({"kind": "model_switch", "cross_check": {"state": "CONTRADICTED"}})
    assert ok is False and "CONTRADICTED" in reason


def test_partition_splits_and_reports_reasons():
    recs = [
        ("t1/verdict.json", {"measurement_path": "direct"}),
        ("t2/verdict.json", _via(calibrated=False)),
        ("t3/verdict.json", _via(state="CONTRADICTED")),
        ("t4/verdict.json", _via(calibrated=True)),
    ]
    signable, quarantined = P.partition(recs)
    assert signable == ["t1/verdict.json", "t4/verdict.json"]
    assert {q["path"] for q in quarantined} == {"t2/verdict.json", "t3/verdict.json"}
    assert all(q["reason"] for q in quarantined)          # every quarantine has a reason
