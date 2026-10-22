"""Large canonical files keep their original keys/bytes and fail closed."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.error import HTTPError

import pytest

from scripts import upload_research_artifacts_to_r2 as upload


def test_409_mib_object_uses_bounded_multipart(tmp_path):
    path = tmp_path / 'prospective_pick_ledger.json'
    with path.open('wb') as handle:
        handle.truncate(409 * 1024 * 1024)
    client = mock.Mock()
    with mock.patch.object(upload, '_large_object_client', return_value=client), \
         mock.patch.object(upload.subprocess, 'run') as wrangler:
        upload._put_object('bucket', 'immutable/generation/ledger.json', path)
    wrangler.assert_not_called()
    args, kwargs = client.upload_file.call_args
    assert args == (str(path), 'bucket', 'immutable/generation/ledger.json')
    assert kwargs['Config'].multipart_chunksize == 64 * 1024 * 1024
    assert kwargs['Config'].max_concurrency == 2
    assert path.stat().st_size == 409 * 1024 * 1024


def test_at_limit_still_uses_wrangler(tmp_path):
    path = tmp_path / 'boundary'
    with path.open('wb') as handle:
        handle.truncate(upload.WRANGLER_MAX_BYTES)
    with mock.patch.object(upload, '_large_object_client') as client, \
         mock.patch.object(upload.subprocess, 'run', return_value=SimpleNamespace(
             returncode=0, stdout='', stderr='')) as wrangler:
        upload._put_object('bucket', 'key', path)
    client.assert_not_called()
    assert wrangler.call_args.args[0][-1] == str(path)


def test_failed_large_upload_cannot_publish_manifest(tmp_path):
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('{}')
    manifest = tmp_path / 'evidence_manifest.json'
    manifest.write_text('{}')
    with mock.patch.object(upload, 'validate_canonical_bundle', return_value={
            'files': [{'path': 'ledger.json'}]}), \
         mock.patch.object(upload, 'WRANGLER_MAX_BYTES', 1), \
         mock.patch.object(upload, '_large_object_client') as factory, \
         mock.patch.object(upload.subprocess, 'run') as wrangler:
        factory.return_value.upload_file.side_effect = RuntimeError('upload failed')
        with pytest.raises(RuntimeError, match='upload failed'):
            upload._upload_canonical(bucket='bucket', prefix='current', bundle=tmp_path)
    wrangler.assert_not_called()
    assert factory.return_value.upload_file.call_count == 1


@pytest.mark.parametrize('user_owned', [False, True])
def test_existing_r2_token_derives_s3_credentials_without_new_secrets(user_owned):
    account, token_id, token = 'a' * 32, 'b' * 32, 'test-secret-value'
    response = lambda: io.BytesIO(json.dumps({'success': True, 'result': {
        'id': token_id, 'status': 'active'}}).encode())
    effects = [response()]
    if user_owned:
        effects.insert(0, HTTPError('url', 403, 'forbidden', {}, None))
    upload._large_object_client.cache_clear()
    try:
        with mock.patch.dict(upload.os.environ, {
                'CLOUDFLARE_ACCOUNT_ID': account, 'CLOUDFLARE_R2_API_TOKEN': token}), \
             mock.patch.object(upload, 'urlopen', side_effect=effects) as verify, \
             mock.patch('boto3.client') as factory:
            upload._large_object_client()
            upload._large_object_client()
        assert verify.call_count == (2 if user_owned else 1)
        assert factory.call_count == 1
        assert factory.call_args.kwargs['aws_access_key_id'] == token_id
        assert factory.call_args.kwargs['aws_secret_access_key'] == hashlib.sha256(token.encode()).hexdigest()
        assert factory.call_args.kwargs['endpoint_url'] == f'https://{account}.r2.cloudflarestorage.com'
    finally:
        upload._large_object_client.cache_clear()


def test_disabled_token_fails_closed_without_sdk_call():
    upload._large_object_client.cache_clear()
    try:
        with mock.patch.dict(upload.os.environ, {
                'CLOUDFLARE_ACCOUNT_ID': 'a' * 32, 'CLOUDFLARE_R2_API_TOKEN': 'secret'}), \
             mock.patch.object(upload, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(
                 b'{"success": true, "result": {"id": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "status": "disabled"}}')), \
             mock.patch('boto3.client') as factory:
            with pytest.raises(RuntimeError, match='Cannot verify'):
                upload._large_object_client()
        factory.assert_not_called()
    finally:
        upload._large_object_client.cache_clear()
