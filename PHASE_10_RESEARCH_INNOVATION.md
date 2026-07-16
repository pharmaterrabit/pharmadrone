# Phase 10 — Research & Innovation Intelligence

Phase 10 turns retained scholarly and clinical-registry evidence into a governed, read-optimised Research & Innovation workspace. Europe PMC, OpenAlex and Crossref refresh outside Streamlit; the page reads the stored PostgreSQL projection so normal navigation does not call external APIs.

## 10A — Research organisations, authors and publications

- Retains DOI, PMID, PMCID, OpenAlex identifiers, title, journal, publication type/date, abstract, citation count and open-access status.
- Deduplicates publications across scholarly sources using DOI first, followed by other authoritative identifiers.
- Retains structured OpenAlex institution identity, ROR identity, country, organisation type and official homepage where supplied.
- Retains authors, ORCID/OpenAlex profiles and source-published affiliations.
- States explicitly that publication authorship or affiliation is not proof of current employment, decision authority or technology-transfer responsibility.

## 10B — Scientific relationships and technology transfer

- Creates a publication co-authorship relationship only when two structured institutions are attached to the same publication.
- Creates a clinical research collaboration only when ClinicalTrials.gov explicitly lists a lead sponsor and collaborator.
- Keeps the relationship boundary visible: co-authorship is not silently converted into a formal/commercial partnership, and a registry collaboration does not establish contractual terms.
- Creates a technology-transfer record only from retained `technology_transfer` or `university_technology` evidence with an official URL.
- Does not convert a scientific publication into a licensable technology. Missing transfer evidence remains visibly unresolved.

## 10C — Weekly monitoring

- Runs `research_innovation` weekly after Europe PMC, OpenAlex and Crossref.
- Rebuilds current publication, author, organisation, relationship and transfer projections from active retained evidence.
- Preserves hash-deduplicated, append-only organisation observations and monitor-run telemetry.
- Reports organisations without a verified technology-transfer inventory instead of pretending that all research is available to license.

## 10D — Research & Innovation workspace

- Replaces the former placeholder with four dedicated views: Research organisations, Publications, Scientific relationships and Technology transfer.
- Adds search and country filters, evidence-linked metrics, official links and separate CSV exports.
- Adds organisation detail pages containing publications, published authors, scientific relationships, verified transfer records and observation history.
- Uses cached, bounded database reads to preserve fast warm-page navigation.

## Source and claim boundaries

Europe PMC, OpenAlex and Crossref are scholarly metadata sources. ClinicalTrials.gov is a public trial registry. These sources can support publication, affiliation, sponsor and collaborator facts but do not by themselves prove technology ownership, commercial availability, licensing terms, partner intent or buying intent. PharmaTune exposes those gaps for human validation.

## Production activation

After deployment, the bootstrap workflow runs `europepmc`, `openalex` and `crossref`, followed by `research_innovation`. Schema migration 11 is applied automatically. Technology-transfer inventory remains empty until an official transfer record is retained; this is an evidence gate, not a system failure.
