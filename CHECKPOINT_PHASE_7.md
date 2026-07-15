# Phase 7 — Pharmaceutical Memory

Status: implementation candidate.

## First production slice

PharmaTune projects its governed opportunity index into a durable memory layer:

- canonical company, product, molecule and problem entities;
- evidence-linked relationships that retain their stable lead and source IDs;
- append-only observations whenever the governed lead snapshot changes;
- a read-optimised Pharmaceutical Memory screen for company histories.

The projection is deterministic and idempotent. It performs no external source or
LLM calls and does not change Opportunity Score, lead status, human audit history,
case-study approval gates or stable lead IDs.

Relationships remain public evidence signals requiring human validation. They do
not prove current commercial need, buying intent, product-specific root cause or
solution fit.

## Completion gate

1. migration 8 applies successfully to production PostgreSQL;
2. repeated synchronisation creates no duplicate entities, relationships or observations;
3. a changed lead creates a new append-only observation while retaining its identity;
4. the live Pharmaceutical Memory page loads company relationships from Neon;
5. the existing regression suite remains green.
