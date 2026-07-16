# Phase 11 — Deals & Funding Intelligence

Phase 11 replaces the former Deals & Funding placeholder with an evidence-governed commercial workspace. Explicit transaction records, research-grant metadata and web-discovered signals remain separate. Streamlit reads a cached PostgreSQL projection; external discovery runs only in scheduled jobs.

## 11A — Licensing, partnerships and M&A

- Stores licensing, acquisition/merger and commercial-partnership event types separately.
- Retains published parties, subject/asset, announcement date, status, geography and evidence URL only when supplied by the retained source.
- Preserves missing counterparties, dates and statuses as `Not stated`.
- Treats a company-domain result as primary-source-linked evidence but still requires human review of the transaction facts.
- Treats third-party web results as discovery signals requiring primary-source verification.

## 11B — Financing, grants and transaction values

- Stores corporate-financing events separately from research grants.
- Retains numeric value and currency only when structured source evidence supplies them.
- Never estimates undisclosed upfront payments, milestones, royalties, financing totals or currencies.
- Retains explicit funder and award identifiers from Europe PMC, OpenAlex and Crossref scholarly metadata.
- Does not infer grant recipient or award value when the scholarly metadata does not establish them.

## 11C — Commercial-signal qualification and monitoring

- Adds bounded, optional Tavily commercial-signal discovery for retained Account Intelligence organisations.
- Classifies M&A, licensing, commercial partnership, corporate financing and other commercial signals deterministically from retained titles/snippets.
- Compares a result URL with the organisation's retained official domain; non-matching results remain unverified signals.
- Runs `commercial_intelligence` weekly and preserves hash-deduplicated, append-only event observations and monitor telemetry.
- Reports the primary-verification queue explicitly.

## 11D — Deals & Funding workspace

- Adds search, event-type and evidence-status filters.
- Shows licensing, M&A, partnership, financing, grant and verification metrics.
- Provides separate tabs for deals/signals and research grants.
- Adds a commercial-event detail page with parties, value, date, status, evidence boundary, source link and observation history.
- Provides separate CSV exports for commercial events and research grants.

## Evidence boundaries

A discovery signal is not a confirmed transaction. A company press release is evidence of what the company announced, not independent proof that a transaction completed. Scholarly funder metadata is not proof of award value, current status or recipient. PharmaTune does not infer ownership transfer, exclusivity, territories, milestone structures, royalties, deal completion or commercial intent when the evidence does not state them.

## Production activation

Schema migration 12 is applied automatically. The standard bootstrap runs `commercial_intelligence` after the scholarly and research projections. `deal_discovery` runs only when `TAVILY_API_KEY` is configured and the optional source is enabled; its results remain verification-gated. Explicit primary transaction feeds can be ingested using the governed commercial source types without changing the workspace schema.
