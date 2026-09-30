from unittest.mock import patch
import pandas as pd
from engine.orographic.shared_research_mart import _frame_records, _parquet_records


def test_frame_records_preserves_nulls_without_eager_dictionary_copy():
    frame = pd.DataFrame({'a': [1.0, float('nan')], 'b': ['x', None]})
    with patch.object(pd.DataFrame, 'to_dict', side_effect=AssertionError('eager materialization')):
        assert list(_frame_records(frame)) == [{'a': 1.0, 'b': 'x'}, {'a': None, 'b': None}]


def test_parquet_records_preserves_rows_across_batches(tmp_path):
    path = tmp_path/'quotes.parquet'
    frame = pd.DataFrame({'symbol': ['ABC']*17000, 'bid': range(17000)})
    frame.to_parquet(path)
    with patch('pandas.read_parquet', side_effect=AssertionError('full file read')):
        rows = list(_parquet_records(path))
    assert len(rows) == 17000
    assert rows[0]['bid'] == 0
    assert rows[-1]['bid'] == 16999
