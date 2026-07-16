# Checkpoint 8.1 A–D — Sales-useful regulatory intelligence

Status: implementation complete; production data refresh required after merge.

## A — Data-quality visibility

The Data Sources page now reports, by official source, the number of indexed
signals and missing company, product, region, official-link, problem and
score/grade fields. Company is measured separately because many official records
do not publish a manufacturer or authorisation holder; that absence is displayed
instead of fabricated.

## B — Authoritative entity repair

Legacy opportunity rows are repaired from their stored official source records
on every scheduled ingestion. Corrected company, product and region fields flow
from the connector record into the opportunity projection. A product description
is never copied into Company.

## C — Official evidence links

Opportunity Explorer includes an `Open` link for each valid HTTP(S) official
source. Opportunity Detail keeps the same official-source action and rejects
record IDs or slugs that are not real URLs.

## D — Deterministic sales qualification brief

Every FDA, EMA, MHRA and ClinicalTrials.gov opportunity receives a stored,
non-LLM qualification brief containing the target account (or an explicit
source-data gap), product, public signal, evidence basis, validation status,
recommended next check and commercial limitation. It does not set
`has_full_report` and does not claim that a public signal proves urgency, budget,
buying intent or solution fit.

## Completion gate

- focused Checkpoint 8.1 regression tests pass;
- the complete automated suite passes;
- Python compilation and diff validation pass;
- GitHub main contains the checkpoint;
- the production scheduler repairs legacy records and builds qualification briefs;
- Streamlit displays the source-quality table and working evidence links.
