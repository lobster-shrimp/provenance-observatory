#!/usr/bin/env python3
"""Render the public GitHub Pages site from data/ (approved Variant C).

Variant C: light Certificate-Transparency / academic register — a stats band, a
dense verdict table, an advisories rail, and methodology/disclosure/verification
links in the footer. Chosen for citability: reads as neutral evidence, not a
vendor dashboard.

PUBLICATION RULE (T5 / Gate 1): the site renders only NEUTRAL evidence from
data/<target>/<date>/verdict.json. The interpreted columns (provenance,
jurisdiction, confidence) show "withheld" unless a PROMOTED public advisory
exists for that target (data/advisories/*.json). Control checks are about our
own endpoints, so they are shown. Nothing accusatory about a named vendor
appears until it has been promoted through the disclosure pipeline.

Scaling (U2): reads the hot window (last HOT_WINDOW_DAYS of daily records).
Raw JSON is never deleted; only the rendered view is bounded.

Self-contained output: one index.html, inline CSS, no external dependencies.
"""
from __future__ import annotations
import argparse
import glob
import html
import json
import os
from datetime import date, datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))
OUT_DIR = os.environ.get("OBSERVATORY_SITE_OUT", os.path.join(ROOT, "site", "dist"))
HOT_WINDOW_DAYS = 90
SPARK_DAYS = 7


def _load_target_records(data_dir: str) -> dict[str, list[tuple[str, dict]]]:
    """target -> [(date_str, record)] sorted ascending, hot window only."""
    cutoff = date.today() - timedelta(days=HOT_WINDOW_DAYS)
    out: dict[str, list[tuple[str, dict]]] = {}
    for verdict_path in glob.glob(os.path.join(data_dir, "*", "*", "verdict.json")):
        parts = verdict_path.split(os.sep)
        target, dstr = parts[-3], parts[-2]
        try:
            if date.fromisoformat(dstr) < cutoff:
                continue
        except ValueError:
            continue
        with open(verdict_path) as f:
            rec = json.load(f)
        out.setdefault(target, []).append((dstr, rec))
    for recs in out.values():
        recs.sort(key=lambda x: x[0])
    return out


def _load_promoted_advisories(data_dir: str) -> dict[str, dict]:
    """target -> latest promoted public advisory record, if any."""
    latest: dict[str, dict] = {}
    for p in glob.glob(os.path.join(data_dir, "advisories", "*.json")):
        with open(p) as f:
            adv = json.load(f)
        t = adv.get("target")
        if t and (t not in latest or adv.get("promoted_at", "") > latest[t].get("promoted_at", "")):
            latest[t] = adv
    return latest


def _sparkline(records: list[tuple[str, dict]]) -> str:
    """7-day fingerprint-stability sparkline as coloured glyphs.

    ▪ stable (same fingerprint as the prior day) · missing day ◆ changed.
    """
    by_date = {d: r for d, r in records}
    today = date.today()
    glyphs, prev_fp = [], None
    for i in range(SPARK_DAYS - 1, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        rec = by_date.get(d)
        if rec is None:
            glyphs.append('<span class="sp sp-none" title="%s: no data">·</span>' % d)
            continue
        fp = rec.get("fingerprint_id", "")
        if prev_fp is None or fp == prev_fp:
            glyphs.append('<span class="sp sp-ok" title="%s: stable">▪</span>' % d)
        else:
            glyphs.append('<span class="sp sp-drift" title="%s: changed">◆</span>' % d)
        prev_fp = fp
    return "".join(glyphs)


def _interpreted_cells(target: str, kind: str, promoted: dict | None) -> tuple[str, str, str]:
    """(provenance, jurisdiction, confidence) — withheld unless a promoted
    advisory exists for this target."""
    if promoted and promoted.get("verdict"):
        v = promoted["verdict"]
        prov = html.escape(str((v.get("provenance_risk") or {}).get("verdict", "—")))
        juris = html.escape(str((v.get("jurisdictional_risk") or {}).get("verdict", "—")))
        conf = html.escape(str((v.get("provenance_risk") or {}).get("confidence", "—")))
        return (f'<span class="badge v">{prov}</span>',
                f'<span class="badge v">{juris}</span>', conf)
    return ('<span class="withheld">withheld</span>',
            '<span class="withheld">withheld</span>', "—")


def _row(target: str, records: list[tuple[str, dict]], promoted: dict | None) -> str:
    dstr, latest = records[-1]
    tgt = latest.get("target") or {}
    kind = (tgt.get("kind") if isinstance(tgt, dict) else "") or ""
    model = html.escape(str(tgt.get("model", "") if isinstance(tgt, dict) else ""))
    fp = html.escape((latest.get("fingerprint_id") or "")[:12])
    prov, juris, conf = _interpreted_cells(target, kind, promoted)

    ctl = latest.get("control_check")
    ctl_html = ""
    if ctl:
        cls = "pass" if ctl.get("pass") else "fail"
        ctl_html = f'<div class="ctl {cls}">control: {"PASS" if ctl.get("pass") else "FAIL"}</div>'

    return f"""<tr>
  <td class="mono">{html.escape(target)}{ctl_html}</td>
  <td>{html.escape(kind)}</td>
  <td class="mono">{model or "&mdash;"}</td>
  <td>{prov}</td>
  <td>{juris}</td>
  <td>{conf}</td>
  <td class="spark">{_sparkline(records)}</td>
  <td class="mono small">{html.escape(dstr)}</td>
  <td class="mono small">{fp}</td>
</tr>"""


def _advisories_rail(promoted: dict[str, dict]) -> str:
    if not promoted:
        return '<p class="muted">No advisories published yet. Interpreted verdicts are ' \
               'withheld pending responsible disclosure and legal review.</p>'
    items = []
    for adv in sorted(promoted.values(), key=lambda a: a.get("promoted_at", ""), reverse=True):
        items.append(
            f'<li><span class="mpa">{html.escape(adv.get("advisory_id",""))}</span> '
            f'<span class="mono">{html.escape(adv.get("target",""))}</span> '
            f'<span class="small muted">{html.escape(adv.get("promoted_at","")[:10])}</span></li>')
    return "<ul class='adv'>" + "".join(items) + "</ul>"


def render(records: dict, promoted: dict, *, now_iso: str) -> str:
    n_targets = len(records)
    n_drift = sum(1 for recs in records.values() if recs and recs[-1][1].get("drift_seen"))
    rows = "\n".join(_row(t, recs, promoted.get(t)) for t, recs in sorted(records.items()))
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Provenance Observatory</title>
<style>
  :root {{ --ink:#1a1a1a; --muted:#6b7280; --line:#e5e7eb; --bg:#fbfbfa; --accent:#0b7285; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:32px 24px; }}
  header h1 {{ font-size:20px; letter-spacing:.14em; margin:0 0 4px; }}
  header p {{ color:var(--muted); margin:0 0 20px; }}
  .note {{ border:1px solid var(--line); border-left:3px solid var(--accent);
    background:#fff; padding:10px 14px; margin:0 0 20px; color:#374151; }}
  .stats {{ display:flex; gap:32px; border:1px solid var(--line); background:#fff;
    padding:14px 18px; margin-bottom:20px; }}
  .stat b {{ display:block; font-size:20px; }} .stat span {{ color:var(--muted); font-size:12px; }}
  .layout {{ display:grid; grid-template-columns:1fr 260px; gap:24px; align-items:start; }}
  table {{ width:100%; border-collapse:collapse; background:#fff; }}
  th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }}
  .mono {{ font-variant-ligatures:none; }} .small {{ font-size:12px; color:#4b5563; }}
  .withheld {{ color:var(--muted); font-style:italic; }}
  .badge {{ border:1px solid var(--line); border-radius:3px; padding:1px 6px; font-size:12px; }}
  .ctl {{ font-size:11px; margin-top:3px; }} .ctl.pass {{ color:#0a7d33; }} .ctl.fail {{ color:#b42318; }}
  .spark {{ letter-spacing:2px; }} .sp-ok {{ color:#0a7d33; }} .sp-drift {{ color:#b42318; }} .sp-none {{ color:#cbd5e1; }}
  aside h2 {{ font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }}
  ul.adv {{ list-style:none; padding:0; margin:0; }} ul.adv li {{ padding:6px 0; border-bottom:1px solid var(--line); }}
  .mpa {{ color:var(--accent); }} .muted {{ color:var(--muted); }}
  footer {{ margin-top:28px; border-top:1px solid var(--line); padding-top:14px;
    color:var(--muted); font-size:12px; display:flex; gap:20px; flex-wrap:wrap; }}
</style></head><body><div class="wrap">
<header>
  <h1>PROVENANCE OBSERVATORY</h1>
  <p>Independent, continuous, evidence-backed monitoring of LLM model provenance and jurisdiction.</p>
</header>
<div class="note">Neutral evidence (token counts, wire fingerprint, latency, drift) is published as
collected, in an append-only log. Interpreted verdicts about named operators are <b>withheld</b>
pending responsible disclosure and legal review (Gate 1). Verdicts are probabilistic, not proof.</div>
<div class="stats">
  <div class="stat"><b>{n_targets}</b><span>MONITORED TARGETS</span></div>
  <div class="stat"><b>{n_drift}</b><span>DRIFT EVENTS (LATEST)</span></div>
  <div class="stat"><b>{len(promoted)}</b><span>PUBLISHED ADVISORIES</span></div>
  <div class="stat"><b>{html.escape(now_iso[:16])}</b><span>LAST UPDATED (UTC)</span></div>
</div>
<div class="layout">
  <main>
  <table>
    <thead><tr>
      <th>Target</th><th>Kind</th><th>Claimed model</th>
      <th>Provenance</th><th>Jurisdiction</th><th>Confidence</th>
      <th>Stability (7d)</th><th>Last checked</th><th>Fingerprint</th>
    </tr></thead>
    <tbody>
{rows if rows else '<tr><td colspan="9" class="muted">No probe data yet.</td></tr>'}
    </tbody>
  </table>
  </main>
  <aside>
    <h2>Advisories</h2>
    {_advisories_rail(promoted)}
  </aside>
</div>
<footer>
  <span>Methodology</span><span>Responsible Disclosure</span>
  <span>Verify Evidence</span><span>Transparency Log</span>
  <span>&copy; Provenance Observatory</span>
</footer>
</div></body></html>"""


def build(data_dir: str = DATA_DIR, out_dir: str = OUT_DIR, *, now_iso: str | None = None) -> str:
    records = _load_target_records(data_dir)
    promoted = _load_promoted_advisories(data_dir)
    now_iso = now_iso or datetime.utcnow().isoformat()
    doc = render(records, promoted, now_iso=now_iso)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w") as f:
        f.write(doc)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    print("wrote", build(a.data, a.out))
