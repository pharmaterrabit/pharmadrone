# Phase 9 — Patent & Lifecycle Intelligence

Phase 9 converts official FDA, EPO and UK patent evidence into a fast, stored lifecycle workspace. Streamlit reads the PostgreSQL projection; it does not download or parse external patent services during page navigation.

## 9A — Orange Book product and application lifecycle

- Projects the FDA application number, product number, trade name, ingredient, dosage/route, strength, approval date, application type, RLD, RS, therapeutic-equivalence code and market category.
- Keeps the FDA application holder in a dedicated field.
- Links every profile to the retained official FDA evidence URL and records the dataset mode.
- Fails visibly when the official archive is unavailable and the Drugs@FDA product fallback contains no patent or exclusivity data.

## 9B — Listed patents, exclusivity, ownership and families

- Stores Orange Book listed patent numbers, listed expiry dates, drug-substance/product flags, use codes, delist requests and submission dates.
- Stores FDA regulatory exclusivity codes and expiry dates separately from patents.
- Does **not** infer patent ownership from the FDA application holder. The holder is retained only as product/application context.
- Does **not** invent patent families. Every unresolved patent is placed in a family-resolution queue with an official Espacenet investigation link; a family ID can be treated as verified only when patent-office evidence supports it.

## 9C — Expiry timelines and weekly monitoring

- Classifies stored records as `Expiry within 24 months`, `Unexpired listed protection`, `No unexpired listed protection`, or `Lifecycle evidence unavailable`.
- Builds approval, patent-expiry and exclusivity-expiry timelines from stored official facts.
- Runs the `patent_lifecycle` projection weekly after the Orange Book refresh path.
- Preserves append-only, hash-deduplicated lifecycle observations and a monitor-run audit trail.

These classifications describe regulatory lifecycle context. They are not findings of patent validity, enforceability, ownership, freedom to operate, generic-entry eligibility or commercial demand.

## 9D — Patent & Lifecycle workspace

- Replaces the former Patents placeholder with a dedicated searchable and filterable workspace.
- Shows product, application holder, RLD/RS, listed-patent and exclusivity counts, next listed expiry and evidence status.
- Provides detailed FDA facts, lifecycle timeline, patent/exclusivity tables, official evidence links, family investigation routes and observation history.
- Exports the filtered lifecycle directory to CSV.

## 9E — EPO, UK and Google Patents coverage

- Uses the official EPO Open Patent Services OAuth interface for bounded weekly EP and GB publication searches. OPS records are stored before the UI reads them.
- Stores patent documents, officially reported applicants and inventors, patent-family members, legal events and evidence-governed product links in separate tables.
- Links GB records to the official UK patent search/register route. UK register evidence is authoritative for UK ownership and register changes; missing fields remain missing.
- Adds Google Patents document links to every retained patent as a discovery and cross-check route. Google is not treated as patent-office authority and is never used alone to assert ownership, legal status, expiry, enforceability or product coverage.
- Keeps EPO/UK search context separate from verified product-patent links. A keyword match does not establish that a patent protects a product.
- Runs `epo_ops_patents` before the weekly `patent_lifecycle` projection whenever `EPO_OPS_KEY` and `EPO_OPS_SECRET` are configured.

## Production activation

After deployment, run `fda_orange_book`, `epo_ops_patents`, and then `patent_lifecycle`, or allow the scheduled workflow to run due sources. Migration 14 adds the global model. EPO OPS requires free developer credentials stored as GitHub secrets `EPO_OPS_KEY` and `EPO_OPS_SECRET`. Without them, Orange Book and Google discovery links still work, while EP/GB ingestion remains visibly unpopulated rather than fabricated.
