from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from pharmadrone import db
from pharmadrone.connectors import clinicaltrials, crossref, europepmc, openalex
from pharmadrone.pipeline import research_innovation
from pharmadrone.scheduler import config, repository


def _ingest(conn, records):
    with conn.transaction():
        repository.ingest_source_records(conn, run_id="phase10-source", source_name="phase10-test", records=records)


def _records():
    paper = {
        "source_type": "paper", "source_name": "OpenAlex", "record_id": "10.1000/example",
        "title": "Advanced formulation research", "url": "https://doi.org/10.1000/example",
        "raw_text": "Advanced formulation and drug delivery research.",
        "entities": {
            "doi": "10.1000/example", "publication_title": "Advanced formulation research",
            "publication_year": 2026, "journal": "Journal of Formulation", "citation_count": 12,
            "open_access": True,
            "institutions": [
                {"name": "University of Oxford", "openalex_id": "https://openalex.org/I1", "ror_id": "https://ror.org/oxford", "country_code": "GB", "organisation_type": "education", "official_url": "https://www.ox.ac.uk"},
                {"name": "University of Cambridge", "openalex_id": "https://openalex.org/I2", "ror_id": "https://ror.org/cambridge", "country_code": "GB", "organisation_type": "education", "official_url": "https://www.cam.ac.uk"},
            ],
            "authors": [
                {"name": "Jane Researcher", "orcid": "0000-0001", "institution_keys": ["https://openalex.org/i1"]},
                {"name": "John Scientist", "openalex_id": "https://openalex.org/A2", "institution_keys": ["https://openalex.org/i2"]},
            ],
        },
    }
    trial = {
        "source_type": "trial", "source_name": "ClinicalTrials.gov", "record_id": "NCT123",
        "title": "Drug delivery study", "url": "https://clinicaltrials.gov/study/NCT123", "raw_text": "",
        "entities": {"sponsor": "Example Pharma", "collaborators": ["Research Institute"]},
    }
    technology = {
        "source_type": "technology_transfer", "source_name": "Oxford TTO", "record_id": "TECH-1",
        "title": "Controlled-release platform", "url": "https://innovation.ox.ac.uk/tech-1", "raw_text": "Available for licensing",
        "entities": {"organisation": "University of Oxford", "technology_title": "Controlled-release platform",
                     "technology_category": "drug delivery", "licensing_status": "Available for licensing",
                     "transfer_contact": "Technology transfer office"},
    }
    return [paper, trial, technology]


def test_phase10_schema_and_weekly_projection_are_installed(tmp_path):
    conn = db.connect(tmp_path / "phase10.db")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {
        "research_organisations", "research_publications", "research_authors",
        "research_publication_authors", "research_organisation_publications",
        "research_partnerships", "research_technologies", "research_monitor_runs",
    }.issubset(tables)
    assert config.source_spec("research_innovation").cadence == "weekly"


def test_openalex_retains_institutions_authors_and_research_metadata():
    payload = {"results": [{
        "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/example", "title": "Example",
        "publication_year": 2026, "publication_date": "2026-01-02", "type": "article", "cited_by_count": 7,
        "abstract_inverted_index": {"Drug": [0], "delivery": [1]}, "open_access": {"is_oa": True, "oa_status": "gold"},
        "primary_location": {"source": {"display_name": "Example Journal"}},
        "authorships": [{"author": {"display_name": "Jane Example", "id": "https://openalex.org/A1", "orcid": "https://orcid.org/0000"},
                         "institutions": [{"display_name": "Example University", "id": "https://openalex.org/I1", "ror": "https://ror.org/1", "country_code": "GB", "type": "education", "homepage_url": "https://example.edu"}]}],
    }]}
    with patch("pharmadrone.connectors.openalex.get_json", return_value=payload):
        record = openalex.search("drug delivery", 1).records[0]
    assert record["entities"]["institutions"][0]["ror_id"] == "https://ror.org/1"
    assert record["entities"]["authors"][0]["name"] == "Jane Example"
    assert record["entities"]["citation_count"] == 7
    assert record["entities"]["open_access"] is True


def test_europepmc_and_crossref_retain_author_affiliation_metadata():
    epmc_payload = {"resultList": {"result": [{
        "pmid": "123", "doi": "10.1000/epmc", "title": "PMC study", "journalTitle": "PMC Journal",
        "pubYear": "2026", "abstractText": "Abstract", "isOpenAccess": "Y", "citedByCount": 3,
        "authorList": {"author": [{"fullName": "Jane Example", "authorAffiliationDetailsList": {"authorAffiliation": [{"affiliation": "Example University"}]}}]},
    }]}}
    crossref_payload = {"message": {"items": [{
        "DOI": "10.1000/crossref", "title": ["Crossref study"], "container-title": ["Crossref Journal"],
        "issued": {"date-parts": [[2026, 2, 3]]}, "is-referenced-by-count": 4,
        "author": [{"given": "John", "family": "Example", "ORCID": "https://orcid.org/0000", "affiliation": [{"name": "Research Institute"}]}],
    }]}}
    with patch("pharmadrone.connectors.europepmc.get_json", return_value=epmc_payload):
        epmc = europepmc.search("formulation", 1).records[0]
    with patch("pharmadrone.connectors.crossref.get_json", return_value=crossref_payload):
        cross = crossref.search("formulation", 1).records[0]
    assert epmc["entities"]["authors"][0]["affiliations"] == ["Example University"]
    assert cross["entities"]["authors"][0]["affiliations"] == ["Research Institute"]
    assert cross["entities"]["publication_date"] == "2026-02-03"


def test_clinicaltrials_retains_explicit_collaborators():
    study = {"protocolSection": {
        "identificationModule": {"nctId": "NCT123", "briefTitle": "Study"},
        "statusModule": {"overallStatus": "COMPLETED"}, "designModule": {"studyType": "INTERVENTIONAL"},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Drug X"}]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Sponsor Pharma"}, "collaborators": [{"name": "Partner University"}]},
    }}
    record, _ = clinicaltrials._row(study, "formulation")
    assert record["entities"]["collaborators"] == ["Partner University"]


def test_projection_builds_governed_research_graph_without_overclaiming(tmp_path):
    conn = db.connect(tmp_path / "projection.db")
    _ingest(conn, _records())
    result = research_innovation.sync(conn, run_id="phase10-week-1", observed_at=datetime(2026, 7, 16, tzinfo=timezone.utc))
    assert result["organisations_seen"] == 4
    assert result["publications_seen"] == 1
    assert result["authors_seen"] == 2
    assert result["partnerships_seen"] == 2
    assert result["technologies_seen"] == 1
    coauthor = dict(conn.execute("SELECT * FROM research_partnerships WHERE partnership_type='publication co-authorship'").fetchone())
    assert "no formal or commercial partnership established" in coauthor["formal_status"]
    author = dict(conn.execute("SELECT * FROM research_authors WHERE display_name='Jane Researcher'").fetchone())
    assert "not proof of current employment" in author["current_role_status"]
    tech = dict(conn.execute("SELECT * FROM research_technologies").fetchone())
    assert tech["licensing_status"] == "Available for licensing"
    oxford = dict(conn.execute("SELECT * FROM research_organisations WHERE canonical_name='University of Oxford'").fetchone())
    profile = research_innovation.profile(conn, oxford["research_organisation_id"])
    assert len(profile["publications"]) == 1
    assert len(profile["technologies"]) == 1


def test_monitor_is_append_only_and_idempotent(tmp_path):
    conn = db.connect(tmp_path / "monitor.db")
    _ingest(conn, _records())
    when = datetime(2026, 7, 16, tzinfo=timezone.utc)
    first = research_innovation.sync(conn, run_id="week-1", observed_at=when)
    second = research_innovation.sync(conn, run_id="week-2", observed_at=when)
    assert first["organisations_changed"] == 4
    assert second["organisations_changed"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM research_organisation_observations").fetchone()["n"] == 4
    assert conn.execute("SELECT COUNT(*) AS n FROM research_monitor_runs").fetchone()["n"] == 2


def test_phase10_routes_workflow_and_truth_boundaries_are_exposed():
    app = Path("pharmatune_ui/app.py").read_text()
    pages = Path("pharmatune_ui/pages.py").read_text()
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert '"Research & Innovation":lambda:pages.research_innovation(_navigate)' in app
    assert '"Research Detail":lambda:pages.research_detail(_navigate)' in app
    assert "europepmc openalex crossref account_intelligence patent_lifecycle research_innovation" in workflow
    assert "A publication proves published research—not technology availability" in pages
    assert "Co-authorship proves a shared publication—not a formal partnership" in pages
