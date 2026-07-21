#!/usr/bin/env python3
"""Render the public GitHub Pages site from data/ (approved Variant C direction).

Variant C: light Certificate-Transparency / academic register — a stats band, a
dense verdict table (target, aggregator, claimed model, provenance + jurisdiction
badges, confidence, 7-day stability sparkline, evidence link), an advisories
rail, and methodology + disclosure links in the footer. Chosen for citability:
it reads as neutral evidence, not a vendor dashboard.

PUBLICATION RULE: until Gate 1 clears a target (`public: true`), render only its
NEUTRAL evidence — never its interpreted verdict label. lib.verdict.split is the
source of truth for that boundary.

Scaling (U2): read the hot window (daily verdicts, last 90 days); older history
is rolled to weekly summaries. Raw JSON/manifests are never deleted.

SKELETON: the HTML rendering is the real build work.
"""
from __future__ import annotations
import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HOT_WINDOW_DAYS = 90


def build() -> None:
    raise NotImplementedError(
        "TODO: read data/ hot window, render Variant C table + advisories rail; "
        "publish neutral evidence only for targets with public=false.")


if __name__ == "__main__":
    build()
