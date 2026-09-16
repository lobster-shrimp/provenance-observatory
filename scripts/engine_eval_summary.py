"""Pure summariser: engine hermetic-eval JSON -> the compact snapshot the site's
assurance panel renders (data/engine_eval.json).

Kept importable (not inlined in refresh-engine-eval.sh) so the family-count logic
is unit-tested and can never regress to the old "N of M" bug where the numerator
counted vocab *cases* (not distinct families) and the denominator was hardcoded
to 25 while the engine shipped 27 references — a transparency bug that could
print more exercised families than the reference set even contains.
"""
from __future__ import annotations

import datetime
import json
import os


def distinct_vocab_families(cases: list[dict]) -> int:
    """Number of DISTINCT family names among exercised VOCAB consistency cases.

    Counts distinct families, NOT raw case count (the old "35 of 25" bug counted
    every parenthesised case). Two eval tiers both carry a parenthetical, so a
    naive `"(" in name` over-counts:

      * vocab consistency cases annotate an origin->family mapping, e.g.
        "qwen2 (CN->Qwen2/Qwen2.5 1.0)" — the family is the token before " (";
      * network/accuracy cases carry a plain description with no arrow, e.g.
        "api.deepseek.com (DeepSeek direct)" — a hostname, NOT a vocab family.

    Only the origin->family (arrow-bearing) cases are vocab families, so those
    are the only ones counted. Scoring-only bundles (no parenthetical) are also
    ignored. This keeps the count honest and <= the reference total.
    """
    families: set[str] = set()
    for c in (cases or []):
        if not isinstance(c, dict):
            continue
        name = c.get("name") or ""
        i = name.find(" (")
        if i < 0:
            continue
        if "->" not in name[i + 2:]:      # network/accuracy case, not a vocab family
            continue
        families.add(name[:i].strip())
    return len(families)


def reference_families_total(engine_src: str, fallback: int) -> int:
    """The REAL number of reference families the engine ships, read from
    provenance_probe/data/tokenizer_ref.json (currently 27) — never hardcoded.

    Falls back to `fallback` (the exercised count) if the file can't be read, so
    the summary never claims more families than it can prove. Auto-tracks future
    families as the reference set grows.
    """
    try:
        ref = os.path.join(engine_src, "provenance_probe", "data", "tokenizer_ref.json")
        with open(ref) as f:
            models = json.load(f).get("models") or []
        n = len(models)
        return n if n > 0 else fallback
    except (OSError, ValueError, TypeError):
        return fallback


def summarize(raw: dict, engine_sha: str, engine_src: str,
              today: str | None = None) -> dict:
    """Build the compact engine-eval snapshot from the raw eval JSON.

    Consistency guarantee: `vocab_families_exercised` <= `reference_families_total`
    always holds (the total is clamped up to the exercised count), so the site can
    never again render "N of M" with N > M.
    """
    m = raw["matrix"]
    exercised = distinct_vocab_families(raw.get("cases", []))
    total = reference_families_total(engine_src, exercised)
    # Clamp: the two numbers are derived consistently — total is never < exercised.
    total = max(total, exercised)
    den = m["TN"] + m["FP"]
    return {
        "passed": raw["passed"],
        "matrix": {k: m[k] for k in ("TP", "FP", "TN", "FN", "ERR")},
        "false_positive_rate": (m["FP"] / den) if den else 0.0,
        "vocab_families_exercised": exercised,
        "reference_families_total": total,
        "generated": today or datetime.date.today().isoformat(),
        "engine_commit": engine_sha,
        "source": "provenance-probe eval/run_eval.py (hermetic consistency+accuracy gate)",
        "note": ("Consistency/regression gate over open-weights GGUF vocabs + scoring "
                 "bundles. NOT a live-endpoint accuracy claim; real named-vendor "
                 "accuracy is validated privately."),
    }
