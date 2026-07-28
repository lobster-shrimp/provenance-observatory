"""Evidence manifest: deterministic root, tamper detection, cosign graceful-skip."""
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import signing  # noqa: E402

D = date.today().isoformat()


def _rec(data, target, payload):
    p = os.path.join(data, target, D)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "verdict.json"), "w") as f:
        json.dump(payload, f)


def test_manifest_lists_records_with_hashes(tmp_path):
    data = str(tmp_path)
    _rec(data, "t1", {"fingerprint_id": "a"})
    _rec(data, "t2", {"fingerprint_id": "b"})
    m = signing.build_manifest(data, D)
    assert set(m["entries"]) == {f"t1/{D}/verdict.json", f"t2/{D}/verdict.json"}
    assert len(m["manifest_root"]) == 64


def test_manifest_includes_agent_records(tmp_path):
    # E5 record-drop: engine `--export` bundles land under data/agents/<target>/<date>/
    # and MUST be picked up + signed by the daily manifest (Codex/Claude E5 seam).
    data = str(tmp_path)
    _rec(data, "endpoint-1", {"fingerprint_id": "a"})
    ap = os.path.join(data, "agents", "acme-copilot", D)
    os.makedirs(ap, exist_ok=True)
    with open(os.path.join(ap, "verdict.json"), "w") as f:
        json.dump({"kind": "agent", "verdict": {"label": "MIXED"}}, f)
    m = signing.build_manifest(data, D)
    assert f"agents/acme-copilot/{D}/verdict.json" in m["entries"]
    assert f"endpoint-1/{D}/verdict.json" in m["entries"]      # endpoint records still included


def test_manifest_root_is_deterministic(tmp_path):
    data = str(tmp_path)
    _rec(data, "t1", {"fingerprint_id": "a"})
    r1 = signing.build_manifest(data, D)["manifest_root"]
    r2 = signing.build_manifest(data, D)["manifest_root"]
    assert r1 == r2


def test_manifest_root_changes_when_a_record_changes(tmp_path):
    data = str(tmp_path)
    _rec(data, "t1", {"fingerprint_id": "a"})
    r1 = signing.build_manifest(data, D)["manifest_root"]
    _rec(data, "t1", {"fingerprint_id": "CHANGED"})
    r2 = signing.build_manifest(data, D)["manifest_root"]
    assert r1 != r2


def test_write_and_integrity_check(tmp_path):
    data = str(tmp_path)
    _rec(data, "t1", {"fingerprint_id": "a"})
    mpath = signing.write_manifest(data, D)
    assert os.path.exists(mpath)
    assert signing.verify_manifest_integrity(data, mpath) == []   # intact
    # tamper with the record after the manifest was written
    _rec(data, "t1", {"fingerprint_id": "tampered"})
    changed = signing.verify_manifest_integrity(data, mpath)
    assert changed == [f"t1/{D}/verdict.json"]


def test_sign_manifest_graceful_without_cosign(tmp_path, monkeypatch):
    data = str(tmp_path)
    _rec(data, "t1", {"fingerprint_id": "a"})
    mpath = signing.write_manifest(data, D)
    monkeypatch.setattr(signing, "have_cosign", lambda: False)
    r = signing.sign_manifest(mpath)
    assert r["signed"] is False and "cosign" in r["reason"]


# --- P2b: the signer quarantines via_omniroute records without calibration ---

def _via_rec(calibrated, state="CORROBORATED"):
    return {"measurement_path": "via_omniroute", "fingerprint_id": "z", "omniroute": {
        "router_headers": {"model": "oc/deepseek-v4"}, "router_claim": "oc/deepseek-v4",
        "calibration": {"passed": calibrated}, "cross_check": {"state": state}}}


def test_uncalibrated_via_omniroute_is_quarantined_not_signed(tmp_path):
    data = str(tmp_path)
    _rec(data, "direct1", {"measurement_path": "direct", "fingerprint_id": "a"})
    _rec(data, "omni_bad", _via_rec(calibrated=False))
    m = signing.build_manifest(data, D)
    assert f"direct1/{D}/verdict.json" in m["entries"]
    assert f"omni_bad/{D}/verdict.json" not in m["entries"]      # NOT signed
    q = {x["path"] for x in m.get("quarantined", [])}
    assert f"omni_bad/{D}/verdict.json" in q


def test_calibrated_disclosed_via_omniroute_is_signed(tmp_path):
    data = str(tmp_path)
    _rec(data, "omni_ok", _via_rec(calibrated=True))
    m = signing.build_manifest(data, D)
    assert f"omni_ok/{D}/verdict.json" in m["entries"]
    assert "quarantined" not in m


def test_contradicted_crosscheck_is_quarantined(tmp_path):
    data = str(tmp_path)
    _rec(data, "omni_mismatch", _via_rec(calibrated=True, state="CONTRADICTED"))
    m = signing.build_manifest(data, D)
    assert m["entries"] == {}                                    # nothing signable
    assert any("CONTRADICTED" in x["reason"] for x in m["quarantined"])


def test_quarantined_records_excluded_from_manifest_root(tmp_path):
    # The signature must not cover a quarantined record: adding one must not
    # change the root computed over the signable set.
    data = str(tmp_path)
    _rec(data, "direct1", {"measurement_path": "direct", "fingerprint_id": "a"})
    root_before = signing.build_manifest(data, D)["manifest_root"]
    _rec(data, "omni_bad", _via_rec(calibrated=False))
    root_after = signing.build_manifest(data, D)["manifest_root"]
    assert root_before == root_after


def test_write_manifest_emits_quarantine_sidecar(tmp_path):
    data = str(tmp_path)
    _rec(data, "omni_bad", _via_rec(calibrated=False))
    signing.write_manifest(data, D)
    qpath = os.path.join(data, "manifests", f"{D}.quarantine.json")
    assert os.path.exists(qpath)
    with open(qpath) as f:
        q = json.load(f)
    assert q["date"] == D and q["quarantined"]


def test_quarantine_sidecar_not_loaded_as_a_manifest(tmp_path):
    # The <date>.quarantine.json sidecar sits next to manifests; load_manifests
    # must NOT pick it up as a bogus manifest row.
    from lib import records
    data = str(tmp_path)
    _rec(data, "direct1", {"measurement_path": "direct", "fingerprint_id": "a"})
    _rec(data, "omni_bad", _via_rec(calibrated=False))
    signing.write_manifest(data, D)
    manifests = records.load_manifests(data)
    assert len(manifests) == 1 and manifests[0].get("manifest_root")


def test_quarantined_records_excluded_from_public_verdict_loaders(tmp_path):
    # HIGH (Codex): the site AND API read through load_target_records; a
    # quarantined record must NOT surface there as a public verdict.
    from lib import records
    data = str(tmp_path)
    _rec(data, "direct1", {"measurement_path": "direct", "fingerprint_id": "a"})
    _rec(data, "omni_bad", _via_rec(calibrated=False))
    _rec(data, "omni_mismatch", _via_rec(calibrated=True, state="CONTRADICTED"))
    loaded = records.load_target_records(data)
    assert "direct1" in loaded
    assert "omni_bad" not in loaded         # uncalibrated proxy — not a public verdict
    assert "omni_mismatch" not in loaded    # CONTRADICTED — quarantined


def _transcript(data, target, payload):
    p = os.path.join(data, target, D)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "transcript.json"), "w") as f:
        json.dump(payload, f)


def test_transcripts_are_signed_and_policy_gated(tmp_path):
    # CRITICAL (Claude): transcript.json was neither signed nor policy-gated.
    data = str(tmp_path)
    _transcript(data, "firstparty", {"verdict": {"misrepresentation": True}})   # legit, signed
    _transcript(data, "sneaky", _via_rec(calibrated=False))                     # proxy, quarantined
    m = signing.build_manifest(data, D)
    assert f"firstparty/{D}/transcript.json" in m["entries"]
    assert f"sneaky/{D}/transcript.json" not in m["entries"]
    assert any("sneaky" in q["path"] for q in m.get("quarantined", []))
