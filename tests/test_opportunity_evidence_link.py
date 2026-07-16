import json

from pharmatune_ui.pages import _official_evidence_url


def test_official_evidence_url_prefers_structured_evidence():
    row = {
        "details": {"evidence": [{"url": "https://www.gov.uk/drug-device-alerts/example"}]},
        "evidence_links_json": json.dumps(["https://fallback.example"]),
    }
    assert _official_evidence_url(row) == "https://www.gov.uk/drug-device-alerts/example"


def test_official_evidence_url_uses_legacy_link_column():
    row = {"details": {}, "evidence_links_json": json.dumps(["slug-only", "https://www.ema.europa.eu/example"])}
    assert _official_evidence_url(row) == "https://www.ema.europa.eu/example"
