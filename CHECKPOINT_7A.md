# Checkpoint 7A — Real Seller-Specific Case Study

Status: deployment candidate — locally implemented on 14 July 2026; live validation and first human-approved customer export remain open.

## Outcome

PharmaTune now includes a durable, real-provider case-study workflow based on Hovione's publicly documented capabilities. The workflow is no longer a generic editable seller demo.

It implements the complete governed sequence:

Hovione provider profile → capability evidence → matched public product problems → evidence review → human validation → approved shortlist → customer-safe export.

## Verified provider profile

The Hovione profile is supported by Hovione's official pages covering:

- particle-engineering development and scale-up;
- formulation development and early clinical supplies;
- solubility and bioavailability technologies;
- dissolution and release testing;
- analytical and QC support;
- physical stability and ICH stability studies;
- related-substances and impurity characterization.

The verification date and source URLs are displayed in the product and included in the customer case study.

## Governance

- Candidate matching remains deterministic and uses stored PharmaTune evidence only.
- The workflow uses the frozen human-validation dataset so every candidate has an audit key.
- A candidate is customer-safe only when deterministic `external_case_study_eligible`, human `external_use_approved` and `external_gate_passed` are all true in the latest append-only audit version.
- Internal review CSV includes candidates still awaiting validation.
- Customer Markdown and HTML exports contain approved rows only.
- Reviewer names, unapproved candidates and internal workflow details are excluded from customer exports.
- Public signals do not prove customer need, buying intent, urgency, budget, root cause or solution fit.

## Persistence

Migration 7 adds:

- `seller_profiles`;
- `seller_case_studies`;
- `seller_case_study_targets`.

Every build creates an immutable saved snapshot with its provider profile, workflow status, target records and approval state.

## Live completion gate

Checkpoint 7A becomes stable after:

1. deployment applies migration 7 successfully;
2. the Hovione case-study page builds against the production validation queue;
3. at least one suitable target is manually reviewed and approved for external case-study use;
4. the rebuilt customer Markdown and HTML exports contain only the approved target(s);
5. the production database retains the saved case-study snapshot.
