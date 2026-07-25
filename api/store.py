"""In-memory index over data/ for the API. Rebuilt from the git-committed tree.

    data/ (signed, append-only)  ──load()──►  Store
      target/date/verdict.json                  .verdicts()  filter+paginate
      advisories/*.json                         .target(name)
      manifests/*.json                          .advisories() .manifests()
                                                .status() .search()

`withheld` is derived from the record itself: an interpreted verdict block is
present only for cleared targets (or a promoted advisory), exactly as the site
renders it — so the API cannot leak a gated verdict.
"""
from __future__ import annotations
import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, ROOT)
from lib import records as _records  # noqa: E402

DATA_DIR = os.environ.get("OBSERVATORY_DATA_DIR", os.path.join(ROOT, "data"))


def _coverage(rec: dict) -> dict:
    """Which layers returned data + degraded flag (tokenizer/usage suppressed)."""
    usable = (rec.get("tokenizer") or {}).get("usable")
    layers = [name for name, present in (
        ("network", rec.get("network")),
        ("wire", rec.get("headers") or rec.get("errors")),
        ("tokenizer", usable),
        ("latency", rec.get("latency")),
    ) if present]
    return {"layers": layers, "degraded": usable is False}


def _interpreted(rec: dict, promoted: dict | None) -> dict:
    """(provenance, jurisdiction, confidence, withheld). Present only when the
    target is cleared (record carries `verdict`) or a promoted advisory exists."""
    v = rec.get("verdict")
    if v:
        return {"provenance": v.get("provenance"), "jurisdiction": v.get("jurisdiction"),
                "confidence": v.get("confidence"), "withheld": False}
    if promoted and promoted.get("verdict"):
        pv = promoted["verdict"]
        return {"provenance": (pv.get("provenance_risk") or {}).get("verdict"),
                "jurisdiction": (pv.get("jurisdictional_risk") or {}).get("verdict"),
                "confidence": (pv.get("provenance_risk") or {}).get("confidence"),
                "withheld": False}
    return {"provenance": None, "jurisdiction": None, "confidence": None, "withheld": True}


class Store:
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
        self.reload()

    def reload(self) -> None:
        self.records = _records.load_target_records(self.data_dir)
        self.promoted = _records.load_promoted_advisories(self.data_dir)
        self._manifests = _records.load_manifests(self.data_dir)
        self.transcripts = _records.load_transcripts(self.data_dir)
        self._mbd = {m.get("date"): m for m in self._manifests}

    # -- item shaping --------------------------------------------------------
    def _item(self, target: str, recs: list) -> dict:
        dstr, latest = recs[-1]
        tgt = latest.get("target") or {}
        interp = _interpreted(latest, self.promoted.get(target))
        m = self._mbd.get(dstr) or {}
        return {
            "target": target,
            "kind": tgt.get("kind", ""),
            "claimed_model": tgt.get("model", ""),
            "last_checked": dstr,
            "fingerprint_id": latest.get("fingerprint_id", ""),
            "drift_seen": bool(latest.get("drift_seen")),
            "coverage": _coverage(latest),
            "control_check": latest.get("control_check"),
            **interp,
            "evidence": {"date": dstr, "manifest_root": m.get("manifest_root"),
                         "signed": bool(m.get("signed"))},
        }

    # -- queries -------------------------------------------------------------
    def verdicts(self, *, kind=None, jurisdiction=None, provenance=None,
                 drift=None, q=None, limit=50, offset=0) -> dict:
        items = [self._item(t, r) for t, r in sorted(self.records.items()) if r]
        if kind:
            items = [i for i in items if i["kind"] == kind]
        if jurisdiction:
            items = [i for i in items if (i["jurisdiction"] or "").upper() == jurisdiction.upper()]
        if provenance:
            items = [i for i in items if (i["provenance"] or "").upper() == provenance.upper()]
        if drift is not None:
            items = [i for i in items if i["drift_seen"] == drift]
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["target"].lower()
                     or ql in (i["claimed_model"] or "").lower()]
        total = len(items)
        return {"total": total, "limit": limit, "offset": offset,
                "items": items[offset:offset + limit]}

    def target(self, name: str) -> dict | None:
        recs = self.records.get(name)
        trec = self.transcripts.get(name)
        if not recs and not trec:
            return None
        if recs:
            item = self._item(name, recs)
            item["history"] = [{"date": d, "fingerprint_id": r.get("fingerprint_id", ""),
                                "drift_seen": bool(r.get("drift_seen")),
                                "coverage": _coverage(r),
                                "control_check": r.get("control_check")}
                               for d, r in reversed(recs)]
        else:
            item = {"target": name, "kind": "session-watch", "withheld": True,
                    "provenance": None, "jurisdiction": None, "confidence": None}
        if trec:
            item["model_change_events"] = trec.get("model_change_events", [])
            item["distinct_identities"] = trec.get("distinct_identities", [])
            item["session_verdict"] = trec.get("verdict")
        return item

    def model_switches(self) -> list:
        """Targets with recorded mid-session model/identity switches."""
        out = []
        for t, r in sorted(self.transcripts.items()):
            evs = r.get("model_change_events") or []
            if evs:
                out.append({"target": t, "events": evs,
                            "distinct_identities": r.get("distinct_identities", []),
                            "verdict": r.get("verdict")})
        return out

    def advisories(self) -> list:
        return sorted(self.promoted.values(),
                      key=lambda a: a.get("promoted_at", ""), reverse=True)

    def manifests(self) -> list:
        return self._manifests

    def manifest(self, date_str: str) -> dict | None:
        return self._mbd.get(date_str)

    def status(self) -> dict:
        latest = self._manifests[0] if self._manifests else {}
        n_aggr = sum(1 for r in self.records.values()
                     if r and (r[-1][1].get("target") or {}).get("kind") == "aggregator")
        n_drift = sum(1 for r in self.records.values() if r and r[-1][1].get("drift_seen"))
        return {
            "monitored_targets": len(self.records),
            "active_aggregators": n_aggr,
            "drift_events": n_drift,
            "published_advisories": len(self.promoted),
            "model_switch_alerts": sum(1 for r in self.transcripts.values()
                                       if r.get("model_change_events")),
            "last_updated": latest.get("date"),
            "transparency_log_tree_head": latest.get("manifest_root"),
            "ok": True,
        }

    def search(self, q: str, limit=20) -> list:
        ql = (q or "").lower().strip()
        if not ql:
            return []
        hits = []
        for t, r in sorted(self.records.items()):
            if not r:
                continue
            item = self._item(t, r)
            if ql in t.lower() or ql in (item["claimed_model"] or "").lower() \
               or ql in (item["kind"] or "").lower():
                hits.append({"target": t, "kind": item["kind"],
                             "claimed_model": item["claimed_model"]})
        return hits[:limit]
