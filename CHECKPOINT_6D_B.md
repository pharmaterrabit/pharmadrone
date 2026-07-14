# Checkpoint 6D-B — Workspace and Platform Administration

Status: **deployment candidate — locally validated on 14 July 2026**.

## Scope

Checkpoint 6D-B adds two role-separated administration experiences without placing platform controls inside the Customer / Analyst navigation.

Workspace Administration includes:

- organisation profile and live workspace-usage counts;
- members, pending invitations and workspace role assignment;
- case-study export policy;
- notification preferences and MFA policy;
- data-retention controls;
- organisation-scoped administration audit activity.

Platform Administration includes:

- live infrastructure overview;
- organisation, workspace and user provisioning;
- the enforced role-and-permission matrix;
- source connector state and scheduler operations;
- ingestion totals, failed jobs and retries;
- durable API usage/cost telemetry when available;
- PostgreSQL health and checksum-verified audit backups;
- append-only administration and security events;
- durable feature flags;
- an honest System Configuration placeholder until each setting has a validated schema and audit rule.

## Access separation

The server resolves the experience from a credential assigned to one role. There is no client-side role switcher.

- `APP_PASSWORD` opens the Customer / Analyst platform.
- `PLATFORM_ADMIN_PASSWORD` opens Platform Administration.
- `WORKSPACE_ADMIN_PASSWORD` opens Workspace Administration.
- `WORKSPACE_ADMIN_ORGANISATION_ID` binds that workspace administrator credential to one organisation.

All configured passwords must be distinct. Workspace administrators are restricted again in the data-access and mutation functions; hiding navigation is not treated as an authorization boundary. They cannot access other organisations, platform connectors, scheduler failures, API billing, backups, feature flags, global database controls, secrets or platform-wide security logs.

## Durable administration schema

Migration 6 adds empty, real-data tables for organisations, workspaces, administration users, workspace settings, append-only administration events, backup records, feature flags and provider usage telemetry. It does not create demonstration organisations, users, costs or feature flags.

Mutations are validated, tenant-scoped and audited. Workspace membership must match the selected organisation. Export, notification and retention values are constrained before persistence. Source secrets, database URLs and provider credentials are never returned to an administration page.

## Frozen intelligence protections

Checkpoint 6D-B does not alter source evidence, Opportunity Scores, stable lead IDs, signal tiers, ClinicalTrials classification, company-role logic, external eligibility, outreach eligibility or the frozen benchmark. It reuses the existing scheduler state and checksum-verified audit backup paths. Administration events are stored separately from human evidence-review history.

## Local validation result

- 97 tests run: 96 passed and one optional live-PostgreSQL integration test skipped;
- every Platform Administration destination rendered through Streamlit's application test runtime without an exception;
- Workspace Administration rendered without platform navigation;
- cross-tenant operations and platform-role assignment by workspace administrators were rejected;
- invalid workspace memberships, governance values and retention periods were rejected;
- migration 6 was repeat-safe in the local database test suite;
- all new and modified Python modules passed bytecode compilation.

## Live deployment validation checklist

- [ ] Deploy the complete repository to the existing Streamlit application.
- [ ] Confirm PostgreSQL applies migration 6 and reports schema v6.
- [ ] Add distinct Platform Admin and Workspace Admin credentials in Streamlit secrets.
- [ ] Sign in as Platform Admin and inspect every administration destination using real production values.
- [ ] Provision the first production organisation and workspace, then bind Workspace Admin to that organisation ID.
- [ ] Sign in as Workspace Admin and confirm global operations remain inaccessible.
- [ ] Sign in with the existing Analyst credential and confirm the Customer / Analyst platform is unchanged.
- [ ] Confirm the frozen 100-record benchmark and audit approval totals are unchanged.

