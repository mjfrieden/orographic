import hashlib
import copy
import json
import unittest
from dataclasses import replace
from engine.orographic.contract_quote_paths import ReplayPolicy, join_quote_paths as _join_quote_paths, retained_json

ARTIFACTS = {}

def join_quote_paths(*args, **kwargs):
    return _join_quote_paths(*args, **kwargs, artifacts=ARTIFACTS)

POLICY = ReplayPolicy('fixed-one-hour-quote-replay', '1', 60, 3600, 60, 30, 5)
ASOF = '2026-10-09T12:00:00Z'
IDENTITY = dict(source_namespace='synthetic_fixture', security_id='security:1',
    contract_id='AAA261016C00100000', contract_namespace='occ', deliverable_id='standard-100-shares:security:1',
    currency='USD', multiplier=100, expires_at_utc='2026-10-16T20:00:00Z',
    identity_evidence={'review_status': 'verified', 'reference': 'synthetic:test-only'},
    position_kind='single_long_option')


def rec(**changes):
    return {**IDENTITY, 'recommendation_id': 'r1', 'decision_at_utc': '2026-10-09T10:00:00Z',
            'available_at_utc': '2026-10-09T10:00:00Z', **changes}


def quote(hour, **changes):
    return {**IDENTITY, 'source_record_id': 'q' + str(hour), 'bid': 1., 'ask': 1.2,
        'capture_at_utc': f'2026-10-09T{hour}:00:01Z',
        'available_at_utc': f'2026-10-09T{hour}:00:02Z',
        'bid_observed_at_utc': f'2026-10-09T{hour}:00:00Z',
        'ask_observed_at_utc': f'2026-10-09T{hour}:00:00Z', **changes}


def seal(rows):
    raw = json.dumps(rows).encode()
    ARTIFACTS[hashlib.sha256(raw).hexdigest()] = raw
    return retained_json(raw, source_namespace='synthetic_fixture')


def run(recs=None, quotes=None, **kwargs):
    return join_quote_paths(seal([rec()] if recs is None else recs),
        seal([quote('10'), quote('11')] if quotes is None else quotes),
        policy=kwargs.get('policy', POLICY), as_of_utc=kwargs.get('as_of', ASOF))


class QuotePathsTest(unittest.TestCase):
    def setUp(self):
        ARTIFACTS.clear()

    def test_happy_path_is_replay_not_fills_and_does_not_mutate(self):
        r, q = seal([rec()]), seal([quote('10'), quote('11')])
        before = copy.deepcopy((r, q))
        result = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF)
        self.assertEqual(before, (r, q))
        p = result['paths'][0]
        self.assertEqual(p['status'], 'eligible_quote_replay')
        self.assertEqual(p['entry']['replay_price'], 1.2)
        self.assertEqual(p['exit']['replay_price'], 1.)
        self.assertEqual(p['label_available_at_utc'], '2026-10-09T11:01:00+00:00')
        self.assertFalse(result['runtime_active']); self.assertFalse(result['broker_fills'])
        self.assertIn('artifact_sha256', p['exit']); self.assertNotIn('profit', result)

    def test_no_missing_as_zero(self):
        result = run(quotes=[])
        self.assertEqual(result['paths'][0]['reason'], 'missing_valid_entry_quote')
        self.assertIsNone(result['paths'][0]['entry'])
        self.assertIsNone(result['paths'][0]['label_available_at_utc'])

    def test_every_identity_component_is_binding(self):
        for field in ('security_id', 'contract_id', 'contract_namespace', 'deliverable_id', 'currency'):
            with self.subTest(field=field):
                result = run(quotes=[quote('10', **{field: 'other'}), quote('11')])
                self.assertEqual(result['paths'][0]['reason'], 'missing_valid_entry_quote')
        result = run(quotes=[quote('10', multiplier=10), quote('11')])
        self.assertEqual(result['coverage']['eligible_quote_replays'], 0)

    def test_no_ticker_or_unverified_multiplier_fallback(self):
        for field in ('security_id', 'contract_id', 'multiplier', 'identity_evidence', 'deliverable_id'):
            with self.subTest(field=field):
                self.assertEqual(run(recs=[rec(**{field: None})])['coverage']['eligible_quote_replays'], 0)

    def test_namespace_separation(self):
        raw = json.dumps([quote('10', source_namespace='other'), quote('11', source_namespace='other')]).encode()
        ARTIFACTS[hashlib.sha256(raw).hexdigest()] = raw
        qs = retained_json(raw, source_namespace='other')
        result = join_quote_paths(seal([rec()]), qs, policy=POLICY, as_of_utc=ASOF)
        self.assertEqual(result['coverage']['eligible_quote_replays'], 0)

    def test_provider_timestamps_are_required_not_trade_or_capture(self):
        for field in ('bid_observed_at_utc', 'ask_observed_at_utc', 'capture_at_utc', 'available_at_utc'):
            with self.subTest(field=field):
                self.assertEqual(run(quotes=[quote('10', **{field: None}), quote('11')])['coverage']['eligible_quote_replays'], 0)

    def test_bad_markets_and_clock_values(self):
        changes = [{'bid': 0}, {'ask': 0}, {'bid': -1}, {'bid': True}, {'bid': 2},
                   {'ask': '1.2'}, {'bid_observed_at_utc': '2026-10-09T09:00:00Z'},
                   {'ask_observed_at_utc': '2026-10-09T10:00:03Z'},
                   {'capture_at_utc': '2026-10-09T10:00:03Z'},
                   {'available_at_utc': '2026-10-09T10:00:07Z'},
                   {'available_at_utc': '2026-10-09'},
                   {'available_at_utc': '2026-10-09T10:00:02'}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(run(quotes=[quote('10', **change), quote('11')])['coverage']['eligible_quote_replays'], 0)

    def test_maturity_waits_for_entire_window(self):
        r = run(as_of='2026-10-09T11:00:03Z')
        self.assertEqual(r['paths'][0]['reason'], 'immature_exit_window')
        self.assertEqual(run(as_of='2026-10-09T11:01:00Z')['coverage']['eligible_quote_replays'], 1)

    def test_late_recommendation_cannot_be_backdated(self):
        self.assertEqual(run(recs=[rec(available_at_utc='2026-10-09T10:00:01Z')])['paths'][0]['reason'], 'recommendation_not_available_at_decision')

    def test_no_backward_or_late_window_fallback(self):
        for hour in ('09', '12'):
            self.assertEqual(run(quotes=[quote(hour), quote('11')])['coverage']['eligible_quote_replays'], 0)

    def test_expiration_is_binding(self):
        self.assertEqual(run(recs=[rec(expires_at_utc='2026-10-09T10:30:00Z')])['paths'][0]['reason'], 'window_after_contract_expiration')

    def test_conflicting_ids_include_invalid_rows(self):
        for changes in ({'bid': 0}, {'bid': .9}, {'security_id': 'different'}):
            result = run(quotes=[quote('10'), quote('10', **changes), quote('11')])
            self.assertEqual(result['paths'][0]['reason'], 'conflicting_source_record_id')

    def test_time_conflicts_do_not_choose_best_price(self):
        result = run(quotes=[quote('10'), quote('10', source_record_id='another', ask=1.3), quote('11')])
        self.assertEqual(result['paths'][0]['reason'], 'conflicting_entry_time_quotes')

    def test_identical_copies_and_permutation_are_deterministic(self):
        r = seal([rec()]); q = seal([quote('10'), quote('10'), quote('11')])
        a = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF)
        b = join_quote_paths(r, q[::-1], policy=POLICY, as_of_utc=ASOF)
        self.assertEqual(a, b); self.assertEqual(a['coverage']['eligible_quote_replays'], 1)

    def test_duplicate_recommendations_are_excluded(self):
        self.assertEqual(run(recs=[rec(), rec()])['coverage']['excluded_by_reason'], {'duplicate_recommendation_id': 2})

    def test_input_hash_tamper(self):
        r = seal([rec()]); r[0]['multiplier'] = 10
        self.assertEqual(join_quote_paths(r, seal([]), policy=POLICY, as_of_utc=ASOF)['paths'][0]['reason'], 'source_row_hash_mismatch')

    def test_parser_fails_closed(self):
        for raw in (b'[{"a":1,"a":2}]', b'[{"x":NaN}]', b'[{"x":1e999}]', b'[1]', b'[{"_lineage":{}}]'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, TypeError)):
                retained_json(raw, source_namespace='s')
        with self.assertRaises(ValueError):
            retained_json(b'{"rows":[]}', source_namespace='s', pointer='rows')

    def test_explicit_mapping_preserves_original_hash_and_clock_missingness(self):
        raw = b'[{"quote_key":"a","contract_symbol":"AAA","observed_at_utc":"2026-10-09T10:00:00Z"}]'
        r = retained_json(raw, source_namespace='s', field_map={'source_record_id':'quote_key', 'contract_id':'contract_symbol', 'capture_at_utc':'observed_at_utc'})[0]
        self.assertEqual(r['contract_id'], 'AAA')
        self.assertNotIn('bid_observed_at_utc', r)
        self.assertIn('retained_row_sha256', r['_lineage'])
        with self.assertRaises(ValueError):
            retained_json(raw, source_namespace='s', field_map={'quote_key':'contract_symbol'})

    def test_future_conflict_does_not_change_historical_selection(self):
        future = quote('12', source_record_id='q10', bid=.5)
        result = run(quotes=[quote('10'), quote('11'), future], as_of='2026-10-09T11:30:00Z')
        self.assertEqual(result['coverage']['eligible_quote_replays'], 1)
        self.assertEqual(result['coverage']['invalid_quotes_by_reason'], {'quote_not_available_at_asof': 1})

    def test_future_recommendation_does_not_poison_duplicate_check(self):
        result = run(recs=[rec(), rec(decision_at_utc='2026-10-09T13:00:00Z', available_at_utc='2026-10-09T13:00:00Z')])
        self.assertEqual(result['coverage']['eligible_quote_replays'], 1)
        self.assertEqual(result['coverage']['excluded_by_reason'], {'recommendation_not_available_at_asof': 1})

    def test_lineage_fields_are_bound_to_verified_source_bytes(self):
        for field in ('artifact_sha256', 'json_pointer', 'observation_id', 'retained_row_sha256'):
            with self.subTest(field=field):
                q = seal([quote('10'), quote('11')]); q[0]['_lineage'][field] = 'forged'
                result = join_quote_paths(seal([rec()]), q, policy=POLICY, as_of_utc=ASOF)
                self.assertEqual(result['coverage']['eligible_quote_replays'], 0)

    def test_ambiguous_normalizations_are_excluded_deterministically(self):
        row = quote('10', alternate_id='different')
        raw = json.dumps([row]).encode(); ARTIFACTS[hashlib.sha256(raw).hexdigest()] = raw
        a = retained_json(raw, source_namespace='synthetic_fixture')
        # Alternate mapping affects a new field, so it is permitted by parsing,
        # but cannot create a second interpretation of this source observation.
        b = retained_json(raw, source_namespace='synthetic_fixture', field_map={'extra_id':'alternate_id'})
        q = a + b + seal([quote('11')]); r = seal([rec()])
        first = join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF)
        second = join_quote_paths(r, q[::-1], policy=POLICY, as_of_utc=ASOF)
        self.assertEqual(first, second)
        self.assertEqual(first['paths'][0]['reason'], 'conflicting_observation_normalization')

    def test_malformed_ids_and_huge_numbers_fail_closed(self):
        self.assertEqual(run(recs=[rec(recommendation_id=[])])['coverage']['eligible_quote_replays'], 0)
        self.assertEqual(run(quotes=[quote('10', bid=10**400), quote('11')])['coverage']['eligible_quote_replays'], 0)
        self.assertEqual(run(recs=[rec(multiplier=10**400)])['coverage']['eligible_quote_replays'], 0)

    def test_equivalent_expiry_offsets_match(self):
        self.assertEqual(run(recs=[rec(expires_at_utc='2026-10-16T16:00:00-04:00')])['coverage']['eligible_quote_replays'], 1)

    def test_missing_artifact_registry_cannot_bypass_integrity(self):
        with self.assertRaises(ValueError):
            _join_quote_paths(seal([rec()]), seal([quote('10'), quote('11')]), policy=POLICY, as_of_utc=ASOF, artifacts=None)

    def test_invalid_policy(self):
        for change in ({'holding_seconds': 0}, {'entry_window_seconds': True}, {'exit_rule': 'best_bid'}, {'policy_version': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                run(policy=replace(POLICY, **change))


if __name__ == '__main__':
    unittest.main()
