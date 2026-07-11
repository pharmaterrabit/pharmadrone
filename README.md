# PharmaTune / PharmaDrone — Global Pharma Opportunity Engine (v1)

A **private, local** pharma BD intelligence engine. It scans configured public sources,
extracts company + product opportunities relevant to formulation, drug-delivery,
CMC, quality, and CDMO/service-provider use cases, scores each one 0–100, rejects
weak leads, and writes evidence-backed case studies (Markdown + HTML) plus exports.

Phase 1 also includes a small **Opportunity Matcher** that reuses already generated
evidence to match product/problem signals to solution types, partner categories, and
technology-target hypotheses. It is **matched against currently indexed PharmaTune evidence**; use Generate/Refresh to add new signals. Matches are potential relevance signals and not proof that a company needs a specific technology.

Not a chatbot. No accounts, no billing. Runs on your laptop **or** as a private
password-protected cloud dashboard (see `DEPLOY.md` for Render Free). **Milestone 1
is just 5 real test reports** — you review those before generating any more.

---

## Run it on your laptop (step by step)

Use **Python 3.12.x** for deployment stability. The repo pins `.python-version` to `3.12.13`, and the package stack is pinned in `requirements.txt` to avoid unbounded hosted-runtime upgrades. Check locally with `python --version` (or `python3 --version`).

**1. Open a terminal in the project folder**

```bash
cd pharmadrone
```

**2. Create a private environment and install the libraries**

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows (PowerShell):
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**3. Add your keys**

Copy the example file to `.env`, then open `.env` in any text editor.

- macOS / Linux: `cp .env.example .env`
- Windows: `copy .env.example .env`

You choose your LLM provider — **no single provider is required**. The default is
OpenRouter with a free model, so you typically need just two keys:
- `OPENROUTER_API_KEY` → https://openrouter.ai/keys  (default provider)
- `TAVILY_API_KEY` → https://tavily.com  (web search)

To use a different LLM, set `LLM_PROVIDER` to `groq`, `openai`, or `gemini` and
fill that provider's key instead. Gemini is optional — only needed if you pick it.

**4. (Recommended) Test the data sources first**

```bash
python -m pharmadrone.test_connectors
```
You'll see a check or cross for each source, with the exact error if one fails.
Fix any failure before generating (usually a missing key or no internet).

**5. Start the dashboard**

```bash
streamlit run app.py
```
Your browser opens at **http://localhost:8501**. Go to tab **1 Generate** and
click **Generate 5 Test Reports** (or **Generate 5 Failure/Rescue Opportunity
Reports** to bias toward recalls/terminations/CMC signals — see
`FAILURE_SIGNAL_LAYER.md`). If a run produces fewer reports than expected, open
the **🔍 Debug** panel that appears after the run — it shows exactly where
candidates were lost (LLM errors, rejection reasons, low evidence) rather than
failing silently.

**6. Review the output**

Reports appear in tab **4 Results & Export** and as files in the `./reports`
folder. Nothing beyond 5 runs unless you choose to.

> Prefer the command line? `python -m pharmadrone.run --mode test`
> Failure/Rescue (event-first) mode: `python -m pharmadrone.run --mode failure`
> Verify discovery finds real cases: `python -m pharmadrone.benchmark` (live) or
> `python -m pharmadrone.benchmark --offline` (fixtures, no network).

---

## The keys (what each is for)

Set **`LLM_PROVIDER`** to choose your model source, then set only that provider's key.

| Variable | Required? | Used for |
|---|---|---|
| `LLM_PROVIDER` | pick one | `openrouter` (default) / `groq` / `openai` / `gemini` |
| `LLM_MODEL` | optional | model string; blank = cheap provider default |
| `OPENROUTER_API_KEY` | if provider=openrouter | extraction / scoring / writing |
| `GROQ_API_KEY` | if provider=groq | extraction / scoring / writing |
| `OPENAI_API_KEY` | if provider=openai | extraction / scoring / writing |
| `GEMINI_API_KEY` | if provider=gemini | **optional fallback** only |
| `TAVILY_API_KEY` | **Yes** | company / press / pipeline + multilingual web discovery |
| `APP_PASSWORD` | cloud | dashboard login |
| `CONTACT_EMAIL` | optional | politer Crossref + OpenAlex |

ClinicalTrials.gov, openFDA, Europe PMC, OpenAlex and Crossref need **no key**.

Cheap/free default models per provider: OpenRouter
`meta-llama/llama-3.3-70b-instruct:free`, Groq `llama-3.1-8b-instant`, OpenAI
`gpt-4o-mini`, Gemini `gemini-2.0-flash`. If the app can't reach the LLM it fails
with a clear message naming the exact key/env var to fix.

---

## Testing each source separately (point 6)

- **Dashboard:** tab **5 Connectors** -> type a query -> *Run connector test*.
  Each source shows Status / Records / Error, so you see exactly which one works.
- **Command line:** `python -m pharmadrone.test_connectors "apixaban food effect"`

If a source fails you get a plain-English reason (bad key, timeout, rate limit,
endpoint changed) — **never a silent empty result** (point 7).

---

## Errors are shown, not hidden (point 7)

Every connector returns a status. During a run, any source failure is:
1. printed live in the progress log,
2. counted in the coverage summary ("Failed" column), and
3. listed verbatim in a warning panel after the run.

A single source failing does **not** crash the run — the other sources continue.

---

## Scoring: a 0–100 Opportunity Score (point 8)

I switched from the skill's /30 rubric to a **100-point Opportunity Score** so it
matches this project's spec. The spec's dimensions map to points like this:

| Dimension | Max |
|---|---:|
| Scientific evidence strength | 20 |
| Technology (seller) fit | 20 |
| Commercial trigger / timing | 15 |
| Evidence quality / diversity | 15 |
| Company accessibility | 15 |
| Regional relevance | 10 |
| Novelty | 5 |
| **Positive subtotal** | **100** |
| Red-flag penalty | -15 |
| Duplication penalty | -10 |

**Grades:** A >= 70 (actionable) · B 50–69 (validate) · C 30–49 (evidence anchor)
· D < 30 (rejected).

Guardrails carried over from the /30 skill so strong science alone can't inflate a
lead: class-level-only evidence with no active trigger is capped at 60; poor buyer
accessibility is capped at 73; a single source type is capped at 60.

---

## Every report includes (point 9)

Evidence links · source type · **source language** · confidence score · red flags
· "Why This May Be Wrong" · outreach angle — plus the full 12-section structure
(`templates/report_template.md`). Evidence table columns:
`Source Type | Source Language | Title | ID | Link | Supports | Does not prove`.

---

## Source coverage summary (point 10)

After every run you get a table (dashboard + `reports/source_coverage.json`)
showing, per source — ClinicalTrials.gov, openFDA, Europe PMC, OpenAlex, Crossref,
Web/Tavily — how many evidence items it returned, how many **accepted leads cite
it**, how many queries ran, and how many failed.

This is **global public-source scouting, not complete global regulator coverage.**

---


---

## Phase 1 Opportunity Matcher

Tab **2 Opportunity Matcher** adds two deterministic modes:

- **Problem → Solution Match**: e.g. `dissolution failure` → evidence-backed
  product/problem leads, likely solution types, possible partner categories,
  confidence, lead status, and a safe BD action.
- **Technology → Target Match**: e.g. `particle engineering technology` → stored
  product/company leads where the current evidence suggests potential relevance.

BD workflow helpers in the matcher tab:

- Short lead titles use `Company — Short product name`; long NDC/package/recall
  descriptions stay inside expandable details.
- Each matched lead shows match strength, problem category, score, grade, lead
  status, source type, last generated date, and the reason for the match.
- Each matched lead can open the already stored full report; reports are not
  regenerated from the matcher.
- Current matcher results can be downloaded as a CSV lead list.

Important discipline:

- The matcher only uses stored opportunities/evidence from previous Generate runs.
- It does not call new sources, LLMs, or live worldwide search; use Generate/Refresh to add new signals.
- Results are labelled as **Direct match**, **Strong related match**,
  **Weak/background match**, or **Background dosage-form descriptor only**.
- Default display shows Direct and Strong related matches only; descriptor-only and
  weak/background matches require the explicit “Include related/weak matches” checkbox.
- For dissolution searches, dosage-form descriptors such as extended release or
  immediate release are not evidence of failure by themselves. Generic words such
  as release, OOS, specification, QC, batch release, or press release are not
  enough to create a default match.
- Technology matches use language such as “may fit”, “potential relevance”, and
  “requires validation”.
- It must not be read as proof that a company needs a specific technology.

## What v1 does NOT do yet

- No 100-report run. Milestone 1 = 5 reports.
- No national regulators yet. See `ROADMAP.md` — next up is **EMA/EPARs**, then
  **FDA Orange Book**, then PMDA, then NMPA, then the rest. Global reach today
  comes from Tavily multilingual queries + region tagging.

---

## Honest limitation

The connectors are coded against each API's documented shape but were **not run
against the live endpoints in the build environment** (it had no internet). They
fail soft and report errors clearly, so first run is the real test — that's what
the connector self-test and the 5-report milestone are for. If a field looks off,
each connector is ~30 lines and isolated.

---

## Cost

Live estimate shows in the dashboard cost breakdown. Tune
`pricing_usd_per_million_tokens` in the config to current rates. Cheaper levers:
`--basic-queries`, fewer active regions/signals, lower `per_source` in
`retrieve.py`. The 5-report test is designed to cost cents.

---

## File map

```
app.py                          Streamlit dashboard (Generate / Opportunity Matcher / Profile / Results / Connectors)
config/technology_profile.yaml  Seller profile, signals, regions, sources, budget
.env.example                    Keys (copy to .env)
templates/report_template.md    The 12-section report structure
ROADMAP.md                      Connector priority order
FAILURE_SIGNAL_LAYER.md         Embedded failure/rescue intelligence (see below)
pharmadrone/
  settings.py cost.py db.py llm.py
  test_connectors.py            per-source self-test (CLI + dashboard)
  connectors/  clinicaltrials openfda europepmc openalex crossref tavily_search  (+ base)
  pipeline/    queries retrieve extract dedup score report opportunity_matcher
  export.py    md/html/csv/json + static site
  run.py       orchestrator + CLI + coverage summary
reports/                        generated output (created on first run)
```

## Reliability patch notes — source health and deterministic mode

- If the LLM provider is rate-limited or the 429 circuit breaker trips, the UI now states: **LLM unavailable/rate-limited; deterministic evidence mode used.** The deterministic fallback remains active; no candidate is fabricated to compensate for LLM failure.
- Tavily web enrichment now uses a short timeout and a one-time sanitised retry when the API rejects advanced query syntax such as `site:` operators. Failed Tavily calls are logged and surfaced in Source Coverage; they do not block the run.
- Source Coverage now distinguishes **available**, **no hits found**, **search skipped**, **API failed / unavailable**, and **partial — some API failures**.
- Root-cause corroboration debug now separates API failures, no-hit searches, retrieved-but-rejected hits, and attached corroboration sources.
- Matcher cards show the stored **Opportunity Score** used for app ranking. Report sections may separately show a **Root-Cause/Solution-Fit overall** score.

## Phase 2 — opportunity index and novelty queue

Phase 2 adds a lightweight local **opportunity index** so PharmaTune can remember
which evidence-backed opportunity signals have already been found.

What changed:

- Each indexed opportunity record gets a stable lead ID generated from company,
  product/molecule, problem category, source type, source ID, and region.
- The local SQLite store now includes `opportunity_index` and
  `opportunity_run_summary` tables.
- Valid deduplicated candidates are indexed even when only the top few reports
  are generated.
- Candidates skipped by the pre-scoring cap are stored as a waiting queue.
- Repeated leads are not duplicated; `last_seen_at`, `last_checked_at`, evidence
  hash, score, grade, and lead status are compared for update detection.
- Results and matcher cards show first found, last checked, last updated,
  source freshness, novelty status, queue status, and whether a full report is
  available.
- Opportunity Matcher searches the indexed PharmaTune evidence, not only the
  current run's generated reports.
- `opportunity_index.csv` is exported with the normal report files.

MVP limitation: this uses local SQLite persistence, which is suitable for a
private Streamlit prototype but not production SaaS persistence. On free hosted
apps, disk may be ephemeral; download exports during the session.

## Phase 3A — Source reliability and evidence enrichment

Phase 3A adds a minimal, additive source reliability and enrichment layer. It does not add new global source expansion, accounts, billing, CRM, outreach automation, or an AI chat co-pilot.

New local MVP capabilities:

- `source_health_events` tracks source/API status for developer/debug review: available, failed, rate-limited, rejected, no results, or skipped.
- `opportunity_enrichment` stores deterministic enrichment metadata for indexed opportunity records.
- Evidence quality is labelled separately from the Opportunity Score using Tier 1–4 source quality.
- Indexed leads can be enriched in a capped queue without rerunning discovery.
- Normal user-facing reports continue to show clean evidence gaps rather than raw Tavily/API errors.

SQLite remains acceptable for this local/Streamlit MVP. It should not be treated as production SaaS persistence.

## Phase 3B — Official Source Expansion Pack 1

Phase 3B adds a small official/context enrichment layer on top of the existing
indexed PharmaTune evidence. It enriches existing opportunity records only; it
does not rerun discovery, change stable lead IDs, change Opportunity Score, or
require an LLM.

The enrichment queue can now add clean metadata for FDA official follow-up
searches, FDA drug-label/product context, ClinicalTrials.gov trial context, and
relevant literature context from the existing literature connectors. FDA labels
and general literature are treated as context only and must never be interpreted
as product-specific root-cause proof.

## Phase 3C — Seller-to-Target Opportunity Workflow

Phase 3C adds a seller-facing workflow inside the Technology Profile tab. A user can describe a technology/service provider profile, select capability categories such as particle engineering, solubility enhancement, formulation CDMO, analytical/QC testing, or sterile manufacturing support, and match that profile against the existing indexed PharmaTune opportunity records.

This workflow is deterministic and read-only. It uses the local `opportunity_index` plus enrichment metadata only. It does not call external APIs, rerun discovery, regenerate reports, require an LLM, change stable lead IDs, or modify the stored Opportunity Score. Seller Fit Strength is displayed separately as a capability-match label: Strong fit, Moderate fit, or Weak/background fit. It reflects technical/capability fit only, not commercial readiness.

Seller-to-target cards preserve evidence discipline: public evidence may indicate a possible fit, but it does not prove that a target company needs the seller's technology, does not confirm product-specific root cause unless directly stated, and does not convert label or literature context into defect evidence.

## Checkpoint 3: 20-company pilot case study

The Results & Export tab can build a configurable, deterministic pilot set of up to 20 existing indexed/enriched opportunity records. Users can edit the case-study title, objective, seller/service profile, capabilities, problem signals, region, evidence threshold, monitor/preview filters, and target limit. The pilot uses the current opportunity index and seller-to-target matching only; it makes no API or LLM calls, does not modify Opportunity Scores or stable lead IDs, and exports both CSV and Markdown summary files with the selected lens, filters, and explicit evidence/readiness limitations.

## Checkpoint 4: 100-target validation study

The Results & Export tab can build an internal, audit-ready validation set of up
to 100 existing indexed/enriched opportunities. The workflow is configurable by
seller/service profile, capabilities, problem signals, region, evidence quality,
monitor/preview/low-priority filters, full-report and enrichment requirements,
and unique-company preference.

Checkpoint 4 is read-only: it calls no APIs or LLMs, invents no records, and does
not change Opportunity Scores, stable lead IDs, discovery, queues, enrichment,
or reports. The CSV includes blank manual-audit fields and stored official-source
URLs where available. Low priority / archive lead classifications are included by
default for internal validation, while records actually hidden/rejected in the
workflow remain excluded.

## Checkpoint 5A — bounded discovery-depth expansion

Failure/Rescue Generate now deepens the existing official structured sources without changing scoring or report caps:

- FDA recall events are paginated by explicit quality-problem category and deduplicated by `recall_number`.
- ClinicalTrials.gov stopped studies are paginated by NCT ID and must contain a usable medicinal intervention; specimen/control-only records are excluded.
- FDA Drug Shortages adds cautious supply, availability, manufacturing, and discontinuation signals. A shortage is not automatically treated as a formulation failure or confirmed root cause.
- All discovered valid candidates are indexed before the existing report-generation cap. Full reports remain limited by `MAX_REPORTS_PER_RUN`.

The default source caps are documented in `.env.example`. They can be reduced for constrained deployments, but should not be increased without checking API limits and manual validation precision.

## Checkpoint 5A.1 — corrected discovery-depth diagnostics

Checkpoint 5A.1 fixes the gap between retrieved source evidence and the source
counts shown to users. Source coverage now reports separate counts for raw API
results, evidence surviving connector gates, candidates created/rejected,
indexed leads, and generated full reports. A source can therefore contribute
indexed queue records even when none of its records appears in the top five
reports.

The FDA recall connector now uses bounded atomic taxonomy queries plus a bounded
recent-recall sweep when exact problem queries are sparse. Results are merged by
FDA recall number, while distinct official recall events remain separate. The
ClinicalTrials.gov connector retains only usable medicinal interventional
records and records explicit exclusion reasons for specimen, placebo-only,
standard-of-care, and diagnostic/control records. FDA shortages remain cautious
supply, availability, manufacturing-quality, or discontinuation signals and do
not establish a formulation root cause or customer need.

## Checkpoint 5A.3 source-depth diagnostics

The two primary Generate buttons now use the same bounded official-source expansion settings. Developer/debug output shows the effective recall/trial/source caps, recall taxonomy and fallback-sweep stages, shortage endpoint totals, source rejections, candidate creation, indexing, and full-report counts separately. Checkpoint 4 also reports official direct-source availability separately from enrichment/evidence-quality review status.


## Checkpoint 5A.4 live wiring diagnostic

The dashboard displays `Checkpoint 5A.4 active`, confirms both primary Generate modes use the same expanded official-source discovery function, reports configured versus attempted recall taxonomy calls and fallback sweep calls, and counts official direct-source validation records independently from enrichment status.

## Checkpoint 6A — precision and external eligibility

Checkpoint 6A adds deterministic, read/export-time precision annotations without changing Opportunity Scores, stable lead IDs, discovery, queues, enrichment, or report generation. The opportunity index and 100-target validation export now include Signal Tiers A–D, broad and specific problem categories, company-role and product-type warnings, source-ID verification diagnostics, and a stricter `external_case_study_eligible` flag. Raw indexed records remain available for internal audit; only verified, suitable, specific A/strong-B signals with acceptable company/product mapping and seller fit are eligible for external case-study use.

## Checkpoint 6A.2 — final live-regression correction

Checkpoint 6A.2 tightens only deterministic precision annotations. ClinicalTrials.gov Tier A classification now inspects stored official titles, summaries, intervention descriptions, arms and outcomes rather than discovery-query context. FDA broad/specific taxonomy is derived from the structured recall reason first. Bulk API/raw-material warnings are limited to actual product descriptors, and company/manufacturer role conflicts remain visible. Opportunity Scores, stable lead IDs, discovery, queue, enrichment and report logic are unchanged.


## Checkpoint 6A.3

ClinicalTrials Tier A now requires a coded, attributable structured-registry evidence trace. Company-role diagnostics distinguish identity mismatches from normal sponsor/contract-manufacturer relationships, and audited source-ID corrections are applied deterministically in validation/export paths.


## Checkpoint 6A.4

ClinicalTrials Tier-A traces now export the actual sentence or match-centred context supporting the signal code, with code-specific validation and deterministic de-duplication. Multi-formulation registry titles such as Asasantin ER are recognised, source-ID audit corrections preserve the D-0202-2025 attribution warning, and company roles are bound to exact named entities so Actavis Elizabeth LLC is not confused with Actavis, Inc. Opportunity Scores, stable lead IDs, discovery, queues, enrichment and reports are unchanged.

## Checkpoint 6A.5

- Enforces signal-code-specific ClinicalTrials evidence invariants.
- Restores explicit delivery-optimisation signals when a documented delivery limitation and named formulation/matrix/device/route solution are both present.
- Requires formulation-comparison evidence to visibly identify multiple or compared formulations rather than a single isolated test/prototype formulation.
- Uses exact entity-aware manufacturer/distributor binding for validation diagnostics.

