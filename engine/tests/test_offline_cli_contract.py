"""Exercise the candidate's actual offline orchestration, not the real model gate."""
import json
from unittest import mock
from engine.orographic import pipeline
from engine.tests import test_offline_snapshot_cli as smoke


def test_offline_orchestration_with_isolated_synthetic_artifact_status(tmp_path):
    models=tmp_path/'synthetic-models'
    models.mkdir()
    for name in ('scout_model.pkl','scout_scaler.pkl','scout_side_model.pkl','production_payoff_ranker.pkl'):
        (models/name).write_bytes(b'SYNTHETIC TEST STATUS ONLY: NOT A MODEL\n'+name.encode())
    for name in ('scout_model_card.json','production_payoff_ranker_card.json'):
        (models/name).write_text(json.dumps({'synthetic':True,'purpose':'offline orchestration test'}))
    # Real status code hashes local fixture bytes. Do not weaken or modify the
    # real CI validate_model_artifacts.py gate or repository model manifest.
    with mock.patch.object(pipeline,'MODEL_DIR',models):
        statuses=pipeline._model_artifact_status()
        assert len(statuses)==6
        assert all(row['present'] and row['sha256'] for row in statuses.values())
        smoke.test_offline_cli_writes_valid_fail_closed_snapshot_and_ledgers(tmp_path/'outputs')
    payload=json.loads((tmp_path/'outputs/latest.json').read_text())
    assert payload['model_artifacts']==statuses
    assert payload['summary']['scout_signal_count']==0
    assert payload['council']['abstain'] is True
