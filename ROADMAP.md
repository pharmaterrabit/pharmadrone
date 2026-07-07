# Connector roadmap

v1 ships the reliable free-JSON core + Tavily. Add regulators **one at a time**,
in this order (robust output first, not 15 brittle scrapers):

| Order | Source | Notes |
|------:|--------|-------|
| ✅ v1 | ClinicalTrials.gov v2, openFDA, Europe PMC, OpenAlex, Crossref, Tavily | live |
| 1 | **EMA / EPARs** | EU medicine data downloads + EPAR links |
| 2 | **FDA Orange Book** | downloadable dataset — patents/exclusivity/lifecycle timing |
| 3 | PMDA (Japan) | advanced module |
| 4 | NMPA (China) | advanced module |
| 5 | SFDA / TGA / ANVISA / India CDSCO / Korea MFDS / Russia GRLS | later |

**How to add one:** create `pharmadrone/connectors/<name>.py` exposing
`search(term, max_results) -> ConnectorResult` (see any existing connector),
add it to `STRUCTURED` and `SOURCE_LABELS` in `pipeline/retrieve.py`, and to
`CHECKS` in `test_connectors.py`. Register it under `sources:` in
`config/technology_profile.yaml`. That's it — coverage summary and error
reporting pick it up automatically.

**Global coverage note:** v1 reaches non-US/EU markets through Tavily
multilingual search + region tagging. This is deliberately labelled
**"global public-source scouting," not complete global regulator coverage.**
