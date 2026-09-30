import io
from unittest.mock import patch
from urllib.error import HTTPError

import pytest
from scripts.restore_research_artifacts_from_r2 import _get_object


def test_streamed_download_and_transient_retry_preserve_destination(tmp_path):
    destination = tmp_path/'ledger.json'
    destination.write_bytes(b'old')
    with patch('scripts.restore_research_artifacts_from_r2.urlopen', side_effect=[TimeoutError(), io.BytesIO(b'new')]) as request, patch('scripts.restore_research_artifacts_from_r2.time.sleep'):
        _get_object('bucket', 'prefix/ledger.json', destination, account_id='account', api_token='token')
    assert destination.read_bytes() == b'new'
    assert request.call_count == 2
    assert request.call_args.kwargs['timeout'] == 60
    assert not destination.with_suffix('.json.partial').exists()


def test_permanent_failure_keeps_existing_download(tmp_path):
    destination = tmp_path/'ledger.json'
    destination.write_bytes(b'old')
    with patch('scripts.restore_research_artifacts_from_r2.urlopen', side_effect=HTTPError('url',403,'Forbidden',{},None)) as request:
        with pytest.raises(HTTPError):
            _get_object('bucket', 'key', destination, account_id='account', api_token='token')
    assert destination.read_bytes() == b'old'
    assert request.call_count == 1
