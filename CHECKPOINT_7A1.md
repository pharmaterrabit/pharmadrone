# Checkpoint 7A.1 — Warm Navigation Performance

Status: **deployment candidate — locally validated on 15 July 2026**.

## Objective

Normal dashboard navigation should return useful server-rendered content within
approximately one second when the Streamlit process and Neon database are warm.
Streamlit Community Cloud cold starts are measured separately because a sleeping
host must restart the Python process before PharmaTune can serve a page.

## Changes

- ordered schema migrations are checked once per configured database engine and
  process, rather than on every Streamlit rerun;
- analyst shell database health is cached for 30 seconds;
- overview, validation, source-health, entity and administration reads use short,
  bounded caches;
- successful human-validation and administration writes invalidate relevant
  cached reads before rerendering;
- Opportunity Explorer filter facets are cached separately and the previous
  duplicate one-row query is removed;
- scheduled refreshes remain externally durable and cached dashboard reads expire
  within 15 seconds (five minutes for stable filter labels).

## Safety

The change does not alter source evidence, opportunity scoring, stable lead IDs,
human approvals, external-use gates, seller matching or exports. A new process,
disposed engine or different database URL still runs the complete ordered
migration check before serving data.

## Local validation

- complete automated suite passes;
- migration-once behaviour has a regression test;
- Python bytecode compilation and whitespace validation pass.

## Live completion gate

1. deploy to the existing Streamlit Community Cloud application;
2. confirm Neon reports schema v7;
3. measure warm navigation across Overview, Explorer, Companies, Products,
   Human Validation, Case Studies, Data Sources and System Health;
4. accept when warm server response is approximately one second for ordinary
   navigation, while recording cold-start time separately;
5. confirm a saved human audit decision immediately invalidates stale audit data.
