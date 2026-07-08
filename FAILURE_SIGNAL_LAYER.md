# Failure Signal Intelligence Layer

An **embedded** capability inside the PharmaDrone Opportunity Engine — not a
standalone app, product, or failed-drug database. It strengthens the existing
problem-first workflow by detecting **failure-based rescue opportunities**
(discontinued, recalled, withdrawn, terminated, reformulated, on-hold products)
that show a formulation / CMC / physical-form / packaging / delivery / quality /
manufacturing weakness, then explains the evidence, the technical issue, the
rescue angle, and the BD action.

## Fix: candidates no longer depend solely on the LLM succeeding

A live run retrieved 84 evidence items but produced 0 reports — the LLM
extraction step was silently swallowing every failure (`except Exception:
continue`, no logging), which is fatal with a free/flaky OpenRouter model. This
is now fixed at the architecture level, not patched at the symptom:

1. **Candidate discovery (`pipeline/discover.py`) always runs first**, using
   the structured `entities` connectors already extract (trial sponsor, recall
   firm, label brand/generic, `whyStopped`) — **no LLM call required**.
2. **LLM extraction runs as an enrichment layer on top.** Its batch failures
   are now captured and surfaced (dashboard Debug panel + CLI log), never
   silently dropped.
3. **Guaranteed fallback**: if, after both steps, the combined candidate count
   is still below 3 and raw evidence ≥ 20, `discover.build_fallback_candidates()`
   forces 3-5 provisional candidates from the strongest evidence clusters —
   including a last-resort tier that groups evidence by source+region when no
   entity or title can be identified at all. Every provisional candidate is
   deterministically scored and its report body is written **without calling
   the LLM**, so the guarantee holds even if the model is completely down.
4. **Targeted failure queries** replace the old generic phrasing — e.g.
   `"terminated trial" "poor solubility"`, `"complete response letter" "CMC
   deficiencies"`, `"recall" "dissolution" tablet` — routed as quoted phrases to
   Tavily and as plain terms to structured APIs.
5. A new **"Generate 5 Failure/Rescue Opportunity Reports"** mode biases every
   query toward recalls, terminations, withdrawals, and CMC/formulation/quality
   signals, and ranks results by Failure/Rescue Signal Strength first.
6. A **Debug panel** (dashboard tab ① after a run, or `reports/debug_report.json`)
   shows: raw evidence count, deterministic vs LLM-extracted candidate counts,
   candidates after dedup, fallback-generated count, rejection reasons broken
   down (low evidence / grade D), any LLM batch errors verbatim, and the top 10
   product/company names seen before scoring.

Net effect: **a healthy evidence haul (≥20 items) can no longer silently end in
zero reports.** If evidence is genuinely weak, you now get weak/provisional
reports with a loud banner and `needs_verification` labelling — never silence.

## Event-first discovery (finds real targets, not generic literature)

Earlier, failure discovery fed generic phrases ("terminated trial poor
solubility …") into every connector — which returned **0** records from
openFDA/ClinicalTrials.gov (recalls and trials don't contain those phrases), so
only literature APIs produced evidence and everything was correctly rejected as
generic → 0 reports. Fixed by querying the structured event sources the way they
are actually indexed, BEFORE any literature:

- **openFDA Enforcement** — `discover_events()` searches the recall **reason**
  field for concrete quality terms: dissolution failure, stability, impurity,
  degradation, sterility, particulate matter, contamination, failed
  specifications, subpotent/superpotent, packaging defect, labeling mix-up,
  container closure, leakage, crystallization, precipitation, failed release
  testing, cGMP, manufacturing defect.
- **ClinicalTrials.gov** — `discover_stopped()` filters by
  `overallStatus = TERMINATED | WITHDRAWN | SUSPENDED | NO_LONGER_AVAILABLE`,
  then parses `whyStopped`, sponsor, intervention, condition, phase, and drug
  name. A candidate is created only when a specific trial/drug/sponsor exists.
- **Tavily** — source-targeted queries (`site:fda.gov recall dissolution tablet`,
  `site:ema.europa.eu withdrawn application quality CMC`, `site:tga.gov.au recall
  medicine stability`, `discontinued development bioavailability company press
  release`, `complete response letter CMC deficiencies drug`, …), region-gated
  to the active regulators. No generic literature searches.

**Source priority** (regulatory recall/enforcement > trial status/whyStopped >
company/investor > pharma news > academic) is applied to ranking. Academic papers
are gathered only by the normal path and are used for mechanism context after a
target exists — they can never dominate Failure/Rescue mode.

**Minimum event-source requirement:** in Failure/Rescue mode at least one
candidate must rest on a regulatory recall, a stopped trial, or a company/news
event source; otherwise the run says so instead of emitting literature-only
reports.

**429 handling:** the LLM client uses 429-aware exponential backoff, an optional
`LLM_FALLBACK_MODEL` tried once on persistent failure, and — critically — the
whole event-first + scoring path is deterministic, so a fully rate-limited LLM
still yields correct reports from the structured event records (verified in the
benchmark with every LLM call forced to 429).

**Benchmark:** `python -m pharmadrone.benchmark --offline` (fixtures, no network)
or `python -m pharmadrone.benchmark` (live) checks all five classes — a recall /
quality issue, a terminated/withdrawn trial, a regulatory rejection/withdrawal, a
company-discontinued programme, and a formulation/CMC signal.

## Quality gates (no fabricated targets or events)

Deterministic discovery is now strictly gated so it can never emit a
"None — prodrug / terminated" style report:

- **Valid-target gate.** A candidate is only created when the evidence contains
  a real target: a specific product/molecule name, a company/sponsor/
  manufacturer, a trial ID, or a recall/enforcement product. Generic scientific
  terms are blacklisted (prodrug, treatment, review, narrative review,
  therapeutic targets, pharmacological mechanisms, emerging approaches,
  off-label, patient, disease, formulation, bioavailability, …) and never count
  as a target on their own.
- **Cluster classes.** Every cluster is classified `valid_bd_opportunity`,
  `weak_academic_cluster`, or `rejected_generic_literature`. **Only
  `valid_bd_opportunity` becomes a report.** The other two are counted in the
  debug panel and discarded. If nothing is valid, the run says *"Generic
  literature cluster found, no BD opportunity generated"* — an honest zero, not
  a fabricated report.
- **Confirmed-event-only failure labelling.** A signal is labelled terminated/
  withdrawn/recalled/discontinued ONLY when a source structurally proves it: a
  recall/enforcement record, a trial's stopped-status/`whyStopped`, or a
  regulatory/company source stating the event. An academic paper that merely
  *mentions* the word "terminated" never triggers a failure label.
- **Failure-section evidence gate.** The Failure Signal Intelligence section
  requires a confirmed event AND at least one BD-grade source (regulatory /
  company / trial / recall). With academic literature only, it renders
  *"No confirmed failure event found — mechanistic/academic relevance only, not
  a confirmed rescue opportunity"* and shows the papers as technical background.
- **Evidence dedup.** Evidence is de-duplicated by URL, DOI/PMID/PMCID/record ID,
  and normalised title, so the same paper can't appear multiple times in one
  Evidence Table.
- **Conservative fallback.** On LLM failure (e.g. OpenRouter 429), the fallback
  produces provisional candidates ONLY from valid-target clusters. If none
  exist, it produces nothing — it never invents a target to hit a quota. A single
  authoritative source (one FDA recall, or a trial with a confirmed stopped
  status) satisfies the evidence floor and is scored deterministically, so a 429
  during scoring can't silently discard a legitimate regulatory candidate.
- **Debug transparency.** For every accepted candidate the debug panel shows its
  valid-target type, whether the event is confirmed, source diversity, whether
  it has regulatory/company/trial evidence, and why it isn't a generic academic
  cluster; plus a table of discarded clusters with the rejection reason.



| Integration point | Implementation |
|---|---|
| Extra evidence-source layer | `connectors/openfda_enforcement.py` (recalls, regulatory) + `whyStopped` capture in `connectors/clinicaltrials.py` + failure-oriented Tavily queries |
| Extra opportunity-signal category | `pipeline/failure_signal.py` → `build_failure_queries()` + `extract_failure_signals()` |
| Extra scoring dimension | `pipeline/failure_signal.py` → `rescue_strength()` / `apply_failure_scoring()` (Failure / Rescue Signal Strength) |
| New section in the report | `render_failure_section()` appended by `pipeline/report.py` |
| BD insight layer | rescue strategy + partner categories + next action fields in the schema |

Everything runs inside the normal `generate()` pipeline. No separate UI.

## Source tagging + evidence priority

Every evidence record carries a `source_category`:
**regulatory · company · trial · publication · conference · news · patent**
(web hits are upgraded to *regulatory* or *company* by domain). Evidence is
ranked, and the report lists it, in this priority order:

> regulatory > company/investor > trial registry > peer-reviewed publication >
> conference > news

Each extracted signal also gets a **signal status**: `confirmed` / `indirect` /
`weak` / `needs_verification`.

## Scoring: Failure / Rescue Signal Strength

- **High** (+12): a regulatory **or** company source confirms a formulation / CMC /
  manufacturing / quality / packaging / delivery / stability issue.
- **Medium** (+7): trial registry / company / investor / multiple indirect sources
  suggest a relevant technical or commercial issue.
- **Low** (+3): only academic or news suggests a possible issue, unconfirmed.
- **Reject/flag** (0): reason unclear, unrelated to formulation/CMC/delivery/quality,
  or speculation only → a red flag is added.

The bonus nudges ranking but is **capped and re-clamped to 0–100**, and the base
evidence-quality guardrails still apply — so a weak-evidence lead can't be inflated
by rescue strength alone.

## Extraction schema (22 fields)

product · molecule · dosage_form · route · company · region · regulatory_body ·
stage · event_type · event_date · failure_reason · problem_category ·
signal_status · confirmed_fact · interpretation · why_scientific · why_commercial ·
rescue_strategy · bd_opportunity · potential_partners · next_action · red_flags
(+ linked evidence with source_category and links).

## Evidence discipline (enforced in the prompt)

Never invents a failure reason. Never claims a formulation/CMC failure unless a
source supports it. Sets `problem_category = null` and status `needs_verification`
when the reason is unclear. Separates confirmed fact from interpretation. Academic
papers support **mechanism only**, never a business conclusion. Regulatory sources
outrank news.

## Milestone 1 — running the 5–10 example test set

Because this environment has no internet, the example set must be generated on
your deployed instance (or laptop), where the connectors can reach live data —
fabricating examples would break the layer's own no-invention rule.

1. Deploy / run as usual (`streamlit run app.py`).
2. Tab **④ Connectors** → confirm **openFDA (Enforcement/Recalls)** returns rows.
3. Tab **① Generate → Generate 5 Test Reports.** Failure queries run automatically.
4. In each report, the **Failure Signal Intelligence** section shows: what happened,
   evidence (priority-ordered with links), problem classification, evidence
   strength, confirmed-vs-interpretation, scientific + commercial relevance,
   rescue strategy, partner categories, next action, and red flags.
5. `opportunities.csv` now includes a `failure_rescue_strength` column.

The section renders deterministically from extracted, evidence-linked fields, so
what you read maps back to real sources — not free-text invention.

## What was NOT added (per milestone)

Direct regulator connectors (EMA/EPARs refusals, MHRA alerts, PMDA, NMPA, SFDA,
TGA…) are **not** built yet. Milestone 1 uses openFDA enforcement + CT.gov
`whyStopped` + failure-oriented web discovery. Add dedicated regulator connectors
one at a time afterwards (see `ROADMAP.md`), each returning the standard
`ConnectorResult` with `source_category="regulatory"`.
