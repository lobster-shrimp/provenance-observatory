"""Increment 2: refresh + sign + publish the LLM-API catalog.

The probe is consumed as a black-box CLI (T7), so `build-catalog` is mocked at the
subprocess boundary; signing degrades to a no-op without cosign. Unlike the
registry, the catalog tracks a NON-deterministic upstream (models.dev), so the gate
is a fail-closed well-formed+non-empty check, not a corpus drift check.
"""
import json
import os
import sys
import types

from fastapi.testclient import TestClient

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
import build_catalog  # noqa: E402  (runner/ on path, as run.py imports it)
from api import app as app_module  # noqa: E402
from api.store import Store  # noqa: E402

_GOOD = {
    "catalog_version": "1", "provider_count": 1, "model_count": 1,
    "providers": [{"provider_id": "deepseek", "name": "DeepSeek",
                   "api_url": "https://api.deepseek.com/v1", "api_host": "api.deepseek.com",
                   "provenance": {"jurisdiction": "PRC", "kind": "prc", "measured": False},
                   "models": [{"id": "deepseek-chat", "context": 128000}]}],
}


def _fake_cli(*, build_rc=0, build_stderr="", content=None):
    """A fake `provenance-probe` CLI: build-catalog writes `content` to --out."""
    content = content if content is not None else _GOOD

    def run(cmd, **kw):
        if cmd[1] == "build-catalog":
            if build_rc == 0:
                out = cmd[cmd.index("--out") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(content, fh)
            return types.SimpleNamespace(returncode=build_rc, stdout="", stderr=build_stderr)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _publish(data_dir, doc, *, signed):
    d = os.path.join(data_dir, "catalog")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    if signed:
        open(path + ".cosign.bundle", "w").close()
    return path


def test_build_signed_catalog_happy_path(tmp_path):
    res = build_catalog.build_signed_catalog(str(tmp_path), run=_fake_cli())
    assert res["built"] is True and res["verified"] is True
    assert os.path.exists(res["path"])
    assert "signed" in res


def test_unavailable_on_old_probe_degrades(tmp_path):
    run = _fake_cli(build_rc=2, build_stderr="error: argument cmd: invalid choice: 'build-catalog'")
    res = build_catalog.build_signed_catalog(str(tmp_path), run=run)
    assert res["built"] is False and res["signed"] is False
    assert "unavailable" in res["reason"]


def test_build_failure_models_dev_unreachable(tmp_path):
    run = _fake_cli(build_rc=2, build_stderr="[build-catalog] could not obtain models.dev data: timeout")
    res = build_catalog.build_signed_catalog(str(tmp_path), run=run)
    assert res["built"] is False and res["signed"] is False
    assert "failed" in res["reason"]


def test_binary_missing_degrades(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("provenance-probe")
    res = build_catalog.build_signed_catalog(str(tmp_path), run=run)
    assert res["built"] is False and "could not run" in res["reason"]


def test_empty_catalog_rejected_and_prior_untouched(tmp_path):
    # fail-closed: a build that yields an empty/garbage catalog must NOT be published.
    data = str(tmp_path)
    good = dict(_GOOD, model_count=99)
    path = _publish(data, good, signed=True)                 # night N: a real catalog
    empty = {"catalog_version": "1", "provider_count": 0, "model_count": 0, "providers": []}
    res = build_catalog.build_signed_catalog(data, run=_fake_cli(content=empty))
    assert res["verified"] is False and res["signed"] is False
    assert json.load(open(path)) == good                     # published copy UNTOUCHED
    assert os.path.exists(path + ".cosign.bundle")           # its signature intact


def test_malformed_json_rejected(tmp_path):
    # build-catalog "succeeded" (rc 0) but wrote junk -> not published.
    def run(cmd, **kw):
        if cmd[1] == "build-catalog":
            with open(cmd[cmd.index("--out") + 1], "w") as fh:
                fh.write("{not json")
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    res = build_catalog.build_signed_catalog(str(tmp_path), run=run)
    assert res["verified"] is False and "not valid JSON" in res["reason"]


def test_unchanged_run_keeps_committed_signature(tmp_path):
    data = str(tmp_path)
    path = _publish(data, _GOOD, signed=True)
    res = build_catalog.build_signed_catalog(data, run=_fake_cli(content=_GOOD))
    assert res.get("unchanged") is True and res["signed"] is True
    assert os.path.exists(path + ".cosign.bundle")


def test_changed_but_unsigned_drops_stale_bundle(tmp_path):
    data = str(tmp_path)
    path = _publish(data, dict(_GOOD, model_count=1), signed=True)
    changed = dict(_GOOD, model_count=42)                    # models.dev moved
    res = build_catalog.build_signed_catalog(data, run=_fake_cli(content=changed))
    assert res["verified"] is True and res["signed"] is False   # no cosign in test env
    assert json.load(open(path))["model_count"] == 42           # promoted
    assert not os.path.exists(path + ".cosign.bundle")          # stale signature removed


# --- store + API ------------------------------------------------------------- #

def _write_catalog(data_dir, *, signed=False):
    d = os.path.join(data_dir, "catalog")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(_GOOD, fh)
    if signed:
        open(path + ".cosign.bundle", "w").close()


def test_store_catalog_reads_and_flags_signed(tmp_path):
    data = str(tmp_path / "data")
    assert Store(data).catalog() is None
    _write_catalog(data, signed=True)
    doc = Store(data).catalog()
    assert doc["model_count"] == 1 and doc["signed"] is True


def test_store_catalog_returns_none_on_non_catalog(tmp_path):
    data = str(tmp_path / "data")
    d = os.path.join(data, "catalog")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "catalog.json"), "w") as fh:
        fh.write("5")                                        # valid JSON, not a catalog object
    assert Store(data).catalog() is None


def test_api_catalog_route(tmp_path, monkeypatch):
    data = str(tmp_path / "data")
    monkeypatch.setattr(app_module, "store", Store(data))
    client = TestClient(app_module.app)
    assert client.get("/api/catalog").status_code == 404     # not published yet
    _write_catalog(data, signed=False)
    monkeypatch.setattr(app_module, "store", Store(data))
    r = client.get("/api/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["model_count"] == 1 and body["signed"] is False
