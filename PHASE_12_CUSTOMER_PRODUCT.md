# Phase 12 — Customer Product

Phase 12 turns the existing PharmaTune intelligence engine into a durable,
tenant-scoped customer workflow without weakening evidence governance.

## 12A — Customer workspace and access scope

- Analyst and read-only executive experiences share the customer application.
- Every saved list, item, rule, alert, export and activity event is scoped by
  organisation/workspace identity or the explicit personal default workspace.
- Workspace administration continues to manage members, roles, MFA, retention,
  notification mode and export policy separately from customer intelligence.

## 12B — Saved intelligence

- Customers can create private or workspace-visible lists.
- Opportunity, organisation, regulatory, patent, research and commercial-event
  detail pages can save records directly to those lists.
- Stable record IDs, retained evidence URLs, notes and evidence-boundary snapshots
  are stored; a saved record is not converted into a confirmed commercial need.

## 12C — Alerts

- Daily or weekly rules can monitor all intelligence or one record family using
  search, source and region filters.
- The daily scheduler evaluates rules only against stored PostgreSQL data, so
  Streamlit navigation never waits for an external source or LLM.
- Alert fingerprints make repeated evaluation idempotent. Read and dismiss
  actions remain tenant scoped and auditable.

## 12D — Governed exports

- Read-only users cannot create lists, rules, decisions or exports.
- Internal exports require the workspace export policy and retain the internal
  intelligence/human-review wording.
- External saved-list exports include only opportunities whose latest human audit
  explicitly approves external use. Unreviewed opportunities and every other
  record family are excluded rather than silently presented as customer safe.
- Export audience, policy, record/exclusion counts and SHA-256 checksum are stored.

## 12E — Customer-facing workflow

- My Workspace summarises lists, saved records, active rules, unread alerts,
  exports and recent activity.
- Saved Lists and Alerts provide complete create, inspect, remove, evaluate,
  acknowledge and download workflows.
- Workspace Settings shows the authenticated scope and effective policy without
  exposing passwords, provider keys or database credentials.

## 12F — Operations and deployment

- Migration 13 creates the customer workflow tables and indexes.
- `customer_alerts` runs daily through the existing GitHub Actions orchestrator.
- Reads use bounded Streamlit caches; external APIs are never called by customer
  page navigation.
- Customer workflow writes and exports create append-only activity records.

## Production activation

Merging to `main` lets Streamlit apply migration 13 at startup. The deployment
should retain `APP_PASSWORD` for the analyst account. Tenant deployments may also
set `ANALYST_ORGANISATION_ID`, `ANALYST_WORKSPACE_ID` and
`ANALYST_EXPORT_ALLOWED`; an optional `READ_ONLY_PASSWORD` provisions the
read-only executive surface. Workspace policy remains authoritative.
