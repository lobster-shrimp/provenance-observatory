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
import re
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
from lib import records as _records  # noqa: E402 — canonical data readers (shared with api/)
from lib import feed as _feed  # noqa: E402 — shared RSS builder (shared with api/)

DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))
OUT_DIR = os.environ.get("OBSERVATORY_SITE_OUT", os.path.join(ROOT, "site", "dist"))
HOT_WINDOW_DAYS = _records.HOT_WINDOW_DAYS
SPARK_DAYS = 7

# Reader functions live once in lib/records.py; keep the internal names as aliases.
_load_target_records = _records.load_target_records
_load_promoted_advisories = _records.load_promoted_advisories
_manifests = _records.load_manifests




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


def _badge(verdict: str) -> str:
    """Colour a verdict label by severity for the table."""
    v = (verdict or "").upper()
    cls = "v"
    if v in ("CONFIRMED", "LIKELY"):
        cls = "v cn"
    elif v in ("UNLIKELY", "NO EVIDENCE"):
        cls = "v ok"
    elif v in ("US", "EU", "CA", "AE"):
        cls = "v ok"
    elif v in ("CN",):
        cls = "v cn"
    return f'<span class="badge {cls}">{html.escape(verdict or "—")}</span>'


def _interpreted_cells(latest: dict, promoted: dict | None) -> tuple[str, str, str]:
    """(provenance, jurisdiction, confidence).

    Shown when the target is CLEARED (the public record carries a `verdict`
    block, i.e. public=true) or a promoted advisory exists. Otherwise withheld.
    """
    v = latest.get("verdict")
    if v:
        conf = html.escape(str(v.get("confidence", "—")).split(" - ")[0])
        return (_badge(v.get("provenance")), _badge(v.get("jurisdiction")), conf)
    if promoted and promoted.get("verdict"):
        pv = promoted["verdict"]
        return (_badge((pv.get("provenance_risk") or {}).get("verdict")),
                _badge((pv.get("jurisdictional_risk") or {}).get("verdict")),
                html.escape(str((pv.get("provenance_risk") or {}).get("confidence", "—"))))
    return ('<span class="withheld">withheld</span>',
            '<span class="withheld">withheld</span>', "—")


def _coverage(rec: dict) -> dict:
    """Which evidence layers returned data, and whether the run is degraded.

    The strongest provenance signal is the tokenizer fingerprint, which needs
    the endpoint to report usage.prompt_tokens. Web apps and some APIs suppress
    that, leaving only wire + latency — real drift is still detectable but at
    LOWER confidence. This makes that explicit instead of silent.
    """
    tok = rec.get("tokenizer") or {}
    usable = tok.get("usable")
    layers = []
    if rec.get("network"):
        layers.append("network")
    if rec.get("headers") or rec.get("errors"):
        layers.append("wire")
    if usable:
        layers.append("tokenizer")
    if rec.get("latency"):
        layers.append("latency")
    return {"layers": layers, "tokenizer_usable": bool(usable),
            "degraded": usable is False, "unknown": usable is None}


def _coverage_badge(rec: dict) -> str:
    """Compact coverage indicator for the index target cell."""
    c = _coverage(rec)
    if c["degraded"]:
        return ('<div class="cov"><span class="badge warn">degraded</span> '
                'no token counts &middot; drift via wire+latency only</div>')
    if c["tokenizer_usable"]:
        return '<div class="cov muted">full signal &middot; tokenizer ✓</div>'
    return ""


def _coverage_note(rec: dict) -> str:
    """Detail-page coverage summary: layers present + degradation warning."""
    c = _coverage(rec)
    layers = ", ".join(c["layers"]) or "none"
    if c["degraded"]:
        return (f'<div class="note"><span class="badge warn">degraded coverage</span> '
                f'latest run reported no token counts (usage suppressed). Layers present: '
                f'{layers}. Fingerprint and drift detection rely on wire + latency only — '
                f'lower confidence than a tokenizer match.</div>')
    return f'<div class="note">Coverage (latest run): {layers}.</div>'


def _slug(target: str) -> str:
    """Filesystem/URL-safe per-target page name."""
    return re.sub(r"[^a-z0-9._-]", "_", (target or "target").lower())


def _detail_page(target: str, records: list[tuple[str, dict]], promoted: dict | None,
                 *, now_iso: str, probe_url: str) -> str:
    """Full drift timeline for one target: every dated run, fingerprint changes,
    control status, tokenizer coverage, and interpreted verdict (withheld unless
    cleared/promoted). The per-target drill-down the index table links to."""
    tgt = (records[-1][1].get("target") or {}) if records else {}
    kind = html.escape(str(tgt.get("kind", "") if isinstance(tgt, dict) else ""))

    # Mark fingerprint changes in ascending order, then show newest-first.
    flagged, prev = [], None
    for dstr, rec in records:
        fp = rec.get("fingerprint_id", "")
        flagged.append((dstr, rec, prev is not None and fp != prev))
        prev = fp
    n_changes = sum(1 for _, _, c in flagged if c)

    trows = []
    for dstr, rec, changed in reversed(flagged):
        fp = html.escape((rec.get("fingerprint_id") or "")[:14]) or "&mdash;"
        cc = rec.get("control_check")
        ctl = ("&mdash;" if not cc else
               f'<span class="ctl {"pass" if cc.get("pass") else "fail"}">'
               f'{"PASS" if cc.get("pass") else "FAIL"}</span>')
        usable = (rec.get("tokenizer") or {}).get("usable")
        tok = "yes" if usable else ('<span class="muted">suppressed</span>'
                                    if usable is not None else "&mdash;")
        prov, _juris, _conf = _interpreted_cells(rec, promoted)
        mark = ('<span class="sp-drift">&#9670; changed</span>' if changed
                else '<span class="sp-ok">&#9642; stable</span>')
        trows.append(f'<tr class="{"tl-chg" if changed else ""}">'
                     f'<td class="mono small">{html.escape(dstr)}</td>'
                     f'<td class="mono">{fp}</td><td>{mark}</td>'
                     f'<td>{ctl}</td><td>{tok}</td><td>{prov}</td></tr>')

    adv = promoted or {}
    adv_html = ("" if not adv else
                f'<div class="note"><b>Advisory {html.escape(adv.get("advisory_id", ""))}</b> '
                f'promoted {html.escape(adv.get("promoted_at", "")[:10])}</div>')

    body = f"""<div class="topnav"><a href="../index.html">&larr; Observatory</a>
    <a href="{probe_url}">Live probe tool &rarr;</a></div>
<header><h1>{html.escape(target)}</h1>
  <p>{kind or "target"} &middot; {len(records)} run(s) in the hot window &middot;
     {n_changes} fingerprint change(s)</p></header>
{_coverage_note(records[-1][1]) if records else ""}
{adv_html}
<table>
  <thead><tr><th>Date</th><th>Fingerprint</th><th>Change</th><th>Control</th>
    <th>Tokenizer</th><th>Provenance</th></tr></thead>
  <tbody>
{chr(10).join(trows) if trows else '<tr><td colspan="6" class="muted">No runs yet.</td></tr>'}
  </tbody>
</table>
<footer><span>Neutral evidence, append-only. Interpreted verdicts withheld until
  Gate 1 clears the target.</span><span>Updated {html.escape(now_iso[:16])} UTC</span></footer>"""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(target)} &middot; Provenance Observatory</title>'
            f'<style>{_CSS}</style></head><body><div class="wrap">{body}</div></body></html>')


def _evidence_cell(dstr: str, manifests_by_date: dict) -> str:
    """Link a row to the signed daily manifest covering its date (the design's
    'evidence bundle' column). Falls back to plain text if no manifest exists."""
    m = manifests_by_date.get(dstr)
    if not m:
        return '<span class="muted">&mdash;</span>'
    root = html.escape((m.get("manifest_root") or "")[:8])
    sig = "&middot;sig" if m.get("signed") else ""
    return (f'<a class="ev" href="evidence/{html.escape(dstr)}.json" '
            f'title="signed manifest for {html.escape(dstr)} (root {html.escape(m.get("manifest_root",""))})">'
            f'bundle-{root}&hellip;{sig} &#8599;</a>')


def _row(target: str, records: list[tuple[str, dict]], promoted: dict | None,
         manifests_by_date: dict) -> str:
    dstr, latest = records[-1]
    tgt = latest.get("target") or {}
    kind = (tgt.get("kind") if isinstance(tgt, dict) else "") or ""
    model = html.escape(str(tgt.get("model", "") if isinstance(tgt, dict) else ""))
    fp = html.escape((latest.get("fingerprint_id") or "")[:12])
    prov, juris, conf = _interpreted_cells(latest, promoted)

    # Raw values for client-side filtering (data-* attributes). Withheld rows
    # carry empty prov/juris so a provenance filter simply won't match them.
    v = latest.get("verdict") or {}
    pv = (promoted or {}).get("verdict") or {}
    raw_prov = v.get("provenance") or (pv.get("provenance_risk") or {}).get("verdict") or ""
    raw_juris = v.get("jurisdiction") or (pv.get("jurisdictional_risk") or {}).get("verdict") or ""
    drift = "1" if latest.get("drift_seen") else "0"

    ctl = latest.get("control_check")
    ctl_html = ""
    if ctl:
        cls = "pass" if ctl.get("pass") else "fail"
        ctl_html = f'<div class="ctl {cls}">control: {"PASS" if ctl.get("pass") else "FAIL"}</div>'

    return f"""<tr data-target="{html.escape(target)}" data-kind="{html.escape(kind)}" \
data-model="{model}" data-prov="{html.escape(raw_prov)}" data-juris="{html.escape(raw_juris)}" \
data-drift="{drift}">
  <td class="mono"><a class="tlink" href="t/{_slug(target)}.html">{html.escape(target)}</a>{ctl_html}{_coverage_badge(latest)}</td>
  <td>{html.escape(kind)}</td>
  <td class="mono">{model or "&mdash;"}</td>
  <td>{prov}</td>
  <td>{juris}</td>
  <td>{conf}</td>
  <td class="spark">{_sparkline(records)}</td>
  <td class="mono small">{html.escape(dstr)}</td>
  <td class="mono small">{_evidence_cell(dstr, manifests_by_date)}</td>
</tr>"""


def _severity_of(adv: dict) -> str:
    """Advisory severity for the badge. Stored severity wins; else derive from
    the worst monitor change; else 'info'."""
    s = (adv.get("severity") or "").lower()
    if s in ("high", "medium", "low", "info"):
        return s
    sevs = {c.get("severity") for c in (adv.get("evidence", {}).get("monitor_changes") or [])}
    if "critical" in sevs:
        return "high"
    if "high" in sevs:
        return "medium"
    if sevs:
        return "low"
    return "info"


def _advisories_rail(promoted: dict[str, dict]) -> str:
    head = '<a class="viewall tlink" href="advisories.html">VIEW ALL</a>'
    if not promoted:
        return (head + '<p class="muted">No advisories published yet. A verdict '
                '<i>change</i> becomes a numbered advisory (MPA-YYYY-NNN) after '
                'responsible disclosure and Gate-1 legal review.</p>')
    items = []
    for adv in sorted(promoted.values(), key=lambda a: a.get("promoted_at", ""), reverse=True):
        sev = _severity_of(adv)
        aid = adv.get("advisory_id", "")
        desc = html.escape(adv.get("summary") or adv.get("title")
                           or f'verdict change on {adv.get("target", "an endpoint")}')
        items.append(
            f'<div class="adv-item"><div class="adv-head">'
            f'<span class="sev {sev}">{sev.upper()}</span>'
            f'<span class="small muted">{html.escape(adv.get("promoted_at","")[:10])}</span></div>'
            f'<div class="mpa">{html.escape(aid)}</div><p>{desc}</p>'
            f'<a class="tlink" href="a/{_slug(aid)}.html">View advisory &#8599;</a></div>')
    return head + "".join(items)


# --- shared chrome ----------------------------------------------------------

def _footer(base: str = "", api_url: str = "") -> str:
    """Real footer with working links. `base` prefixes relative paths for pages
    served from a subdirectory (t/, a/). `api_url` defaults to the configured
    OBSERVATORY_API_URL so footer API links match the nav."""
    api_url = api_url or os.environ.get("OBSERVATORY_API_URL", "http://127.0.0.1:8000")
    year = date.today().year
    return f"""<footer>
  <div class="fcols">
    <div class="fcol">
      <h4>Provenance Observatory</h4>
      <p>&copy; {year} Provenance Observatory</p>
      <p>All evidence bundles are cryptographically signed.</p>
    </div>
    <div class="fcol"><h4>Resources</h4>
      <a href="{base}methodology.html">Methodology</a>
      <a href="{base}how-it-works.html">How It Works</a>
      <a href="{base}faq.html">FAQ</a>
      <a href="{base}data-dictionary.html">Data Dictionary</a>
      <a href="{api_url}/api/docs">API Documentation</a></div>
    <div class="fcol"><h4>Policies</h4>
      <a href="{base}disclosure.html">Responsible Disclosure</a>
      <a href="{base}security.html">Security Policy</a>
      <a href="{base}privacy.html">Privacy Policy</a>
      <a href="{base}terms.html">Terms of Use</a></div>
    <div class="fcol"><h4>Verification</h4>
      <p>Verify any evidence bundle signature and log inclusion.</p>
      <a href="{base}verify.html">Verify Evidence &#8599;</a></div>
    <div class="fcol"><h4>Transparency Log</h4>
      <p>All records are committed to an append-only, signed log.</p>
      <a href="{base}transparency-log.html">View Log &#8599;</a></div>
  </div>
</footer>"""


def _bottom_bar(manifests: list[dict], now_iso: str) -> str:
    if manifests:
        m = manifests[0]
        head = html.escape((m.get("manifest_root") or "")[:24])
        tail = (f'TRANSPARENCY LOG TREE HEAD: {head}&hellip; '
                f'&middot; STH DATE: {html.escape(m.get("date",""))}')
    else:
        tail = "TRANSPARENCY LOG: no manifests yet"
    return (f'<div class="bottombar"><span>All times UTC &middot; '
            f'Data updated: {html.escape(now_iso[:16])} UTC</span><span>{tail}</span></div>')


def _page(title: str, inner: str, *, base: str = "") -> str:
    """Standalone content page wrapper (shared CSS + real footer)."""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{html.escape(title)} &middot; Provenance Observatory</title>'
            f'<style>{_CSS}</style></head><body><div class="wrap">'
            f'<div class="topnav"><a href="{base}index.html">&larr; Observatory</a></div>'
            f'<header><h1>{html.escape(title)}</h1></header>'
            f'<div class="prose">{inner}</div>{_footer(base)}</div></body></html>')


# --- footer content pages (real, static) ------------------------------------

def _methodology_page() -> str:
    return _page("Methodology", """
<p>Each endpoint is assessed by independent layers; signals are combined by
log-odds into two separate verdicts, each with a confidence level. No single
layer decides a verdict.</p>
<h2>Layers</h2>
<ul>
  <li><b>Network / jurisdiction</b> — registry (RDAP) + endpoint classification.
    CDN-fronting makes raw IP geolocation unreliable, so this is registry-based.</li>
  <li><b>Wire fingerprint</b> — vendor headers, error schema, model catalog.</li>
  <li><b>Tokenizer fingerprint</b> — prompt-token counts over a fixed probe set
    vs shipped reference vectors; the strongest provenance signal, when the
    endpoint reports usage.</li>
  <li><b>Behavioral / deception</b> — self-ID, alignment asymmetry, CJK leakage,
    persona-vs-jurisdiction claims. Off for commercial monitoring here.</li>
  <li><b>Latency</b> — response-time profile, for drift corroboration.</li>
</ul>
<h2>Verdicts</h2>
<p>Two independent risks: <b>provenance</b> (are the weights Chinese-origin?) and
<b>jurisdiction</b> (is inference executed by a PRC operator / on PRC soil?).
Each lands on a tier: <code>CONFIRMED</code>, <code>LIKELY</code>,
<code>INDETERMINATE</code>, <code>UNLIKELY</code>, <code>NO EVIDENCE</code>.
When the strongest layer returns nothing, the verdict floors at INDETERMINATE —
never a false clean bill.</p>
<h2>Coverage &amp; confidence</h2>
<p>Web apps often suppress token counts; then the tokenizer layer is
unavailable and drift is judged on wire + latency only, at <b>degraded</b>
confidence. Each target's coverage is labelled explicitly.</p>
<h2>Accuracy</h2>
<p>The engine ships a hermetic accuracy/consistency eval (zero false positives
across 11 tokenizer families) run in CI; the live control false-positive rate is
shown on the home page. Full engine methodology and layer source live in the
<a href="https://github.com/lobster-shrimp/provenance-probe">provenance-probe</a>
repository.</p>""")


def _disclosure_page() -> str:
    return _page("Responsible Disclosure", """
<p>This project publishes <b>neutral evidence</b> (token counts, wire
fingerprint, latency, drift, fingerprint id, signed manifests) as it is
collected. <b>Interpreted verdicts</b> about a named operator are <b>withheld</b>
until:</p>
<ul>
  <li>the operator has been privately notified and given a disclosure window to
    respond, and</li>
  <li>legal review (Gate 1) has cleared publication for that target.</li>
</ul>
<p>A verdict <i>change</i> becomes a numbered advisory (MPA-YYYY-NNN) only after
that process. Verdicts are probabilistic, not proof, and carry a confidence
level and a measured error rate.</p>
<h2>Reporting</h2>
<p>To dispute a record or report an issue with a monitored endpoint you operate,
open an issue on the project repository. Corrections are appended to the log
(records are never silently deleted).</p>""")


def _verify_page() -> str:
    return _page("Verify Evidence", """
<p>Every day's records are hashed into a manifest with a single
<code>manifest_root</code> (the tree head), and the manifest is signed with
<a href="https://docs.sigstore.dev/">cosign</a> (keyless, via CI OIDC). You can
verify any bundle yourself.</p>
<h2>1. Fetch the manifest</h2>
<p>From the <a href="transparency-log.html">Transparency Log</a>, download the
day's <code>evidence/&lt;date&gt;.json</code> and its
<code>evidence/&lt;date&gt;.json.cosign.bundle</code>.</p>
<h2>2. Verify the signature</h2>
<pre><code>cosign verify-blob \\
  --bundle &lt;date&gt;.json.cosign.bundle \\
  --certificate-identity-regexp '.*' \\
  --certificate-oidc-issuer-regexp '.*' \\
  &lt;date&gt;.json</code></pre>
<h2>3. Verify inclusion</h2>
<p>Recompute the root: hash each listed record file, sort the
<code>path&nbsp;&nbsp;hash</code> lines, and SHA-256 the result — it must equal
<code>manifest_root</code>. Any changed, added, or removed record changes the
root, so the log is tamper-evident.</p>""")


def _transparency_page(manifests: list[dict]) -> str:
    if manifests:
        rows = "\n".join(
            f'<tr id="{html.escape(m.get("date",""))}">'
            f'<td class="mono">{html.escape(m.get("date",""))}</td>'
            f'<td class="mono small">{html.escape((m.get("manifest_root") or "")[:32])}&hellip;</td>'
            f'<td>{len(m.get("entries", {}))}</td>'
            f'<td>{_rekor_cell(m)}</td>'
            f'<td><a class="ev" href="evidence/{html.escape(m.get("date",""))}.json">manifest</a></td></tr>'
            for m in manifests)
    else:
        rows = '<tr><td colspan="5" class="muted">No manifests yet.</td></tr>'
    inner = f"""
<p>Every day's records are committed to an append-only log and hashed into a
signed manifest. The <code>manifest_root</code> is the tree head; any change to
any record changes it. Each signed manifest is also recorded in
<a href="https://www.sigstore.dev/">Rekor</a>, Sigstore's public append-only
transparency log — that Rekor entry is the independent inclusion proof (no
separate log server needed). See <a href="verify.html">Verify Evidence</a> to
check a bundle yourself.</p>
<table><thead><tr><th>Date</th><th>Tree head (manifest_root)</th><th>Records</th>
<th>Rekor entry</th><th>Bundle</th></tr></thead><tbody>
{rows}
</tbody></table>"""
    return _page("Transparency Log", inner)


def _rekor_cell(m: dict) -> str:
    """Link to the manifest's Rekor transparency-log entry when we have its index."""
    idx = m.get("rekor_log_index")
    if idx is not None:
        u = f"https://search.sigstore.dev/?logIndex={html.escape(str(idx))}"
        return f'<a class="ev" href="{u}">Rekor #{html.escape(str(idx))} &#8599;</a>'
    if m.get("signed"):
        return '<span class="small">signed (proof in bundle)</span>'
    return '<span class="muted">unsigned (local)</span>'


def _advisory_page(adv: dict) -> str:
    sev = _severity_of(adv)
    aid = html.escape(adv.get("advisory_id", ""))
    changes = adv.get("evidence", {}).get("monitor_changes") or []
    ch = "".join(f'<li><b>{html.escape(c.get("severity",""))}</b> '
                 f'{html.escape(c.get("field",""))}: {html.escape(c.get("detail",""))}</li>'
                 for c in changes) or "<li>(no change detail recorded)</li>"
    inner = f"""
<p><span class="sev {sev}">{sev.upper()}</span> &middot; promoted
{html.escape(adv.get("promoted_at","")[:10])} &middot; target
<span class="mono">{html.escape(adv.get("target",""))}</span></p>
<p>{html.escape(adv.get("summary") or adv.get("title") or "Verdict change advisory.")}</p>
<h2>Evidence (what changed)</h2>
<ul>{ch}</ul>
<p class="small muted">Interpreted verdicts are published only for targets cleared
through responsible disclosure and Gate-1 review.</p>"""
    return _page(f"{aid} advisory", inner, base="../")


def _advisories_index(promoted: dict) -> str:
    if not promoted:
        inner = ('<p class="muted">No advisories published yet. A verdict change '
                 'becomes a numbered advisory (MPA-YYYY-NNN) after responsible '
                 'disclosure and Gate-1 legal review.</p>')
    else:
        items = "".join(
            f'<div class="adv-item"><div class="adv-head">'
            f'<span class="sev {_severity_of(a)}">{_severity_of(a).upper()}</span>'
            f'<span class="small muted">{html.escape(a.get("promoted_at","")[:10])}</span></div>'
            f'<div class="mpa">{html.escape(a.get("advisory_id",""))}</div>'
            f'<p>{html.escape(a.get("summary") or a.get("title") or a.get("target",""))}</p>'
            f'<a class="tlink" href="a/{_slug(a.get("advisory_id",""))}.html">View advisory &#8599;</a></div>'
            for a in sorted(promoted.values(), key=lambda x: x.get("promoted_at", ""), reverse=True))
        inner = items
    return _page("Advisories", inner)


def _about_page() -> str:
    return _page("About", """
<p>The Provenance Observatory is independent, continuous, evidence-backed
monitoring of what actually serves a given LLM API endpoint — and whether that
model is Chinese-origin or under PRC jurisdiction.</p>
<p>Every night it probes a curated watch list, commits the raw measurements to
an append-only, cryptographically signed log, and opens a numbered advisory when
an endpoint's fingerprint changes. It publishes neutral evidence as collected;
interpreted verdicts about a named operator appear only after responsible
disclosure and legal review.</p>
<h2>Use it</h2>
<ul>
  <li><a href="index.html">Live verdict table</a> — search, filter, drill into any target.</li>
  <li><a href="methodology.html">Methodology</a> — how the layers and scoring work.</li>
  <li><a href="transparency-log.html">Transparency Log</a> + <a href="verify.html">Verify Evidence</a> — check any signed bundle yourself.</li>
  <li><a href="feed.xml">RSS</a> — advisories and drift events.</li>
</ul>
<p>The fingerprinting engine is
<a href="https://github.com/lobster-shrimp/provenance-probe">provenance-probe</a>;
this service consumes it as a black-box CLI. Both are open source and forkable.</p>""")


_CONTACT = "https://github.com/lobster-shrimp/provenance-observatory"


def _privacy_page() -> str:
    return _page("Privacy Policy", f"""
<p class="small muted">Describes this project's actual data practices. Last reviewed with the current build.</p>
<p>The Provenance Observatory is a public evidence site and a read-only JSON
API. There are <b>no user accounts and no sign-in</b>.</p>
<h2>What we collect from you</h2>
<ul>
  <li><b>The static site:</b> nothing. No cookies, no analytics, no trackers,
    no fingerprinting. Your browser fetches static files.</li>
  <li><b>The API:</b> a client IP is held transiently in memory only to enforce
    rate limiting; it is not persisted, logged to disk, or shared. Standard
    access logs may be retained by the hosting/CDN provider under their own
    policies.</li>
</ul>
<h2>What we publish</h2>
<p>Measurements about third-party <i>LLM endpoints</i> — not about site
visitors. Cryptographic signatures of our evidence are recorded in the public
<a href="https://www.sigstore.dev/">Rekor</a> transparency log.</p>
<h2>Third parties</h2>
<p>Requests are served by our hosting/CDN provider, which processes them under
its own privacy terms. We use no advertising or analytics vendors.</p>
<h2>Contact</h2>
<p>Questions or requests: <a href="{_CONTACT}">the project repository</a>.</p>""")


def _security_page() -> str:
    return _page("Security Policy", f"""
<p>The published surface is static and read-only, holds no user data or secrets,
and every day's evidence is cosign-signed and recorded in Rekor, so tampering is
detectable (see <a href="verify.html">Verify Evidence</a>).</p>
<h2>Reporting a vulnerability</h2>
<p>Please report security issues privately via
<a href="{_CONTACT}/security/advisories">the repository's security advisories</a>
(or open a minimal issue asking for a private channel). Include steps to
reproduce and impact. We aim to acknowledge promptly and to fix and disclose
responsibly.</p>
<h2>Scope</h2>
<ul>
  <li>In scope: this site, the API, the signing/verification path, the engine.</li>
  <li>Out of scope: denial-of-service, findings that require a compromised host,
    and the third-party endpoints we monitor (we only probe authorized targets;
    behavioral probes are off for commercial targets).</li>
</ul>
<p>A machine-readable summary is at
<a href="/.well-known/security.txt">/.well-known/security.txt</a>.</p>""")


def _terms_page() -> str:
    return _page("Terms of Use", f"""
<p>By using this site, the API, or the published evidence, you agree to the
following. The evidence and code are open source under the repository's licence.</p>
<h2>No warranty; verdicts are not proof</h2>
<p>Everything is provided <b>as is</b>, without warranty. Verdicts are
<b>probabilistic estimates</b> carrying a confidence level and a measured error
rate — they are <b>not proof</b> and <b>not legal advice</b>. Independently
verify the signed evidence before relying on any verdict for a decision.</p>
<h2>Use of the evidence</h2>
<ul>
  <li>The append-only, signed log is the canonical record; quote it in context
    and do not misrepresent a verdict's tier, confidence, or withheld status.</li>
  <li>Interpreted verdicts are published only for cleared targets; others are
    withheld and must not be inferred.</li>
  <li>The API is read-only and rate-limited; automated clients must respect the
    limits and identify themselves where practical.</li>
</ul>
<h2>Corrections and disputes</h2>
<p>Operators of a monitored endpoint may dispute a record via
<a href="disclosure.html">Responsible Disclosure</a>. Corrections are made by
appending to the log; records are never silently deleted.</p>
<h2>Liability</h2>
<p>To the maximum extent permitted by law, the project and its contributors are
not liable for any damages arising from use of the site, API, or evidence.</p>
<p class="small muted">Contact: <a href="{_CONTACT}">the project repository</a>.</p>""")


def _how_it_works_page() -> str:
    return _page("How It Works", """
<p>One nightly pipeline turns a watch list into signed, citable evidence.</p>
<h2>The pipeline</h2>
<ul>
  <li><b>Probe.</b> For each target, the <a href="https://github.com/lobster-shrimp/provenance-probe">provenance-probe</a>
    engine runs layered black-box checks (tokenizer, wire, network, latency).</li>
  <li><b>Score.</b> Signals combine by log-odds into two verdicts — provenance
    and jurisdiction — each with a confidence level.</li>
  <li><b>Sign.</b> The day's records are hashed into a manifest and signed with
    cosign keyless; the signature is recorded in Rekor (public transparency log).</li>
  <li><b>Commit.</b> The signed evidence is appended to git — the source of
    truth this site and API read.</li>
  <li><b>Detect drift.</b> Each run is diffed against a pinned baseline; a
    changed fingerprint opens a numbered advisory (after disclosure + review).</li>
</ul>
<h2>Two-tier publication</h2>
<p>Neutral evidence (token counts, wire fingerprint, latency, drift,
fingerprint id, signed manifests) is published as collected. Interpreted
verdicts about a named operator are withheld until responsible disclosure and
legal review clear the target. See <a href="methodology.html">Methodology</a>
and <a href="disclosure.html">Responsible Disclosure</a>.</p>""")


def _faq_page() -> str:
    return _page("FAQ", """
<h2>What's the difference between provenance and jurisdiction?</h2>
<p><b>Provenance</b> = are the model weights Chinese-origin, wherever they run.
<b>Jurisdiction</b> = is inference executed by a PRC-domiciled operator / on PRC
soil. They are scored independently; a US-hosted Chinese-origin model trips
provenance but not jurisdiction.</p>
<h2>Why are some verdicts "withheld"?</h2>
<p>Interpreted verdicts about a named operator publish only after responsible
disclosure and legal review. Until then the neutral evidence is still shown.</p>
<h2>Are these verdicts proof?</h2>
<p>No. They are probabilistic, carry a confidence level and a measured error
rate, and can be independently verified from the signed evidence.</p>
<h2>What is "degraded" coverage?</h2>
<p>When an endpoint suppresses token counts the strongest signal (tokenizer) is
unavailable, so drift is judged on wire + latency only, at lower confidence.
It's labelled per target so you know.</p>
<h2>Can I verify the evidence myself?</h2>
<p>Yes — see <a href="verify.html">Verify Evidence</a>. Every manifest is
cosign-signed and recorded in Rekor.</p>
<h2>How do I consume this programmatically?</h2>
<p>Use the <a href="feed.xml">RSS feed</a> or the JSON API (see the API link in
the nav). Fields are documented in the <a href="data-dictionary.html">Data
Dictionary</a>.</p>""")


def _data_dictionary_page() -> str:
    return _page("Data Dictionary", """
<p>Field reference for the published records and the JSON API.</p>
<h2>Verdict record / <code>/api/verdicts</code> item</h2>
<ul>
  <li><code>target</code> — endpoint identifier on the watch list.</li>
  <li><code>kind</code> — control-positive/-negative, aggregator, first-party, cn-direct, webapp.</li>
  <li><code>claimed_model</code> — the model id the endpoint advertises.</li>
  <li><code>provenance</code> / <code>jurisdiction</code> — verdict tier
    (CONFIRMED, LIKELY, INDETERMINATE, UNLIKELY, NO EVIDENCE) or null.</li>
  <li><code>confidence</code> — low / moderate / high.</li>
  <li><code>withheld</code> — true when the interpreted verdict is not yet cleared.</li>
  <li><code>drift_seen</code> — fingerprint changed vs the pinned baseline.</li>
  <li><code>coverage</code> — <code>{layers, degraded}</code>; degraded = token counts suppressed.</li>
  <li><code>fingerprint_id</code> — stable backend identity (overhead-invariant).</li>
  <li><code>last_checked</code> — date of the latest run.</li>
  <li><code>evidence</code> — <code>{date, manifest_root, signed}</code> for the day's signed bundle.</li>
</ul>
<h2>Manifest / <code>/api/manifests</code> item</h2>
<ul>
  <li><code>date</code>, <code>entries</code> (path -> sha256), <code>manifest_root</code> (tree head).</li>
  <li><code>signed</code> — a cosign bundle exists.</li>
  <li><code>rekor_log_index</code> — the Rekor transparency-log entry index (inclusion proof).</li>
</ul>
<p>Verdict tiers and layers are described in <a href="methodology.html">Methodology</a>.</p>""")


def _load_engine_eval(data_dir: str) -> dict:
    """Engine accuracy/consistency eval summary, if the engine published one.

    Written by provenance-probe's eval (eval/run_eval.py --json, summarised).
    Absent is fine — the panel degrades gracefully.
    """
    path = os.path.join(data_dir, "engine_eval.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}
    return {}


def _control_accuracy(records: dict) -> dict:
    """Live false-positive/true-positive tally from the controls' latest runs.

    control-negative failing = a false positive (US model flagged Chinese) — the
    continuous FP gate. control-positive passing = a known-CN model caught.
    """
    neg_total = neg_pass = pos_total = pos_pass = 0
    for recs in records.values():
        if not recs:
            continue
        cc = recs[-1][1].get("control_check")
        if not cc:
            continue
        if cc.get("kind") == "control-negative":
            neg_total += 1
            neg_pass += 1 if cc.get("pass") else 0
        elif cc.get("kind") == "control-positive":
            pos_total += 1
            pos_pass += 1 if cc.get("pass") else 0
    return {"neg_total": neg_total, "neg_pass": neg_pass,
            "fp": neg_total - neg_pass, "pos_total": pos_total, "pos_pass": pos_pass}


def _assurance_panel(records: dict, engine_eval: dict) -> str:
    """Two-column assurance panel: live control accuracy + engine hermetic eval."""
    ca = _control_accuracy(records)
    if ca["neg_total"] or ca["pos_total"]:
        cls = "ok" if ca["fp"] == 0 else "cn"
        live = (f'<b class="badge {cls}">{ca["fp"]} false positive'
                f'{"" if ca["fp"] == 1 else "s"}</b> '
                f'<span class="small">{ca["neg_pass"]}/{ca["neg_total"]} negative '
                f'controls clean · {ca["pos_pass"]}/{ca["pos_total"]} positive controls caught</span>')
    else:
        live = '<span class="muted">no control runs yet</span>'

    if engine_eval:
        m = engine_eval.get("matrix", {})
        fp = m.get("FP", "?")
        cls = "ok" if (fp == 0 and engine_eval.get("passed")) else "cn"
        eng = (f'<b class="badge {cls}">{fp} false positive'
               f'{"" if fp == 1 else "s"}</b> '
               f'<span class="small">{engine_eval.get("vocab_families_exercised", "?")} of '
               f'{engine_eval.get("reference_families_total", "?")} reference families '
               f'exercised · gate {"PASS" if engine_eval.get("passed") else "FAIL"}</span>'
               f'<div class="small muted">engine {html.escape(str(engine_eval.get("engine_commit", "")))}'
               f' · {html.escape(str(engine_eval.get("generated", "")))}</div>')
    else:
        eng = '<span class="muted">engine eval summary not published yet</span>'

    return f"""<div class="assurance">
  <div class="acol">
    <h3>Live control accuracy</h3>
    <div>{live}</div>
    <div class="small muted">Own authorized endpoints (DeepSeek+, OpenAI&minus;), rechecked each run.
      The continuous false-positive gate before any real-vendor verdict publishes.</div>
  </div>
  <div class="acol">
    <h3>Engine eval (hermetic)</h3>
    <div>{eng}</div>
    <div class="small muted">Consistency/regression gate in the engine's CI over open-weights
      tokenizers. Not a live-endpoint accuracy claim.</div>
  </div>
</div>"""


_CSS = """
  :root { --ink:#1a1a1a; --muted:#6b7280; --line:#e5e7eb; --bg:#fbfbfa; --accent:#0b7285; }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
    font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }
  .wrap { max-width:1200px; margin:0 auto; padding:32px 24px; }
  .topnav { display:flex; gap:14px; font-size:12px; text-transform:uppercase;
    letter-spacing:.08em; margin:0 0 14px; border-bottom:1px solid var(--line); padding-bottom:8px; }
  .topnav .active { color:var(--ink); font-weight:700; }
  .topnav a { color:var(--accent); text-decoration:none; }
  header h1 { font-size:20px; letter-spacing:.14em; margin:0 0 4px; }
  header p { color:var(--muted); margin:0 0 20px; }
  .note { border:1px solid var(--line); border-left:3px solid var(--accent);
    background:#fff; padding:10px 14px; margin:0 0 20px; color:#374151; }
  .stats { display:flex; gap:32px; border:1px solid var(--line); background:#fff;
    padding:14px 18px; margin-bottom:20px; }
  .stat b { display:block; font-size:20px; } .stat span { color:var(--muted); font-size:12px; }
  .layout { display:grid; grid-template-columns:1fr 260px; gap:24px; align-items:start; }
  table { width:100%; border-collapse:collapse; background:#fff; }
  th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }
  th { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  .mono { font-variant-ligatures:none; } .small { font-size:12px; color:#4b5563; }
  .withheld { color:var(--muted); font-style:italic; }
  .badge { border:1px solid var(--line); border-radius:3px; padding:1px 6px; font-size:12px; }
  .badge.cn { border-color:#f0c0c0; background:#fdf2f2; color:#b42318; }
  .badge.ok { border-color:#bfe3c7; background:#f3faf4; color:#0a7d33; }
  .badge.warn { border-color:#e6d08a; background:#fffbeb; color:#8a6d1a; }
  .ctl { font-size:11px; margin-top:3px; } .ctl.pass { color:#0a7d33; } .ctl.fail { color:#b42318; }
  .spark { letter-spacing:2px; } .sp-ok { color:#0a7d33; } .sp-drift { color:#b42318; } .sp-none { color:#cbd5e1; }
  aside h2 { font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); }
  ul.adv { list-style:none; padding:0; margin:0; } ul.adv li { padding:6px 0; border-bottom:1px solid var(--line); }
  .mpa { color:var(--accent); } .muted { color:var(--muted); }
  .assurance { display:grid; grid-template-columns:1fr 1fr; gap:0; border:1px solid var(--line);
    background:#fff; margin:0 0 20px; }
  .assurance .acol { padding:14px 18px; }
  .assurance .acol + .acol { border-left:1px solid var(--line); }
  .assurance h3 { font-size:11px; text-transform:uppercase; letter-spacing:.08em;
    color:var(--muted); margin:0 0 8px; }
  a.tlink { color:var(--accent); text-decoration:none; } a.tlink:hover { text-decoration:underline; }
  .back { display:inline-block; margin:0 0 14px; color:var(--accent); text-decoration:none; }
  .tl-chg td { background:#fdf2f2; }
  .cov { font-size:11px; margin-top:3px; }
  footer { margin-top:28px; border-top:1px solid var(--line); padding-top:18px; color:var(--muted); font-size:12px; }
  .fcols { display:grid; grid-template-columns:1.4fr 1fr 1fr 1fr 1fr; gap:24px; }
  .fcol h4 { font-size:11px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:0 0 8px; }
  .fcol a { display:block; color:var(--accent); text-decoration:none; padding:2px 0; }
  .fcol a:hover { text-decoration:underline; }
  .fcol p { margin:0 0 8px; }
  .bottombar { margin-top:18px; border-top:1px solid var(--line); padding-top:10px;
    display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap;
    font-size:11px; color:var(--muted); }
  .sev { font-size:10px; font-weight:700; letter-spacing:.05em; padding:1px 6px; border:1px solid var(--line); border-radius:3px; }
  .sev.high { border-color:#f0c0c0; background:#fdf2f2; color:#b42318; }
  .sev.medium { border-color:#e6d08a; background:#fffbeb; color:#8a6d1a; }
  .sev.low { border-color:#bfe3c7; background:#f3faf4; color:#0a7d33; }
  .sev.info { border-color:#c7d2e0; background:#f4f7fb; color:#33507d; }
  .adv-item { padding:10px 0; border-bottom:1px solid var(--line); }
  .adv-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:5px; }
  .adv-item .mpa { font-weight:700; color:var(--ink); }
  .adv-item p { margin:4px 0; color:#374151; }
  .viewall { float:right; font-size:11px; }
  a.ev { color:var(--accent); text-decoration:none; } a.ev:hover { text-decoration:underline; }
  .prose { max-width:760px; } .prose h2 { font-size:15px; margin:22px 0 8px; }
  .prose li { margin:4px 0; } .prose code { background:#f0f2f4; padding:1px 4px; border-radius:3px; }
  .nav { display:flex; align-items:center; gap:18px; font-size:12px; text-transform:uppercase;
    letter-spacing:.08em; margin:0 0 14px; border-bottom:1px solid var(--line); padding-bottom:8px; }
  .nav .brand { font-weight:700; letter-spacing:.12em; margin-right:auto; }
  .nav a { color:var(--accent); text-decoration:none; }
  .stbadge { display:inline-flex; align-items:center; gap:6px; }
  .stdot { width:8px; height:8px; border-radius:50%; background:#9aa3af; display:inline-block; }
  .stdot.ok { background:#0a7d33; } .stdot.warn { background:#8a6d1a; }
  .controls { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin:0 0 12px; }
  .controls input, .controls select { font:13px ui-monospace,monospace; padding:6px 9px;
    border:1px solid var(--line); border-radius:6px; background:#fff; }
  .controls input#q { flex:1; min-width:180px; }
  #count { color:var(--muted); font-size:12px; }
  tr.hidden { display:none; }
  #viewmore { margin:12px 0; text-align:center; }
  #viewmore button { font:12px ui-monospace,monospace; padding:8px 16px; border:1px solid var(--line);
    border-radius:6px; background:#fff; color:var(--accent); cursor:pointer; }
  .noscript-note { display:none; }
"""


def _nav(api_url: str, probe_url: str) -> str:
    """Top nav matching the design: STATUS badge, SEARCH, ABOUT, API, RSS."""
    return (f'<div class="nav"><span class="brand">PROVENANCE OBSERVATORY</span>'
            f'<span class="stbadge" id="stbadge"><span class="stdot"></span>'
            f'<span class="txt">nightly</span></span>'
            f'<a href="#" onclick="var e=document.getElementById(\'q\');if(e)e.focus();return false">Search</a>'
            f'<a href="about.html">About</a>'
            f'<a href="{html.escape(api_url)}/api/docs">API</a>'
            f'<a href="feed.xml">RSS</a>'
            f'<a href="{html.escape(probe_url)}">Live probe tool &rarr;</a></div>')


def _controls() -> str:
    """Client-side search + filter + count. Enhances the rendered table; the
    table works fully without JS (this is progressive enhancement)."""
    return ('<div class="controls">'
            '<input id="q" type="search" placeholder="Search target or model…" aria-label="Search">'
            '<select id="f-kind"><option value="">all kinds</option></select>'
            '<select id="f-prov"><option value="">any provenance</option>'
            '<option>CONFIRMED</option><option>LIKELY</option><option>INDETERMINATE</option>'
            '<option>UNLIKELY</option><option>NO EVIDENCE</option></select>'
            '<select id="f-juris"><option value="">any jurisdiction</option></select>'
            '<select id="f-drift"><option value="">all</option>'
            '<option value="drift">drift only</option></select>'
            '<span id="count"></span></div>')


# Progressive-enhancement script. Plain string (not an f-string) so its braces
# are literal; __API_URL__ / __PAGE_SIZE__ are filled at render time. Operates
# on the already-rendered rows, so search/filter/paginate work even if the API
# is offline; the status badge upgrades from 'nightly' to 'operational' only if
# the API answers.
_APP_JS = r"""
(function(){
  var API="__API_URL__", PAGE=__PAGE_SIZE__;
  var rows=[].slice.call(document.querySelectorAll('#vtable tbody tr[data-target]'));
  if(rows.length){
    var q=document.getElementById('q'), fk=document.getElementById('f-kind'),
        fp=document.getElementById('f-prov'), fj=document.getElementById('f-juris'),
        fd=document.getElementById('f-drift'), cnt=document.getElementById('count'),
        vm=document.getElementById('viewmore'), vmb=vm?vm.querySelector('button'):null;
    var shown=PAGE;
    function uniq(a){var s={};rows.forEach(function(r){var v=r.getAttribute(a);if(v)s[v]=1;});return Object.keys(s).sort();}
    function fill(sel,vals){if(!sel)return;vals.forEach(function(v){var o=document.createElement('option');o.value=v;o.textContent=v;sel.appendChild(o);});}
    fill(fk,uniq('data-kind')); fill(fj,uniq('data-juris'));
    function match(r){
      var s=(q&&q.value||'').toLowerCase();
      if(s && (r.getAttribute('data-target')+' '+r.getAttribute('data-model')).toLowerCase().indexOf(s)<0) return false;
      if(fk&&fk.value && r.getAttribute('data-kind')!==fk.value) return false;
      if(fp&&fp.value && r.getAttribute('data-prov')!==fp.value) return false;
      if(fj&&fj.value && r.getAttribute('data-juris')!==fj.value) return false;
      if(fd&&fd.value==='drift' && r.getAttribute('data-drift')!=='1') return false;
      return true;
    }
    function apply(){
      var f=rows.filter(match), i=0;
      rows.forEach(function(r){r.classList.add('hidden');});
      f.forEach(function(r){ if(i<shown) r.classList.remove('hidden'); i++; });
      if(cnt) cnt.textContent=f.length+' target'+(f.length===1?'':'s')+(f.length>shown?(' (showing '+shown+')'):'');
      if(vm) vm.style.display=(f.length>shown)?'block':'none';
    }
    [q,fk,fp,fj,fd].forEach(function(el){if(el)el.addEventListener('input',function(){shown=PAGE;apply();});});
    if(vmb) vmb.addEventListener('click',function(){shown+=PAGE;apply();});
    apply();
  }
  var badge=document.getElementById('stbadge');
  if(badge && API){
    fetch(API.replace(/\/$/,'')+'/api/status').then(function(r){return r.json();}).then(function(s){
      if(s&&s.ok){badge.querySelector('.stdot').className='stdot ok';badge.querySelector('.txt').textContent='operational';}
    }).catch(function(){});
  }
})();
"""

PAGE_SIZE = 25


def render(records: dict, promoted: dict, *, now_iso: str, engine_eval: dict | None = None,
           manifests: list[dict] | None = None) -> str:
    probe_url = os.environ.get("OBSERVATORY_PROBE_URL", "http://127.0.0.1:8770")
    api_url = os.environ.get("OBSERVATORY_API_URL", "http://127.0.0.1:8000")
    app_js = _APP_JS.replace("__API_URL__", api_url).replace("__PAGE_SIZE__", str(PAGE_SIZE))
    manifests = manifests or []
    mbd = {m.get("date"): m for m in manifests}
    n_targets = len(records)
    n_aggregators = sum(1 for recs in records.values()
                        if recs and (recs[-1][1].get("target") or {}).get("kind") == "aggregator")
    n_drift = sum(1 for recs in records.values() if recs and recs[-1][1].get("drift_seen"))
    rows = "\n".join(_row(t, recs, promoted.get(t), mbd) for t, recs in sorted(records.items()))
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Provenance Observatory</title>
<style>{_CSS}</style></head><body><div class="wrap">
{_nav(api_url, probe_url)}
<header>
  <h1>PROVENANCE OBSERVATORY</h1>
  <p>Independent, continuous, evidence-backed monitoring of LLM model provenance and jurisdiction.</p>
</header>
<div class="note">Neutral evidence (token counts, wire fingerprint, latency, drift) is published as
collected, in an append-only log. Interpreted verdicts about named operators are <b>withheld</b>
pending responsible disclosure and legal review (Gate 1). Verdicts are probabilistic, not proof.</div>
{_assurance_panel(records, engine_eval or {})}
<div class="stats">
  <div class="stat"><b>{n_targets}</b><span>MONITORED TARGETS</span></div>
  <div class="stat"><b>{n_aggregators}</b><span>ACTIVE AGGREGATORS</span></div>
  <div class="stat"><b>{n_drift}</b><span>DRIFT EVENTS (LATEST)</span></div>
  <div class="stat"><b>{len(promoted)}</b><span>PUBLISHED ADVISORIES</span></div>
  <div class="stat"><b>{html.escape(now_iso[:16])}</b><span>LAST UPDATED (UTC)</span></div>
</div>
<div class="layout">
  <main>
  {_controls()}
  <table id="vtable">
    <thead><tr>
      <th>Target</th><th>Kind</th><th>Claimed model</th>
      <th>Provenance</th><th>Jurisdiction</th><th>Confidence</th>
      <th>Stability (7d)</th><th>Last checked</th><th>Evidence bundle</th>
    </tr></thead>
    <tbody>
{rows if rows else '<tr><td colspan="9" class="muted">No probe data yet.</td></tr>'}
    </tbody>
  </table>
  <div id="viewmore" style="display:none"><button type="button">View more</button></div>
  </main>
  <aside>
    <h2>Advisories</h2>
    {_advisories_rail(promoted)}
  </aside>
</div>
{_footer()}
{_bottom_bar(manifests, now_iso)}
<script>{app_js}</script>
</div></body></html>"""


def build(data_dir: str = DATA_DIR, out_dir: str = OUT_DIR, *, now_iso: str | None = None) -> str:
    import shutil
    records = _load_target_records(data_dir)
    promoted = _load_promoted_advisories(data_dir)
    engine_eval = _load_engine_eval(data_dir)
    manifests = _manifests(data_dir)
    now_iso = now_iso or datetime.utcnow().isoformat()
    os.makedirs(out_dir, exist_ok=True)

    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w") as f:
        f.write(render(records, promoted, now_iso=now_iso,
                       engine_eval=engine_eval, manifests=manifests))

    # Footer + nav content pages (real links, not dead spans).
    for fname, doc in (
        ("methodology.html", _methodology_page()),
        ("disclosure.html", _disclosure_page()),
        ("verify.html", _verify_page()),
        ("transparency-log.html", _transparency_page(manifests)),
        ("advisories.html", _advisories_index(promoted)),
        ("about.html", _about_page()),
        ("how-it-works.html", _how_it_works_page()),
        ("faq.html", _faq_page()),
        ("data-dictionary.html", _data_dictionary_page()),
        ("security.html", _security_page()),
        ("privacy.html", _privacy_page()),
        ("terms.html", _terms_page()),
    ):
        with open(os.path.join(out_dir, fname), "w") as f:
            f.write(doc)

    # RFC 9116 security.txt (machine-readable disclosure contact).
    expires = (datetime.utcnow() + timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    wk = os.path.join(out_dir, ".well-known")
    os.makedirs(wk, exist_ok=True)
    with open(os.path.join(wk, "security.txt"), "w") as f:
        f.write(f"Contact: {_CONTACT}/security/advisories\n"
                f"Expires: {expires}\n"
                f"Policy: {_CONTACT}/blob/main/DISCLOSURE.md\n"
                "Preferred-Languages: en\n")

    # Static RSS feed (shared builder) so the RSS nav works without the API up.
    drift_items = [{"target": t, "last_checked": recs[-1][0]}
                   for t, recs in records.items() if recs and recs[-1][1].get("drift_seen")]
    entries = _feed.entries_from(list(promoted.values()), drift_items)
    with open(os.path.join(out_dir, "feed.xml"), "w") as f:
        f.write(_feed.build_rss(entries))

    # Signed evidence bundles: copy manifests (+ cosign bundles) so the site's
    # evidence-bundle links resolve and are independently verifiable.
    edir = os.path.join(out_dir, "evidence")
    os.makedirs(edir, exist_ok=True)
    for p in glob.glob(os.path.join(data_dir, "manifests", "*.json*")):
        shutil.copy2(p, os.path.join(edir, os.path.basename(p)))

    # Per-advisory pages under a/.
    adir = os.path.join(out_dir, "a")
    os.makedirs(adir, exist_ok=True)
    for adv in promoted.values():
        with open(os.path.join(adir, f"{_slug(adv.get('advisory_id',''))}.html"), "w") as f:
            f.write(_advisory_page(adv))

    # Per-target detail pages (drift timeline) under t/.
    probe_url = os.environ.get("OBSERVATORY_PROBE_URL", "http://127.0.0.1:8770")
    tdir = os.path.join(out_dir, "t")
    os.makedirs(tdir, exist_ok=True)
    for target, recs in records.items():
        page = _detail_page(target, recs, promoted.get(target),
                            now_iso=now_iso, probe_url=probe_url)
        with open(os.path.join(tdir, f"{_slug(target)}.html"), "w") as f:
            f.write(page)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=DATA_DIR)
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    print("wrote", build(a.data, a.out))
