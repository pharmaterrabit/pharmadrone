# Failure Signal Intelligence Layer

An **embedded** capability inside the PharmaDrone Opportunity Engine — not a
standalone app, product, or failed-drug database. It strengthens the existing
problem-first workflow by detecting **failure-based rescue opportunities**
(discontinued, recalled, withdrawn, terminated, reformulated, on-hold products)
that show a formulation / CMC / physical-form / packaging / delivery / quality /
manufacturing weakness, then explains the evidence, the technical issue, the
rescue angle, and the BD action.

## Where it plugs in

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
