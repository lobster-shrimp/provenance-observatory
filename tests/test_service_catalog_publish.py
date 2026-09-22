"""Refresh + sign + publish the SERVICE catalog (sibling to test_catalog_publish.py).

The probe is consumed as a black-box CLI (T7), so `build-service-catalog` is mocked at
the subprocess boundary; signing degrades to a no-op without cosign. On a pinned probe
that predates the command the regen is a clean NO-OP and the committed seed is kept —
this is the seed-fallback the nightly relies on until a probe release ships the command.
"""
import json
import os
import sys
import types

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", "runner"))
import build_service_catalog  # noqa: E402  (runner/ on path, as run.py imports it)

_GOOD = {
    "catalog_version": "1", "corpus_version": "2026.07.2", "service_count": 2,
    "services": [
        {"name": "DeepSeek (chat)", "url": "https://chat.deepseek.com",
         "host": "chat.deepseek.com", "kind": "web-app", "operator": "DeepSeek",
         "jurisdiction": "PRC", "fronts": ["DeepSeek"], "evidence": "curated",
         "source": "curated", "confidence": 0.95, "measured": False},
        {"name": "OpenAI ChatGPT", "url": "https://chat.openai.com",
         "host": "chat.openai.com", "kind": "web-app", "operator": "OpenAI",
         "jurisdiction": "first-party", "fronts": ["OpenAI"], "evidence": "curated",
         "source": "curated", "confidence": 0.95, "measured": False},
    ],
}


def _fake_cli(*, build_rc=0, build_stderr="", content=None):
    """A fake `provenance-probe` CLI: build-service-catalog writes `content` to --out."""
    content = content if content is not None else _GOOD

    def run(cmd, **kw):
        if cmd[1] == "build-service-catalog":
            if build_rc == 0:
                out = cmd[cmd.index("--out") + 1]
                with open(out, "w", encoding="utf-8") as fh:
                    json.dump(content, fh)
            return types.SimpleNamespace(returncode=build_rc, stdout="", stderr=build_stderr)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    return run


def _publish(data_dir, doc, *, signed):
    d = os.path.join(data_dir, "service-catalog")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "service-catalog.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
    if signed:
        open(path + ".cosign.bundle", "w").close()
    return path


def test_build_signed_service_catalog_happy_path(tmp_path):
    res = build_service_catalog.build_signed_service_catalog(str(tmp_path), run=_fake_cli())
    assert res["built"] is True and res["verified"] is True
    assert os.path.exists(res["path"])
    assert "signed" in res


def test_unavailable_on_old_probe_keeps_seed(tmp_path):
    # The seed-fallback the nightly depends on: an old probe (no subcommand) is a no-op
    # and the committed seed is left exactly as published.
    data = str(tmp_path)
    path = _publish(data, _GOOD, signed=True)
    run = _fake_cli(build_rc=2,
                    build_stderr="error: argument cmd: invalid choice: 'build-service-catalog'")
    res = build_service_catalog.build_signed_service_catalog(data, run=run)
    assert res["built"] is False and res["signed"] is False
    assert "unavailable" in res["reason"]
    assert json.load(open(path)) == _GOOD                    # seed UNTOUCHED
    assert os.path.exists(path + ".cosign.bundle")           # its signature intact


def test_build_failure_degrades(tmp_path):
    run = _fake_cli(build_rc=2, build_stderr="[build-service-catalog] corpus read failed")
    res = build_service_catalog.build_signed_service_catalog(str(tmp_path), run=run)
    assert res["built"] is False and res["signed"] is False
    assert "failed" in res["reason"]


def test_binary_missing_degrades(tmp_path):
    def run(cmd, **kw):
        raise FileNotFoundError("provenance-probe")
    res = build_service_catalog.build_signed_service_catalog(str(tmp_path), run=run)
    assert res["built"] is False and "could not run" in res["reason"]


def test_empty_catalog_rejected_and_prior_untouched(tmp_path):
    data = str(tmp_path)
    good = dict(_GOOD, service_count=99)
    path = _publish(data, good, signed=True)
    empty = {"catalog_version": "1", "service_count": 0, "services": []}
    res = build_service_catalog.build_signed_service_catalog(data, run=_fake_cli(content=empty))
    assert res["verified"] is False and res["signed"] is False
    assert json.load(open(path)) == good                     # published copy UNTOUCHED
    assert os.path.exists(path + ".cosign.bundle")           # signature intact


def test_measured_true_row_rejected(tmp_path):
    # INVARIANT: every row must be a static pointer (measured:false). A row asserting
    # measured:true is not a static attribution pointer and must be rejected.
    data = str(tmp_path)
    bad = {"catalog_version": "1", "service_count": 1,
           "services": [dict(_GOOD["services"][0], measured=True)]}
    res = build_service_catalog.build_signed_service_catalog(data, run=_fake_cli(content=bad))
    assert res["verified"] is False and res["signed"] is False
    assert "invariant" in res["reason"]


def test_unchanged_keeps_signature(tmp_path):
    data = str(tmp_path)
    _publish(data, _GOOD, signed=True)
    res = build_service_catalog.build_signed_service_catalog(data, run=_fake_cli(content=_GOOD))
    assert res.get("unchanged") is True
