# Phase 9 — Patent & Lifecycle Intelligence

Phase 9 converts the already-ingested official FDA Orange Book dataset into a fast, stored lifecycle workspace. Streamlit reads the projection from PostgreSQL; it does not download or parse FDA files during page navigation.

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

## Production activation

After deployment, run `fda_orange_book` and then `patent_lifecycle`, or run the bootstrap workflow. The first projection applies schema migration 10 and populates the workspace from stored Orange Book records. If the FDA archive is temporarily unavailable, PharmaTune retains product fallback records but explicitly leaves lifecycle evidence unavailable.
