"""Live JSON API over the committed evidence tree (FastAPI). Local-first.

Serves only the PUBLIC two-tier records already in data/ (interpreted verdicts
are present only for cleared targets / promoted advisories), so the API is
Gate-1-consistent by construction — it never sees the gated tier.
"""
