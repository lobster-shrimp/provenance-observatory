#!/usr/bin/env bash
# Controls self-test = the automated Gate-2 false-positive check.
#
# Stands up two REAL-tokenizer control endpoints (genuine GGUF token counts,
# no HF account needed), runs the collector against them, and asserts:
#   - positive control (Qwen2)   is identified as Qwen2/Qwen2.5  (true positive)
#   - negative control (Llama-3) is NOT flagged Chinese-origin    (no false positive)
# Exits non-zero on any false positive. This is the FP baseline the observatory
# must pass before any real-vendor verdict is published (Gate 2).
#
# Requires: python3, pip. Installs into a throwaway venv under /tmp.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
WORK="$(mktemp -d)"
VOCABS="$WORK/vocabs"; mkdir -p "$VOCABS"
BASE=https://raw.githubusercontent.com/ggml-org/llama.cpp/master/models

echo "[1/5] venv + deps"
# provenance-probe is not on PyPI yet. Install from source: set
# PROVENANCE_PROBE_SRC to a local checkout, else pull from GitHub.
PP_SRC="${PROVENANCE_PROBE_SRC:-git+https://github.com/lobster-shrimp/provenance-probe.git}"
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" -q install "$PP_SRC" gguf tokenizers pyyaml flask >/dev/null

echo "[2/5] fetch real vocabs"
curl -fsS -o "$VOCABS/qwen2.gguf"     "$BASE/ggml-vocab-qwen2.gguf"
curl -fsS -o "$VOCABS/llama-bpe.gguf" "$BASE/ggml-vocab-llama-bpe.gguf"

echo "[3/5] start control endpoints"
"$WORK/venv/bin/python" "$HERE/real_control_mock.py" "$VOCABS/qwen2.gguf"     8902 "northstar-secure-1" >/dev/null 2>&1 &
POS=$!; "$WORK/venv/bin/python" "$HERE/real_control_mock.py" "$VOCABS/llama-bpe.gguf" 8903 "acme-west-safe-1"  >/dev/null 2>&1 &
NEG=$!
trap 'kill $POS $NEG 2>/dev/null || true; rm -rf "$WORK"' EXIT
sleep 3

echo "[4/5] collect"
cat > "$WORK/controls.yaml" <<YAML
defaults: {layers: [tokenizer, wire, latency], behavioral: false, per_run_probe_cap: 200}
targets:
  - {name: control-qwen-known-answer, kind: control-positive, base_url: "http://127.0.0.1:8902/v1", model: "northstar-secure-1", api_style: openai, expect_family: "Qwen2/Qwen2.5", public: true, authorized: true}
  - {name: control-western-negative, kind: control-negative, base_url: "http://127.0.0.1:8903/v1", model: "acme-west-safe-1", api_style: openai, expect_not_origin: "CN", public: true, authorized: true}
budget: {monthly_ceiling_usd: 50}
YAML
export OBSERVATORY_DATA_DIR="$WORK/data" OBSERVATORY_STAGING_DIR="$WORK/staging"
"$WORK/venv/bin/python" "$REPO/runner/run.py" --targets "$WORK/controls.yaml"

echo "[5/5] assert 0 false positives"
"$WORK/venv/bin/python" - "$WORK/data" <<'PY'
import json, glob, sys
data = sys.argv[1]
fp = 0
for p in glob.glob(f"{data}/*/*/verdict.json"):
    c = json.load(open(p)).get("control_check")
    if not c: continue
    print(f"  {c['kind']:<18} top={c.get('top_model')} score={c.get('top_score')} "
          f"origin={c.get('top_origin')} -> {'PASS' if c['pass'] else 'FAIL'}")
    fp += (not c["pass"])
print(f"false positives: {fp}")
sys.exit(1 if fp else 0)
PY
echo "CONTROLS SELF-TEST: PASS (0 false positives)"
