"""Deterministic v1 parity and operation-count tests; no timing thresholds."""
import hashlib
import json
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from engine.orographic.contract_quote_paths import (
    ReplayLimits, ReplayLimitError, join_quote_paths, retained_json,
)
from test_contract_quote_paths import rec, quote, POLICY, ASOF


def inputs(recs, quotes):
    artifacts = {}
    def seal(rows):
        raw = json.dumps(rows).encode()
        artifacts[hashlib.sha256(raw).hexdigest()] = raw
        return retained_json(raw, source_namespace='synthetic_fixture')
    return seal(recs), seal(quotes), artifacts


def parity_cases():
    cases = {'eligible': ([rec()], [quote('10'), quote('11')]),
        'missing_entry': ([rec()], []),
        'missing_exit': ([rec()], [quote('10')]),
        'stale': ([rec()], [quote('10', bid_observed_at_utc='2026-10-09T09:00:00Z'), quote('11')]),
        'id_conflict': ([rec()], [quote('10'), quote('10', bid=.5), quote('11')]),
        'invalid_id_conflict': ([rec()], [quote('10'), quote('10', bid=0), quote('11')]),
        'tie_conflict': ([rec()], [quote('10'), quote('10', source_record_id='other', ask=2), quote('11')]),
        'same_price_tie': ([rec()], [quote('10'), quote('10', source_record_id='other'), quote('11')]),
        'identical_duplicates': ([rec()], [quote('10'), quote('10'), quote('11')]),
        'duplicate_recommendations': ([rec(), rec()], [quote('10'), quote('11')]),
        'future_quote': ([rec()], [quote('10'), quote('11'), quote('13', source_record_id='q10')]),
        'future_recommendation': ([rec(), rec(decision_at_utc='2026-10-09T13:00:00Z', available_at_utc='2026-10-09T13:00:00Z')], [quote('10'), quote('11')]),
        'wrong_security': ([rec()], [quote('10', security_id='other'), quote('11')]),
        'wrong_contract': ([rec()], [quote('10', contract_id='other'), quote('11')]),
        'unverified_deliverable': ([rec(identity_evidence=None)], [quote('10'), quote('11')]),
        'expiry_offset': ([rec(expires_at_utc='2026-10-16T16:00:00-04:00')], [quote('10'), quote('11')]),
        'late_decision_availability': ([rec(available_at_utc='2026-10-09T10:00:01Z')], [quote('10'), quote('11')]),
        'malformed_recommendation_id': ([rec(recommendation_id=[])], [quote('10'), quote('11')]),
        'huge_bid': ([rec()], [quote('10', bid=10**400), quote('11')]),
    }
    # A later conflict anywhere inside the window must still exclude, even
    # though an otherwise valid first available quote could be selected.
    later = quote('10', source_record_id='later', available_at_utc='2026-10-09T10:00:04Z')
    cases['later_window_conflict'] = ([rec()], [quote('10'), later, {**later, 'ask': 1.4}, quote('11')])
    return cases


def scale_inputs(n):
    start = datetime(2026, 10, 9, 10, tzinfo=timezone.utc)
    rows = []
    for i in list(range(n)) + [3602]:
        available = start + timedelta(seconds=i)
        rows.append(quote('10', source_record_id=f'q{i}',
            available_at_utc=available.isoformat(),
            capture_at_utc=(available - timedelta(seconds=1)).isoformat(),
            bid_observed_at_utc=(available - timedelta(seconds=2)).isoformat(),
            ask_observed_at_utc=(available - timedelta(seconds=2)).isoformat()))
    return inputs([rec(recommendation_id=f'r{i}') for i in range(n)], rows)


class QuotePathsScalingTest(unittest.TestCase):
    def test_exact_report_hashes_match_frozen_v1(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/quote_path_v1_report_hashes.json').read_text())
        self.assertEqual(expected['module_sha256'], '6bb6424fe6e1341ba551ad7b78bec35b4657eda2b0f9a37a5549291397bd1331')
        for name, (recs, quotes) in parity_cases().items():
            with self.subTest(case=name):
                r, q, artifacts = inputs(recs, quotes)
                out = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts)
                self.assertEqual(out['report_sha256'], expected['reports'][name])

    def test_each_artifact_decoded_once_and_windows_are_logarithmic(self):
        for n in (64, 256, 1024):
            with self.subTest(rows=n):
                r, q, artifacts = scale_inputs(n)
                operations = {}
                out = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF,
                                       artifacts=artifacts, operation_counts=operations)
                self.assertEqual(out['coverage']['eligible_quote_replays'], n)
                self.assertEqual(operations['artifact_hashes'], 2)
                self.assertEqual(operations['artifact_decodes'], 2)
                self.assertEqual(operations['row_validations'], 2*n + 1)
                self.assertEqual(operations['reconstructed_rows'], 2*n + 1)
                self.assertEqual(operations['quote_rows_indexed'], n + 1)
                self.assertEqual(operations['window_queries'], 2*n)
                self.assertLessEqual(operations['time_index_comparisons'], 4*n*math.ceil(math.log2(n+2)))

    def test_contract_index_does_not_scan_unrelated_contracts(self):
        n = 128
        r, q, artifacts = inputs(
            [rec(recommendation_id=f'r{i}', contract_id=f'contract:{i}') for i in range(n)],
            [quote(hour, source_record_id=f'q{i}:{hour}', contract_id=f'contract:{i}') for i in range(n) for hour in ('10', '11')])
        operations = {}
        out = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts, operation_counts=operations)
        self.assertEqual(out['coverage']['eligible_quote_replays'], n)
        self.assertEqual(operations['quote_rows_indexed'], 2*n)
        self.assertLessEqual(operations['time_index_comparisons'], 8*n)

    def test_limits_fail_without_partial_reports(self):
        r, q, artifacts = inputs([rec()], [quote('10'), quote('11')])
        cases = [dict(max_artifacts=1), dict(max_artifact_bytes=10),
                 dict(max_total_artifact_bytes=10), dict(max_input_rows=2),
                 dict(max_row_bytes=10), dict(max_total_row_bytes=10),
                 dict(max_json_nodes=2), dict(max_json_depth=1),
                 dict(max_reconstructed_rows=1)]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ReplayLimitError):
                join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts,
                                 limits=replace(ReplayLimits(), **fields))
        for fields in (dict(max_artifacts=0), dict(max_input_rows=True)):
            with self.assertRaises(ValueError):
                join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts,
                                 limits=replace(ReplayLimits(), **fields))

    def test_generator_consumption_is_bounded(self):
        r, q, artifacts = inputs([rec()], [quote('10')])
        consumed = []
        def infinite():
            while True:
                consumed.append(1)
                yield q[0]
        with self.assertRaises(ReplayLimitError):
            join_quote_paths([], infinite(), policy=POLICY, as_of_utc=ASOF,
                             artifacts=artifacts, limits=replace(ReplayLimits(), max_input_rows=3))
        self.assertEqual(len(consumed), 4)

    def test_pointer_and_ingestion_limits(self):
        raw = b'{"a":{"b":[{"x":1}]}}'
        with self.assertRaises(ReplayLimitError):
            retained_json(raw, source_namespace='s', pointer='/a/b/0',
                          limits=replace(ReplayLimits(), max_pointer_depth=2))
        with self.assertRaises(ReplayLimitError):
            retained_json(b'[{"x":1},{"x":2}]', source_namespace='s',
                          limits=replace(ReplayLimits(), max_input_rows=1))

    def test_operation_diagnostics_do_not_change_report(self):
        r, q, artifacts = inputs([rec()], [quote('10'), quote('11')])
        plain = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts)
        counts = {'old': 1}
        instrumented = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF, artifacts=artifacts, operation_counts=counts)
        self.assertEqual(plain, instrumented)
        self.assertNotIn('old', counts)


if __name__ == '__main__':
    unittest.main()
