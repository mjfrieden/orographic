import json
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.orographic.evidence_store import build_canonical_evidence_bundle
from scripts.upload_research_artifacts_to_r2 import _upload_canonical
from scripts.restore_research_artifacts_from_r2 import restore_prefix

PREFIX = 'orographic/evidence-canonical/current'


def test_failed_publish_preserves_previous_bundle_and_restore_uses_manifest(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('{"entries": []}')
    bundle = tmp_path / 'bundle'
    build_canonical_evidence_bundle(source_roots=[], current_prospective_ledger=ledger,
        current_moonshot_ledger=ledger, payoff_evidence=None, output_dir=bundle)
    store = {}
    def put(bucket, key, path):
        store[key] = path.read_bytes()
    def get(bucket, key, path, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(store[key])
    with patch('scripts.upload_research_artifacts_to_r2._put_object', side_effect=put):
        _upload_canonical(bucket='bucket', prefix=PREFIX, bundle=bundle)
    old = dict(store)
    manifest_path = bundle / 'evidence_manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['generated_at_utc'] = '2026-09-30T14:00:00Z'
    manifest_path.write_text(json.dumps(manifest))
    count = 0
    def interrupted(bucket, key, path):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError('500 interrupted upload')
        put(bucket, key, path)
    with patch('scripts.upload_research_artifacts_to_r2._put_object', side_effect=interrupted):
        with pytest.raises(RuntimeError):
            _upload_canonical(bucket='bucket', prefix=PREFIX, bundle=bundle)
    assert all(store[key] == value for key, value in old.items())
    # Legacy mutable files must never supersede manifest-selected generation.
    store[f'{PREFIX}/prospective_pick_ledger.json'] = b'corrupted old mutable object'
    with patch('scripts.restore_research_artifacts_from_r2._list_objects', return_value=[{'key': key} for key in store if key.startswith(PREFIX + '/')]), patch('scripts.restore_research_artifacts_from_r2._get_object', side_effect=get):
        assert restore_prefix(bucket='bucket', account_id='account', api_token='token', prefix=PREFIX, output_dir=tmp_path/'restored') == len(manifest['files']) + 1
    with patch('scripts.upload_research_artifacts_to_r2._put_object', side_effect=put):
        _upload_canonical(bucket='bucket', prefix=PREFIX, bundle=bundle)
    assert store[f'{PREFIX}/evidence_manifest.json'] != old[f'{PREFIX}/evidence_manifest.json']
