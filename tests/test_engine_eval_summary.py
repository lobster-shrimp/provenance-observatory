"""engine_eval_summary: the family-count logic behind data/engine_eval.json.

Guards the "35 of 25" transparency bug — the numerator counted every
parenthesised eval *case* (not distinct vocab families) while the denominator was
hardcoded to 25 though the engine ships 27. The two numbers must be derived
consistently and the summary must never report N (exercised) > M (total).
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import engine_eval_summary as ees  # noqa: E402


def _raw(names, *, fp=0):
    return {
        "passed": True,
        "matrix": {"TP": 3, "FP": fp, "TN": 4, "FN": 1, "ERR": 0},
        "cases": [{"name": n} for n in names],
    }


def _engine_with_refs(tmp_path, n):
    d = tmp_path / "engine" / "provenance_probe" / "data"
    d.mkdir(parents=True)
    (d / "tokenizer_ref.json").write_text(
        json.dumps({"models": [{"name": f"m{i}"} for i in range(n)]})
    )
    return str(tmp_path / "engine")


def test_counts_distinct_vocab_families_not_cases(tmp_path):
    # Two distinct vocab families across three vocab cases; a case with no paren
    # (scoring-only bundle) is ignored.
    raw = _raw([
        "qwen2 (CN->Qwen2/Qwen2.5 1.0)",
        "qwen2 (CN->Qwen2/Qwen2.5 1.0)",      # duplicate family -> not double-counted
        "llama-bpe (US->Llama-3 1.0)",
        "openai_clean_us.json",               # scoring bundle, no paren
    ])
    s = ees.summarize(raw, "abc123", _engine_with_refs(tmp_path, 27), today="2026-09-16")
    assert s["vocab_families_exercised"] == 2
    assert s["reference_families_total"] == 27
    assert s["vocab_families_exercised"] <= s["reference_families_total"]


def test_network_accuracy_cases_are_not_vocab_families(tmp_path):
    # Accuracy-tier hostname cases ALSO carry a parenthetical but no "->" arrow;
    # they must not inflate the vocab-family count (the subtle part of the bug).
    raw = _raw([
        "qwen2 (CN->Qwen2/Qwen2.5 1.0)",          # vocab family (counts)
        "glm-4 (CN->GLM-4.5 1.0)",                # vocab family (counts)
        "api.deepseek.com (DeepSeek direct)",     # hostname (ignored)
        "localhost (loopback gateway)",           # hostname (ignored)
        "example.com (unknown host)",             # hostname (ignored)
    ])
    s = ees.summarize(raw, "sha", _engine_with_refs(tmp_path, 27))
    assert s["vocab_families_exercised"] == 2


def test_reference_total_read_from_engine_not_hardcoded(tmp_path):
    raw = _raw(["qwen2 (CN->Qwen2 1.0)", "yi (CN->Yi 1.0)"])
    s = ees.summarize(raw, "sha", _engine_with_refs(tmp_path, 27))
    assert s["reference_families_total"] == 27      # from tokenizer_ref.json, not 25


def test_total_falls_back_to_exercised_when_ref_missing(tmp_path):
    raw = _raw(["qwen2 (CN->Qwen2 1.0)", "yi (CN->Yi 1.0)", "glm-4 (CN->GLM 1.0)"])
    s = ees.summarize(raw, "sha", str(tmp_path / "no-engine-here"))
    assert s["vocab_families_exercised"] == 3
    assert s["reference_families_total"] == 3       # fallback, never < exercised


def test_never_reports_exercised_greater_than_total(tmp_path):
    # Even if the ref file somehow reports FEWER families than were exercised,
    # the total is clamped up so the site never renders "N of M" with N > M.
    raw = _raw([f"fam{i} (CN->F{i} 1.0)" for i in range(6)])
    s = ees.summarize(raw, "sha", _engine_with_refs(tmp_path, 1))
    assert s["reference_families_total"] >= s["vocab_families_exercised"]


def test_matrix_and_fp_rate_preserved(tmp_path):
    raw = _raw(["qwen2 (CN->Qwen2 1.0)"], fp=1)
    s = ees.summarize(raw, "sha", _engine_with_refs(tmp_path, 27), today="2026-09-16")
    assert s["matrix"] == {"TP": 3, "FP": 1, "TN": 4, "FN": 1, "ERR": 0}
    assert s["false_positive_rate"] == 1 / (4 + 1)  # FP / (TN + FP)
    assert s["generated"] == "2026-09-16"
    assert s["engine_commit"] == "sha"
