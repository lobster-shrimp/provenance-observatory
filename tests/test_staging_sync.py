"""Private staging-repo sync: clone/push round-trip with MOCKED git (no network),
local-only fallback, and the no-token-leak invariant."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import staging_sync  # noqa: E402


class FakeGit:
    """Records every git invocation and returns a scripted CompletedProcess."""
    def __init__(self, returncodes=None):
        self.calls = []
        self.returncodes = returncodes or {}

    def __call__(self, cmd, cwd=None, capture_output=True, text=True):
        import subprocess as _sp
        self.calls.append({"cmd": cmd, "cwd": cwd})
        # key on the git subcommand (cmd[0] == "git")
        sub = cmd[1] if len(cmd) > 1 else ""
        rc = self.returncodes.get(sub, 0)
        return _sp.CompletedProcess(cmd, rc, stdout="", stderr="")


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("STAGING_PAT", "ghp_SECRETTOKEN")
    monkeypatch.setenv("OBSERVATORY_STAGING_REPO", "https://github.com/org/private-staging.git")
    return monkeypatch


# --- configuration gate -----------------------------------------------------

def test_not_configured_when_both_missing(monkeypatch):
    monkeypatch.delenv("STAGING_PAT", raising=False)
    monkeypatch.delenv("OBSERVATORY_STAGING_REPO", raising=False)
    ok, reason = staging_sync.is_configured()
    assert not ok and "no STAGING_PAT" in reason


def test_not_configured_when_pat_missing(monkeypatch):
    monkeypatch.delenv("STAGING_PAT", raising=False)
    monkeypatch.setenv("OBSERVATORY_STAGING_REPO", "https://github.com/org/x.git")
    ok, reason = staging_sync.is_configured()
    assert not ok and "STAGING_PAT missing" in reason


# --- local-only fallback (no network) ---------------------------------------

def test_clone_fallback_no_network(monkeypatch, tmp_path):
    monkeypatch.delenv("STAGING_PAT", raising=False)
    monkeypatch.delenv("OBSERVATORY_STAGING_REPO", raising=False)
    # If git were called, this would explode — proves the fallback runs no git.
    monkeypatch.setattr(staging_sync, "subprocess",
                        type("X", (), {"run": staticmethod(
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("git called")))}))
    sdir = str(tmp_path / "staging")
    res = staging_sync.clone_if_configured(sdir)
    assert res["enabled"] is False
    assert os.path.isdir(sdir)          # dir created for local-only use
    assert "drift persistence OFF" in res["log"]


def test_push_fallback_is_noop(monkeypatch, tmp_path):
    monkeypatch.delenv("STAGING_PAT", raising=False)
    monkeypatch.delenv("OBSERVATORY_STAGING_REPO", raising=False)
    res = staging_sync.push_if_configured(str(tmp_path), "msg")
    assert res["enabled"] is False and res["pushed"] is False


# --- clone/push round-trip (mocked git) -------------------------------------

def test_clone_runs_git_and_scrubs_token(env, tmp_path, monkeypatch):
    fake = FakeGit()
    monkeypatch.setattr(staging_sync.subprocess, "run", fake)
    sdir = str(tmp_path / "staging")
    res = staging_sync.clone_if_configured(sdir)
    assert res["enabled"] is True
    subs = [c["cmd"][1] for c in fake.calls]
    assert subs == ["clone", "remote"]      # clone, then scrub remote url
    # clone URL carries the token...
    clone_cmd = fake.calls[0]["cmd"]
    assert any("x-access-token:ghp_SECRETTOKEN@" in a for a in clone_cmd)
    # ...but the remote is reset to the TOKEN-LESS url (defence in depth)
    set_url_cmd = fake.calls[1]["cmd"]
    assert set_url_cmd[-1] == "https://github.com/org/private-staging.git"
    assert not any("ghp_SECRETTOKEN" in a for a in set_url_cmd)


def test_push_commits_and_pushes_when_dirty(env, tmp_path, monkeypatch):
    # diff --cached --quiet returns 1 (changes present) -> commit+push happen.
    fake = FakeGit(returncodes={"diff": 1})
    monkeypatch.setattr(staging_sync.subprocess, "run", fake)
    res = staging_sync.push_if_configured(str(tmp_path), "staging: nightly 2026-09-16")
    assert res["pushed"] is True
    verbs = [a for c in fake.calls for a in c["cmd"] if a in ("add", "diff", "commit", "push")]
    assert verbs == ["add", "diff", "commit", "push"]
    push_cmd = fake.calls[-1]["cmd"]
    assert any("x-access-token:ghp_SECRETTOKEN@" in a for a in push_cmd)  # authed inline


def test_push_noop_when_clean(env, tmp_path, monkeypatch):
    # diff --cached --quiet returns 0 (clean) -> no commit, no push.
    fake = FakeGit(returncodes={"diff": 0})
    monkeypatch.setattr(staging_sync.subprocess, "run", fake)
    res = staging_sync.push_if_configured(str(tmp_path), "msg")
    assert res["pushed"] is False
    subs = [c["cmd"][1] for c in fake.calls]
    assert "commit" not in subs and "push" not in subs


# --- security: the PAT never appears in a raised error ----------------------

def test_git_error_never_leaks_token(env, tmp_path, monkeypatch):
    fake = FakeGit(returncodes={"clone": 128})   # clone fails
    monkeypatch.setattr(staging_sync.subprocess, "run", fake)
    with pytest.raises(RuntimeError) as ei:
        staging_sync.clone_if_configured(str(tmp_path / "s"))
    assert "ghp_SECRETTOKEN" not in str(ei.value)
    assert "<redacted-url>" in str(ei.value)
