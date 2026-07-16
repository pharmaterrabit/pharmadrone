# PharmaTune canonical delivery ledger

This ledger resolves earlier conversations that mixed Phase and Checkpoint names.
Repository commits and regression tests are the completion evidence.

| Canonical delivery | Status | Repository evidence |
|---|---|---|
| Phase 1 — Opportunity Matcher | Complete | deterministic problem-to-solution and technology-to-target matcher |
| Phase 2 — Opportunity Index | Complete | stable lead IDs, novelty and persistent queue |
| Phase 3A — Source Reliability | Complete | source health and evidence-quality enrichment |
| Phase 3B — Official Enrichment | Complete | FDA, trial and literature context enrichment |
| Phase 3C — Seller-to-Target Workflow | Complete | deterministic seller-fit workflow |
| Checkpoint 3 — 20-company pilot | Complete | configurable deterministic pilot export |
| Checkpoint 4 — 100-target validation | Complete | frozen audit-ready validation study |
| Checkpoint 5A–5A.4 — Discovery depth | Complete | bounded source expansion and diagnostics |
| Checkpoint 6A–6A.5.2 — Precision | Complete | deterministic precision and external-eligibility gates |
| Checkpoint 6B — Human audit | Complete | append-only review and correction history |
| Checkpoint 6C/6C.1 — Persistence | Complete | PostgreSQL plus scheduled incremental refresh |
| Checkpoint 6D-A — Analyst platform | Complete | customer/analyst Streamlit dashboard |
| Checkpoint 6D-B — Administration | Complete | role-separated administration workspace |
| Checkpoint 7A/7A.1 — Real case study and performance | Complete | Hovione workflow and warm-navigation optimisation |
| Phase 7 memory slice | Complete | canonical entities, evidence relationships and observations |
| Checkpoint 7B — Production Readiness | Complete in code; live verdict required | seven fail-closed System Health gates |
| Checkpoint 8.1 A–D — Sales-useful regulatory intelligence | Complete in code; production refresh required | source-quality telemetry, authoritative entity repair, official links and deterministic qualification briefs |
| Checkpoint 8.2 A–D — Commercial qualification routing | Complete in code; production refresh required | readiness tiers, database filters, contact-function routing and explicit qualification gaps |
| Checkpoint 8.3 A–D — Account Intelligence | Complete in code; production refresh required | canonical organisations and aliases, MAH/manufacturer/product/signal relationships, public-evidence contact governance and weekly monitoring |
| Checkpoint 8.4 A–D — Regulatory Intelligence workspace | Complete in code; production refresh required | dedicated FDA/EMA/MHRA event workspace, database filters, official-evidence detail, monitoring and export |
| Phase 9 A–D — Patent & Lifecycle Intelligence | Complete in code; production projection required | FDA Orange Book applications/products, listed patents and exclusivities, evidence-gated ownership/family resolution, expiry timelines, weekly monitoring and exports |

Labels such as “1A” and “2A” were not used in the repository and are not silently
invented here. The canonical sequence above is the permanent source of truth.
