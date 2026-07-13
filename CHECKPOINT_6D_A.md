# Checkpoint 6D-A — Customer / Analyst Platform

Status: **stable — deployed and validated live on 13 July 2026**.

## Scope

Checkpoint 6D-A replaces the legacy five-tab customer experience with the approved PharmaTune enterprise Customer / Analyst platform. The legacy application is retained in `legacy_app.py` for rollback and frozen-workflow reference.

Implemented customer screens:

- Overview;
- Opportunity Explorer and Opportunity Detail;
- Companies, Products and Technology Profile;
- Human Validation;
- Case Study Builder;
- Data Sources and System Health;
- clearly labelled placeholders for Research & Innovation, Regulatory Signals, Deals & Funding, Patents and Settings.

The Platform Admin Console and Workspace Administration are intentionally excluded until Checkpoint 6D-B.

## Data and performance

- Overview uses lightweight aggregate queries.
- Opportunity Explorer uses database-side filtering, `LIMIT`/`OFFSET` pagination and bounded page sizes.
- Full index loading occurs only when the user explicitly builds a case study.
- Audit history is loaded on demand.
- Exports are generated only after a user requests a case study.
- All production figures are read from the configured backend; unsupported modules show empty planned states rather than demo data.

## Evidence and audit protections

The UI preserves the three-layer model: confirmed source evidence, deterministic PharmaTune interpretation and human decision. It does not change Opportunity Scores, stable lead IDs, signal tiers, ClinicalTrials classification, company-role logic, external eligibility or the frozen benchmark. Human audit saves continue through the existing append-only Checkpoint 6B write path.

## Stability

Checkpoint 6C.1 was automatically validated on 13 July 2026. The live system showed PostgreSQL schema v5, five migrations, nine enabled source jobs, zero failed sources, the scheduled run stored at 2026-07-13T12:42:52Z, 100 frozen audit records, four audit versions and four corrections.

Checkpoint 6D-A live validation completed on 13 July 2026. The deployed platform passed sign-in, responsive navigation, PostgreSQL reads, database-side pagination, opportunity-detail evidence separation, case-study generation and both CSV and Markdown exports. A controlled non-approving audit-progress version was appended to record `D-1274-2020` by `Codex Validation`; it became Version 1 with status `in_review`. The benchmark remained 100 records, completed approvals remained four, external approvals remained zero and no source evidence was modified.

## Local validation result

- 88 tests run: 87 passed and one optional live-PostgreSQL integration test skipped;
- every Customer / Analyst navigation destination rendered without an application exception;
- production startup remains fail-closed;
- the original `pharmadrone/` business-logic package is byte-for-byte unchanged from the Checkpoint 6C.1 input archive;
- no schema migration was required.

## Live deployment validation checklist

- [x] Deploy the complete repository to the existing Streamlit application.
- [x] Confirm sign-in and PostgreSQL schema v5 health.
- [x] Review the responsive Customer / Analyst navigation and narrow-screen controls.
- [x] Confirm the Explorer returns real PostgreSQL records and pagination changes pages.
- [x] Open an opportunity and verify the three evidence layers remain distinct.
- [x] Save one non-approving audit-progress version and confirm Version 1 is retained.
- [x] Build and download one 20-record case-study shortlist as CSV and Markdown.
- [x] Confirm the scheduler shows the latest automatic run and zero unexplained failed sources.
- [x] Confirm the frozen benchmark remains 100 records; the deliberate validation write changed no source evidence or approval totals.
