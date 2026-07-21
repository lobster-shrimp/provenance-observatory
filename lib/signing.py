"""Evidence signing — makes the Certificate-Transparency claim honest.

git history alone is rewritable by whoever holds push access, so "tamper-evident
against a third party" needs a real signature over an anchored log. Each day we
build a MANIFEST — every committed record path plus its SHA-256, and a single
manifest_root hash over them — and sign the manifest with cosign keyless
(Fulcio cert + Rekor transparency-log inclusion). Anyone can later verify a
record hasn't changed and that the manifest was signed at the time claimed.

This module has two layers:
  - manifest building + hashing: pure, deterministic, fully testable here.
  - cosign wrapper: thin shell-out. cosign keyless needs an OIDC identity, so
    real signing happens in CI (the workflow has id-token: write). Locally,
    sign_manifest degrades gracefully when cosign is absent.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess

SCHEMA_VERSION = "0.1.0"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(data_dir: str, date_str: str) -> dict:
    """Manifest of every record committed for `date_str`, with a root hash.

    Entries are keyed by path relative to data_dir (stable across machines).
    manifest_root = sha256 over the canonical "path  hash" lines, sorted — so
    any change to any record, or any added/removed record, changes the root.
    """
    entries: dict[str, str] = {}
    for name in ("verdict.json", "no-verdict.json"):
        for p in _iter_records(data_dir, date_str, name):
            rel = os.path.relpath(p, data_dir)
            entries[rel] = _sha256_file(p)
    canonical = "\n".join(f"{k}  {entries[k]}" for k in sorted(entries))
    root = hashlib.sha256(canonical.encode()).hexdigest()
    return {"schema_version": SCHEMA_VERSION, "date": date_str,
            "entries": dict(sorted(entries.items())), "manifest_root": root}


def _iter_records(data_dir: str, date_str: str, filename: str):
    import glob
    yield from glob.glob(os.path.join(data_dir, "*", date_str, filename))


def write_manifest(data_dir: str, date_str: str) -> str:
    manifest = build_manifest(data_dir, date_str)
    out_dir = os.path.join(data_dir, "manifests")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{date_str}.json")
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
    return path


def have_cosign() -> bool:
    return shutil.which("cosign") is not None


def sign_manifest(manifest_path: str) -> dict:
    """Sign the manifest with cosign keyless (Fulcio + Rekor). Writes a
    <manifest>.cosign.bundle next to it. Returns {signed, bundle, reason}.

    Keyless signing needs an OIDC token (present in CI via id-token: write); it
    is a no-op with a clear reason when cosign is absent or no identity exists.
    """
    if not have_cosign():
        return {"signed": False, "bundle": None,
                "reason": "cosign not installed (signing runs in CI)"}
    bundle = manifest_path + ".cosign.bundle"
    r = subprocess.run(
        ["cosign", "sign-blob", "--yes", manifest_path, "--bundle", bundle],
        capture_output=True, text=True)
    if r.returncode != 0:
        return {"signed": False, "bundle": None,
                "reason": f"cosign sign-blob failed: {r.stderr.strip()[:200]}"}
    return {"signed": True, "bundle": bundle, "reason": "ok"}


def verify_manifest(manifest_path: str, bundle_path: str,
                    identity: str, issuer: str) -> bool:
    """Verify a signed manifest. identity/issuer pin who signed it (e.g. the
    GitHub Actions workflow OIDC identity)."""
    if not have_cosign():
        raise RuntimeError("cosign not installed")
    r = subprocess.run(
        ["cosign", "verify-blob", "--bundle", bundle_path,
         "--certificate-identity", identity,
         "--certificate-oidc-issuer", issuer, manifest_path],
        capture_output=True, text=True)
    return r.returncode == 0


def verify_manifest_integrity(data_dir: str, manifest_path: str) -> list[str]:
    """Recompute hashes and return the list of records that no longer match the
    manifest (empty = intact). Signature-independent tamper check."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    changed = []
    for rel, want in manifest.get("entries", {}).items():
        p = os.path.join(data_dir, rel)
        if not os.path.exists(p) or _sha256_file(p) != want:
            changed.append(rel)
    return changed
