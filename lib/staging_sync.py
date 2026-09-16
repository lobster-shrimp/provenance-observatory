"""Private staging-repo sync — makes drift detection actually persist.

The observatory's silent-swap detection depends on state that MUST round-trip
between nightly runs:

  - `<target>/baseline.json`  — the raw bundle `provenance-probe monitor` diffs
    the current run against (the pinned baseline).
  - `<target>/state.json`     — `lib.baseline.TargetState`: pinned_baseline,
    recent_fingerprints, and the UNSTABLE damper state.
  - `<target>/advisories/*.json` + `notice-*.txt` — draft advisories.
  - `advisory-counter.json`   — the MPA-YYYY-NNN counter.

On a GitHub-hosted runner `STAGING_DIR` is empty at the start of every job, so
without this module the drift check re-seeds its baseline every night and never
fires. This module clones a PRIVATE git repo into `STAGING_DIR` before the run
and pushes it back after — keeping the gated raw bundles + tokenizer vectors
PRIVATE (never in public `data/`) while making the diff + UNSTABLE machine
persist.

Security (invariant, do not regress):
  - Least-privilege: the PAT is a fine-grained token scoped to the STAGING repo
    only. It is never logged, never echoed, never written to public `data/`, and
    scrubbed from the local `.git/config` remote after clone.
  - No PAT / no repo URL configured → this is a clean no-op and the runner keeps
    its current local-only behavior. Local dev + CI tests need no network.

Consumed strictly via git as a subprocess (no GitHub API import).
"""
from __future__ import annotations
import os
import shutil
import subprocess

# Env contract.
_PAT_ENV = "STAGING_PAT"
_REPO_ENV = "OBSERVATORY_STAGING_REPO"

_BOT_NAME = "observatory-bot"
_BOT_EMAIL = "observatory-bot@users.noreply.github.com"


def is_configured() -> tuple[bool, str]:
    """(enabled, reason). Both STAGING_PAT and OBSERVATORY_STAGING_REPO are
    required — one without the other is a misconfiguration, reported (not a
    silent half-on state)."""
    pat = os.environ.get(_PAT_ENV)
    repo = os.environ.get(_REPO_ENV)
    if not pat and not repo:
        return False, "no STAGING_PAT / OBSERVATORY_STAGING_REPO set"
    if not pat:
        return False, "OBSERVATORY_STAGING_REPO set but STAGING_PAT missing"
    if not repo:
        return False, "STAGING_PAT set but OBSERVATORY_STAGING_REPO missing"
    return True, ""


def _authed_url(repo_url: str, pat: str) -> str:
    """Inject the PAT into an https clone URL. The result is passed only as a
    subprocess arg — never logged (see `_git`/`_scrub`)."""
    if repo_url.startswith("https://"):
        return "https://x-access-token:" + pat + "@" + repo_url[len("https://"):]
    # ssh or other transports: assume the environment already has credentials.
    return repo_url


def _scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if (secret and text) else text


def _redact_arg(arg: str, secret: str | None) -> str:
    if secret and secret in arg:
        return "<redacted-url>"
    if "x-access-token:" in arg or "@github" in arg:
        return "<redacted-url>"
    return arg


def _git(args: list[str], *, cwd: str | None = None, secret: str | None = None) -> subprocess.CompletedProcess:
    """Run git, raising a RuntimeError whose message never contains the PAT.

    subprocess.CalledProcessError would put the full command (incl. the authed
    URL) into the traceback, so we do NOT use check=True — we build a scrubbed
    error ourselves.
    """
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        safe = " ".join(_redact_arg(a, secret) for a in args)
        raise RuntimeError(f"git {safe} failed ({r.returncode}): "
                           f"{_scrub((r.stderr or r.stdout or '').strip()[:200], secret)}")
    return r


def clone_if_configured(staging_dir: str) -> dict:
    """Clone the private staging repo into `staging_dir` when configured.

    Returns {"enabled": bool, "reason": str, "log": str}. When not configured,
    ensures `staging_dir` exists and returns enabled=False (local-only fallback,
    no network).
    """
    enabled, reason = is_configured()
    if not enabled:
        os.makedirs(staging_dir, exist_ok=True)
        return {"enabled": False, "reason": reason,
                "log": f"drift persistence OFF ({reason}); local-only staging at {staging_dir}"}
    repo = os.environ[_REPO_ENV]
    pat = os.environ[_PAT_ENV]
    # The private repo is the source of truth in sync mode — we own this dir, so
    # a stale local copy is replaced by the authoritative remote.
    shutil.rmtree(staging_dir, ignore_errors=True)
    _git(["clone", "--depth", "1", _authed_url(repo, pat), staging_dir], secret=pat)
    # Scrub the token from the persisted remote URL so it never lingers in
    # .git/config (defence in depth; the dir is ephemeral CI state, never public).
    _git(["remote", "set-url", "origin", repo], cwd=staging_dir, secret=pat)
    return {"enabled": True, "reason": "",
            "log": "cloned private staging repo (drift persistence ON)"}


def push_if_configured(staging_dir: str, message: str) -> dict:
    """Commit + push the staging dir back to the private repo when configured.

    Returns {"enabled": bool, "pushed": bool, "log": str}. Commits only when
    something changed. No-op (enabled=False) when not configured.
    """
    enabled, reason = is_configured()
    if not enabled:
        return {"enabled": False, "pushed": False,
                "log": f"drift persistence OFF ({reason}); staging not pushed"}
    repo = os.environ[_REPO_ENV]
    pat = os.environ[_PAT_ENV]
    _git(["add", "-A"], cwd=staging_dir, secret=pat)
    # Nothing staged → nothing to push (diff --cached --quiet exits 0 == clean).
    clean = subprocess.run(["git", "diff", "--cached", "--quiet"],
                           cwd=staging_dir, capture_output=True, text=True)
    if clean.returncode == 0:
        return {"enabled": True, "pushed": False,
                "log": "staging unchanged; nothing to push"}
    _git(["-c", f"user.name={_BOT_NAME}", "-c", f"user.email={_BOT_EMAIL}",
          "commit", "-m", message], cwd=staging_dir, secret=pat)
    # Push with the authed URL inline (origin was scrubbed to a token-less URL).
    _git(["push", _authed_url(repo, pat), "HEAD"], cwd=staging_dir, secret=pat)
    return {"enabled": True, "pushed": True,
            "log": "pushed staging state to private repo"}


__all__ = ["is_configured", "clone_if_configured", "push_if_configured"]
