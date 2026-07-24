#!/usr/bin/env bash
# Regenerate data/engine_eval.json — the engine's accuracy/consistency eval
# summary that the site's assurance panel renders. Run nightly so the public
# badge tracks the engine automatically instead of a hand-committed snapshot.
#
# Self-contained: builds a throwaway venv, gets the engine source (local
# PROVENANCE_PROBE_SRC if given, else a shallow clone — the eval harness and its
# vendored GGUF vocabs are NOT on PyPI), runs the hermetic eval, and writes a
# compact summary. Safe to run locally or in CI.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
OUT="$REPO/data/engine_eval.json"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "[1/3] engine source"
if [ -d "${PROVENANCE_PROBE_SRC:-}" ]; then
  ENGINE_SRC="$PROVENANCE_PROBE_SRC"
else
  git clone --depth 1 https://github.com/lobster-shrimp/provenance-probe.git "$WORK/engine" >/dev/null 2>&1
  ENGINE_SRC="$WORK/engine"
fi
ENGINE_SHA="$(git -C "$ENGINE_SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)"
[ -f "$ENGINE_SRC/eval/run_eval.py" ] || { echo "ERROR: engine eval not found at $ENGINE_SRC/eval"; exit 2; }

echo "[2/3] venv + run hermetic eval"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" -q install -e "$ENGINE_SRC[eval]" >/dev/null
# Run from the engine checkout so `python -m eval.run_eval` resolves eval/ + vocabs.
( cd "$ENGINE_SRC" && "$WORK/venv/bin/python" -m eval.run_eval --json ) > "$WORK/eval.json"

echo "[3/3] summarize -> $OUT"
"$WORK/venv/bin/python" - "$WORK/eval.json" "$ENGINE_SHA" "$OUT" <<'PY'
import json, sys, datetime
raw, sha, out = sys.argv[1], sys.argv[2], sys.argv[3]
o = json.load(open(raw))
m = o["matrix"]
families = sum(1 for c in o["cases"] if "(" in c["name"])   # vocab-tier cases carry an origin annotation
den = m["TN"] + m["FP"]
summary = {
    "passed": o["passed"],
    "matrix": {k: m[k] for k in ("TP", "FP", "TN", "FN", "ERR")},
    "false_positive_rate": (m["FP"] / den) if den else 0.0,
    "vocab_families_exercised": families,
    "reference_families_total": 25,
    "generated": datetime.date.today().isoformat(),
    "engine_commit": sha,
    "source": "provenance-probe eval/run_eval.py (hermetic consistency+accuracy gate)",
    "note": ("Consistency/regression gate over open-weights GGUF vocabs + scoring "
             "bundles. NOT a live-endpoint accuracy claim; real named-vendor "
             "accuracy is validated privately."),
}
json.dump(summary, open(out, "w"), indent=2)
print(json.dumps(summary, indent=2))
PY
echo "ENGINE EVAL REFRESH: ok"
