from pathlib import Path


def test_workflow_bootstraps_global_regulator_data_on_merge():
    workflow = Path(".github/workflows/pharmatune_refresh.yml").read_text()
    assert "github.event_name == 'push'" in workflow
    assert "for source in ema_medicines mhra_medicine_recalls fda_orange_book" in workflow
    assert 'run-source "$source" --force' in workflow
