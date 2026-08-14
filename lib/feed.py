"""Shared RSS 2.0 builder — used by both the live API (api/app.py) and the
static site (site/build.py) so the feed is identical whether served or built."""
from __future__ import annotations
from xml.sax.saxutils import escape as _esc

LINK = "https://github.com/lobster-shrimp/provenance-observatory"


def build_rss(entries: list[tuple[str, str, str]]) -> str:
    """entries: list of (title, description, pubDate). Returns RSS 2.0 XML."""
    items = "".join(
        f"<item><title>{_esc(t)}</title>"
        f"<description>{_esc(d)}</description>"
        f"<pubDate>{_esc(str(p))}</pubDate>"
        f'<guid isPermaLink="false">{_esc(t + str(p))}</guid></item>'
        for t, d, p in entries)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<rss version="2.0"><channel>'
            '<title>Provenance Observatory</title>'
            '<description>LLM provenance/jurisdiction advisories and drift.</description>'
            f'<link>{LINK}</link>{items}</channel></rss>')


def entries_from(advisories: list[dict], drift_items: list[dict],
                 announcements: "list[dict] | None" = None) -> list[tuple[str, str, str]]:
    """Normalise announcements + advisories + drift rows into feed entries (shared
    shape). Announcements are news (releases/method notes), prefixed and kept
    DISTINCT from the numbered MPA provenance advisories."""
    out = []
    for n in announcements or []:
        body = n.get("body") or n.get("summary") or ""
        if n.get("url"):
            body = (body + "  " + n["url"]).strip()
        out.append((f"Announcement: {n.get('title', 'release')}", body, n.get("date", "")))
    for a in advisories:
        out.append((f"{a.get('advisory_id','advisory')}: {a.get('target','')}",
                    a.get("summary") or a.get("title") or "Verdict change advisory.",
                    a.get("promoted_at", "")))
    for it in drift_items:
        out.append((f"Drift: {it.get('target','')}",
                    f"Fingerprint changed; last checked {it.get('last_checked') or it.get('date','')}.",
                    it.get("last_checked") or it.get("date", "")))
    return out
