from datetime import datetime, timezone
import hashlib
import json
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scripts.publish_cirrus_source import source_record


def test_pointer_only_references_verified_quote_bytes(tmp_path):
    quote=tmp_path/'live_option_quotes.parquet'
    pq.write_table(pa.table({'quote_date':[datetime.now(timezone.utc).date()]}),quote)
    manifest=dict(bundle_id='test',files=[dict(path=quote.name,rows=1,sha256=hashlib.sha256(quote.read_bytes()).hexdigest())])
    (tmp_path/'evidence_manifest.json').write_text(json.dumps(manifest))
    result=source_record(tmp_path,10,20,'abc')
    assert result['run_id']==10 and result['artifact_id']==20 and result['quote_rows']==1
    quote.write_bytes(b'corrupted')
    with pytest.raises(ValueError,match='hash mismatch'):source_record(tmp_path,10,20,'abc')


def test_pointer_rejects_incorrect_declared_rows(tmp_path):
    quote=tmp_path/'live_option_quotes.parquet'
    pq.write_table(pa.table({'quote_date':[datetime.now(timezone.utc).date()]}),quote)
    (tmp_path/'evidence_manifest.json').write_text(json.dumps(dict(bundle_id='test',files=[
        dict(path=quote.name,rows=2,sha256=hashlib.sha256(quote.read_bytes()).hexdigest())])))
    with pytest.raises(ValueError,match='row count'):source_record(tmp_path,10,20,'abc')
