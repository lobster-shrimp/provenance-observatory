"""Increment 2: regenerate + sign + publish the provider-attribution registry.

The probe is consumed as a black-box CLI (T7), so the build/verify steps are
mocked at the subprocess boundary; signing degrades to a no-op without cosign.
"""
import json
import os
import sys
import types

from fastapi.testclient import TestClient

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
import build_registry  # noqa: E402  (runner/ on path, as run.py imports it)
from api import app as app_module  # noqa: E402
from api.store import Store  # noqa: E402


def _fake_cli(*, build_rc=0, verify_rc=0, build_stderr="", verify_stderr="",
              content=None):
    """A fake `provenance-probe` CLI: build-registry writes `content` (a registry
    dict) to --out, verify-registry returns a code. Returns a callable for `run=`."""
    content = content if content is not None else {
        "registry_version": "1", "entries": [{"domain": "api.deepseek.com"}]}

    def run(cmd, **kw):
        sub = cmd[1]
        if sub == "build-registry":
            if build_rc == 0:
                out = cmd[cmd.index("--out") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(content, fh)
            return types.SimpleNamespace(returncode=build_rc, stdout="", stderr=build_stderr)
        if sub == "verify-registry":
            return types.SimpleNamespace(returncode=verify_rc, stdout="OK", stderr=verify_stderr)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _publish(data_dir, doc, *, signed):
    """Simulate a prior committed registry (+ optional signature)."""
    d = os.path.join(data_dir, "registry")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "registry.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    if signed:
        open(path + ".cosign.bundle", "w").close()
    return path


def test_build_signed_registry_happy_path(tmp_path):
    res = build_registry.build_signed_registry(str(tmp_path), run=_fake_cli())
    assert res["built"] is True and res["verified"] is True
    assert os.path.exists(res["path"])                       # registry written into data/registry/
    assert "signed" in res                                   # cosign may be absent -> False, but present


def test_build_registry_unavailable_on_old_probe_degrades(tmp_path):
    # argparse rejects the unknown subcommand on a probe < 0.27.0
    run = _fake_cli(build_rc=2, build_stderr="error: argument cmd: invalid choice: 'build-registry'")
    res = build_registry.build_signed_registry(str(tmp_path), run=run)
    assert res["built"] is False and res["signed"] is False
    assert "unavailable" in res["reason"]                    # clean no-op, not a hard failure


def test_verify_drift_blocks_signing(tmp_path):
    run = _fake_cli(verify_rc=1, verify_stderr="registry verification FAILED")
    res = build_registry.build_signed_registry(str(tmp_path), run=run)
    assert res["built"] is True and res["verified"] is False and res["signed"] is False


def test_build_registry_binary_missing_degrades(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("provenance-probe")
    res = build_registry.build_signed_registry(str(tmp_path), run=run)
    assert res["built"] is False and "could not run" in res["reason"]


def test_verify_failure_never_overwrites_or_falsely_signs_the_published_copy(tmp_path):
    # HIGH regression: a drifted build must NOT reach the served path, and must not
    # leave a prior bundle presenting the new content as signed.
    data = str(tmp_path)
    old = {"registry_version": "1", "entries": [{"domain": "OLD"}]}
    path = _publish(data, old, signed=True)                 # night N: published + signed
    drift = {"registry_version": "1", "entries": [{"domain": "DRIFTED"}]}
    res = build_registry.build_signed_registry(
        data, run=_fake_cli(verify_rc=1, verify_stderr="FAILED", content=drift))
    assert res["verified"] is False and res["signed"] is False
    assert json.load(open(path)) == old                     # published copy UNTOUCHED (not drifted)
    assert os.path.exists(path + ".cosign.bundle")          # night-N signature (matches OLD) intact


def test_changed_but_unsigned_run_drops_the_stale_bundle(tmp_path):
    # content changed and this run couldn't sign (no cosign) -> promote the new
    # registry but REMOVE the old bundle so nothing reads as signed over new bytes.
    data = str(tmp_path)
    old = {"registry_version": "1", "entries": [{"domain": "OLD"}]}
    path = _publish(data, old, signed=True)
    new = {"registry_version": "1", "entries": [{"domain": "NEW"}]}
    res = build_registry.build_signed_registry(data, run=_fake_cli(content=new))
    assert res["verified"] is True and res["signed"] is False
    assert json.load(open(path)) == new                     # promoted
    assert not os.path.exists(path + ".cosign.bundle")      # stale signature removed


def test_unchanged_deterministic_run_keeps_the_committed_signature(tmp_path):
    data = str(tmp_path)
    same = {"registry_version": "1", "entries": [{"domain": "api.deepseek.com"}]}
    path = _publish(data, same, signed=True)
    res = build_registry.build_signed_registry(data, run=_fake_cli(content=same))
    assert res.get("unchanged") is True and res["signed"] is True
    assert os.path.exists(path + ".cosign.bundle")          # untouched valid signature


# --- store + API ------------------------------------------------------------- #

def _write_registry(data_dir, *, signed=False):
    d = os.path.join(data_dir, "registry")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "registry.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"registry_version": "1", "entry_count": 1,
                   "entries": [{"domain": "api.deepseek.com", "jurisdiction": "PRC"}]}, fh)
    if signed:
        open(path + ".cosign.bundle", "w").close()


def test_store_registry_reads_and_flags_signed(tmp_path):
    data = str(tmp_path / "data")
    assert Store(data).registry() is None                    # nothing published yet
    _write_registry(data, signed=True)
    doc = Store(data).registry()
    assert doc["entry_count"] == 1 and doc["signed"] is True


def test_store_registry_returns_none_on_non_dict_json(tmp_path):
    # MEDIUM: a bare scalar / partial write must degrade to None, not 500 the API.
    data = str(tmp_path / "data")
    d = os.path.join(data, "registry")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "registry.json"), "w") as fh:
        fh.write("5")                                        # valid JSON, not an object
    assert Store(data).registry() is None


def test_api_registry_route(tmp_path, monkeypatch):
    data = str(tmp_path / "data")
    monkeypatch.setattr(app_module, "store", Store(data))
    client = TestClient(app_module.app)
    assert client.get("/api/registry").status_code == 404    # not published yet
    _write_registry(data, signed=False)
    monkeypatch.setattr(app_module, "store", Store(data))
    r = client.get("/api/registry")
    assert r.status_code == 200
    body = r.json()
    assert body["entry_count"] == 1 and body["signed"] is False
