# Checkpoint 8.4 — Regulatory Intelligence workspace

Status: implementation complete; production deployment required after merge.

## A — Dedicated regulatory workspace

Regulatory Intelligence is now separate from Opportunity Explorer. It contains
FDA, EMA and MHRA recalls/quality defects, shortages, safety communications,
safety reviews/referrals/outcomes and post-authorisation withdrawals. Search,
filtering, counting, ordering and pagination execute in the database.

## B — Evidence-first event detail

Each event opens a regulator-specific detail view containing the source ID,
medicine/product, organisation or MAH when stated, market, event classification,
last-check date, structured source facts and a direct official evidence link.
Missing evidence URLs are shown as repair requirements rather than hidden.

## C — Monitoring and analyst action

The workspace exposes current, review-due, stale and missing-review-date states.
It routes recalls to Quality/CMC, shortages to Supply Chain/Procurement, and
safety/withdrawal actions to Pharmacovigilance/Regulatory Affairs. These are
review routes, not claims of commercial need, urgency, budget or buying intent.

## D — Coverage, quality and export

Analysts can inspect regulator/event coverage, source-level completeness,
missing-organisation and missing-link counts, and export the currently filtered
page to CSV. Normal navigation reads stored PostgreSQL data and performs no live
network calls, preserving the warm-page performance rule.

## Completion gate

- deterministic regulator/event taxonomy passes tests;
- database filtering excludes non-regulatory trial records;
- official string and structured evidence links work;
- monitoring states and responsible-function routes pass tests;
- navigation and hidden detail routing pass regression tests;
- the full automated suite, compilation and diff validation pass;
- GitHub main contains the checkpoint and Streamlit deploys it.
