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
# ENGINE_SRC is passed so the family total is read from the engine's real
# tokenizer_ref.json (never hardcoded); REPO is passed so the throwaway venv can
# import the shared, unit-tested summariser (scripts/engine_eval_summary.py).
"$WORK/venv/bin/python" - "$WORK/eval.json" "$ENGINE_SHA" "$OUT" "$ENGINE_SRC" "$REPO" <<'PY'
import json, os, sys
raw_path, sha, out, engine_src, repo = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
sys.path.insert(0, os.path.join(repo, "scripts"))
from engine_eval_summary import summarize
o = json.load(open(raw_path))
summary = summarize(o, sha, engine_src)
json.dump(summary, open(out, "w"), indent=2)
print(json.dumps(summary, indent=2))
PY
echo "ENGINE EVAL REFRESH: ok"
