# Checkpoint 6B — Human Validation and Audit Workflow

## Frozen baseline

Checkpoint 6B is additive to the frozen **Checkpoint 6A.5.2** deterministic production baseline. It does not modify discovery, Opportunity Score, stable lead IDs, signal tiers, ClinicalTrials evidence-code rules, company-role attribution, external-case-study eligibility, report generation or source connectors.

## Audit database schema

Checkpoint 6B creates four separate SQLite tables at runtime:

1. `audit_benchmark_batches`
   - immutable metadata and SHA-256 checksum for an imported golden validation CSV;
2. `audit_queue_records`
   - immutable original benchmark rows keyed by batch and source identity;
3. `human_audit_versions`
   - append-only, source-ID-keyed audit decisions and checklist state;
4. `human_audit_corrections`
   - append-only correction history containing original value, corrected value, reviewer, timestamp, reason and supporting URL.

These tables are separate from `opportunities`, `evidence`, `opportunity_index`, enrichment and source-health tables.

## UI workflow

Results & Export contains a **Human Validation Queue** section:

- optional import of the frozen Checkpoint 6A.5.2 100-target CSV;
- queue filters for audit status, deterministic external eligibility, signal tier, source type, company warning, seller fit, region, report availability, source ID, company and product;
- audit dashboard metrics;
- three separate review tabs:
  - **A. Original source record**
  - **B. PharmaTune interpretation**
  - **C. Human audit decision**
- append-only audit form with external-use and outreach approval gates;
- version history and correction history;
- internal, external-approved, outreach-approved, rejected/correction-required, audit-history and correction-history exports.

## Human approval gates

External approval requires official-source, product, company-role and signal checks, warning acknowledgement and explicit human external-use approval. Tier D records are blocked. Unresolved company/distributor warnings require explicit human resolution.

Outreach approval additionally requires valid external approval, current-relevance validation, correct target company/site validation, technical-fit validation and reviewed outreach wording.

## Golden benchmark

The deployment ZIP does not contain the user's live 100-target CSV. Upload it through the Human Validation Queue to preserve it as an immutable SHA-256-keyed benchmark batch. No deterministic values are rewritten during import.
