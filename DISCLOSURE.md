# Publication Policy

This is the operative publication policy of the Provenance Observatory.

The Provenance Observatory publishes its complete work as it is collected: the
**measurements** (token counts, wire fingerprint, latency, drift, fingerprint
id, control-check results) **and** the **interpreted verdicts** (provenance and
jurisdiction, each with a confidence label), together, in an **append-only,
cryptographically signed log**. Nothing is withheld and nothing is delayed. A
verdict published alongside the evidence that produced it is more trustworthy,
not less: anyone can see exactly how each verdict was reached and judge it for
themselves.

A verdict *change* becomes a numbered advisory (`MPA-YYYY-NNN`) and is published
as collected, on the same surfaces (site, JSON API, RSS) and in the same signed
log as every other record.

## What we do NOT claim

Transparency is not certainty. We are explicit about the limits of a black-box
method:

- **Verdicts are probabilistic, not proof.** Every verdict carries a confidence
  label. A verdict is an evidence-weighted estimate, never a legal
  determination.
- **Distillation and fine-tuning confound origin.** A model distilled from, or
  fine-tuned on outputs of, another family can carry signals of that family.
  Provenance signals weight origin; they cannot by themselves prove who trained
  a set of weights.
- **Black-box signal degrades under evasion.** An operator who suppresses token
  counts, normalizes headers, or otherwise adapts to the probes reduces the
  available signal. When the strongest layer is unavailable, coverage is labeled
  **degraded** and confidence is lowered accordingly — never silently.
- **We publish a false-positive rate.** Known-answer (positive) and negative
  controls run continuously; the live control false-positive rate is shown on
  the home page, and a hermetic accuracy/consistency eval runs in the engine's
  CI. A verdict must be read against that measured error rate.

## Corrections and retractions

Any operator, or any reader, may dispute a record at any time by opening an
issue on the project repository. There is no window to wait out and nothing to
clear first.

- A verdict we find to be wrong is **prominently RETRACTED**. The retraction is
  itself an appended, signed record — the original is never silently deleted, so
  the correction and what it corrects both stay in the log.
- Corrections and retractions publish on **the same surfaces** as the original
  verdict (site, API, RSS) with equal prominence.
- Because the log is append-only and signed, the full history — including our
  mistakes and their corrections — is permanent and independently verifiable.

## Contact

Report a dispute, correction request, or security issue via the project
repository:

- Repository: <https://github.com/lobster-shrimp/provenance-observatory>
- Security contact: `SECURITY_CONTACT_TBD`
- PGP key: `PGP_KEY_TBD`
