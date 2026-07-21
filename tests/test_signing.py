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
