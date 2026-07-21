#!/usr/bin/env python3
"""Drift → advisory pipeline (design decisions T5 / T9 / publication gate).

Two-tier publication:
  - Drift opens/updates a DRAFT advisory in a PRIVATE staging repo (never the
    public repo). The vendor is notified at detection time.
  - A maintainer promotes the interpreted verdict + advisory to the public feed
    only after the DISCLOSURE.md window AND target `public: true`.
  - Advisory numbers (MPA-YYYY-NNN) are assigned AT PROMOTION, so unpromoted
    drafts leave no gaps in the public sequence.

Dedup: one open advisory per target per unresolved change; subsequent drift
appends to the open advisory rather than opening a new one.

Baseline advance is delegated to lib.baseline — importantly, an UNSTABLE-
triggered close does NOT advance the baseline.

SKELETON: the staging-repo I/O, vendor notification, and promotion command are
the real build work (marked TODO). Credentials note: the workflow's default
GITHUB_TOKEN cannot write the private staging repo — a least-privilege
fine-grained PAT (staging repo only, Contents + Issues) is required.
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import baseline  # noqa: E402


def open_or_update_draft(target_name: str, evidence: dict, state) -> None:
    """On drift: create a draft advisory in staging, or append if one is open."""
    raise NotImplementedError(
        "TODO: write draft to PRIVATE staging repo; notify vendor; dedup on "
        "open advisory per target; use least-privilege PAT.")


def promote(advisory_staging_id: str) -> str:
    """Maintainer action: after the DISCLOSURE.md window, assign MPA-YYYY-NNN
    and publish the interpreted verdict + advisory to the public feed."""
    raise NotImplementedError(
        "TODO: assign advisory number at promotion; publish to public Issues + "
        "RSS/JSON; only if target public=true and window elapsed.")


__all__ = ["open_or_update_draft", "promote", "baseline"]
