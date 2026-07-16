# Checkpoint 8.2 — Commercial qualification routing

Status: implementation complete; production refresh required after merge.

## A — Qualification-priority tiers

Every indexed opportunity is classified as P1 Ready to qualify, P2 Account
research, or P3 Evidence repair. The tier is based only on the presence of a
named target account, a product and a working official-source link. It is not a
commercial-urgency score and does not infer budget, buying intent or solution
fit.

## B — Actionable database filters and ordering

Opportunity Explorer can filter by qualification tier and recommended contact
function. Filtering, counting, ordering and pagination remain in the database so
the warm-page performance rule is preserved.

## C — Role-specific contact routing

Deterministic source and problem rules route quality/recall signals to Quality /
CMC, shortage signals to Supply Chain / Procurement, safety signals to
Pharmacovigilance / Regulatory Affairs, and clinical-trial signals to Clinical
Development / Business Development. Each route includes an explicit rationale.

## D — Qualification gaps and next action

Opportunity Detail shows the missing facts that must be resolved before
qualification and a safe next action. Existing and newly refreshed records work:
the UI derives the route at read time, while the scheduler persists the same
fields in the sales qualification brief for auditability.

## Completion gate

- focused Checkpoint 8.2 regression tests pass;
- the complete automated suite passes;
- Python compilation and diff validation pass;
- GitHub main contains the checkpoint;
- Streamlit deploys the merged dashboard;
- the production scheduler persists updated qualification briefs.
