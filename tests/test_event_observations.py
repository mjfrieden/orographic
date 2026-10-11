import json
import sqlite3
import unittest
from engine.orographic import event_observations as e


def record(**changes):
    result = dict(kind='earnings_schedule', event_id='earnings:issuer-1:2026Q3', issuer_id='issuer-1', ticker='OLD',
                  scheduled_at='2026-10-20T20:00:00Z', first_public_at='2026-10-01T13:00:00Z',
                  first_seen_at='2026-10-01T13:01:00Z', available_at='2026-10-01T13:02:00Z', revision_id='1')
    result.update(changes)
    return result


class Events(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        e.initialize(self.conn)

    def ingest(self, r, source='synthetic_fixture', evidence=True):
        raw = e.canonical([r]).encode()
        p = {'field_evidence': {key: 'fixture:/0/' + key for key in e.TIME_FIELDS if r.get(key)}} if evidence else None
        return e.ingest(self.conn, raw, source_id=source, records=[('/0', r)], provenance_by_pointer={'/0': p})

    def at(self, when='2026-10-02T00:00:00Z', **kwargs):
        return e.as_of(self.conn, when, allow_fixtures=True, **kwargs)

    def test_error_and_cancelled_vintages_fail_closed(self):
        for status in ('cancelled', 'error', 'withdrawn'):
            self.ingest(record(event_id=status, status=status))
        self.ingest(record(event_id='error-message', error='provider unavailable'))
        self.assertEqual([], self.at())

    def test_nested_candidate_array_keeps_original_bytes(self):
        raw = b'{"scan":{"candidates":[{"ticker":"XYZ","earnings_date":"2026-10-20"}]}}'
        e.ingest_cirrus(self.conn, raw, kind='earnings', candidate_pointer='/scan/candidates')
        row = self.conn.execute('SELECT artifact_sha256,json_pointer FROM event_observation_vintages').fetchone()
        self.assertEqual((e.sha256(raw), '/scan/candidates/0'), tuple(row))

    def test_public_archive_requires_explicit_review(self):
        self.ingest(record(), source='bls_public_archive')
        self.assertEqual([], self.at())

    def test_exact_cutoff(self):
        self.ingest(record())
        self.assertEqual([], self.at('2026-10-01T13:01:59.999999Z'))
        self.assertEqual(1, len(self.at('2026-10-01T13:02:00Z')))
        self.assertEqual([], e.as_of(self.conn, '2026-10-02T00:00:00Z'))

    def test_schedule_revision_does_not_rewrite_history(self):
        self.ingest(record())
        self.ingest(record(scheduled_at='2026-10-22T20:00:00Z', available_at='2026-10-03T13:02:00Z',
                           first_public_at='2026-10-03T13:00:00Z', first_seen_at='2026-10-03T13:01:00Z', revision_id='2'))
        self.assertIn('2026-10-20', self.at()[0]['scheduled_at'])
        self.assertIn('2026-10-22', self.at('2026-10-04T00:00:00Z')[0]['scheduled_at'])

    def test_unknown_is_not_midnight_or_generated_time(self):
        raw = e.canonical([{'ticker': 'XYZ', 'earnings_date': '2026-10-20', 'generated_at': '2026-09-01T00:00:00Z'}]).encode()
        e.ingest_cirrus(self.conn, raw, kind='earnings')
        row = self.conn.execute('SELECT first_public_at,first_seen_at,available_at,scheduled_at,event_date FROM event_observation_vintages').fetchone()
        self.assertEqual((None, None, None, None, '2026-10-20'), tuple(row))
        self.assertEqual([], self.at())

    def test_tier1_release_at_is_schedule_only(self):
        raw = b'{"events":[{"event_id":"cpi-2026-10","release_at":"2026-10-20T12:30:00Z","name":"CPI"}]}'
        e.ingest_cirrus(self.conn, raw, kind='tier1')
        row = self.conn.execute('SELECT scheduled_at,released_at,first_public_at FROM event_observation_vintages').fetchone()
        self.assertEqual(('2026-10-20T12:30:00.000000+00:00', None, None), tuple(row))
        self.assertEqual('cpi-2026-10', self.conn.execute('SELECT event_id FROM event_observation_vintages').fetchone()[0])
        self.assertEqual([], self.at())

    def test_tier1_date_only_retained(self):
        e.ingest_cirrus(self.conn, b'{"events":[{"release_at":"2026-10-20"}]}', kind='tier1')
        self.assertEqual((None, '2026-10-20'), tuple(self.conn.execute('SELECT scheduled_at,event_date FROM event_observation_vintages').fetchone()))

    def test_bad_timestamps_fail_closed(self):
        for value in ['2026-10-01', '2026-10-01T13:00:00', 'garbage', 42]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.ingest(record(first_seen_at=value))
        self.assertEqual(0, e.readiness(self.conn)['observation_count'])

    def test_missing_provenance_not_eligible(self):
        self.ingest(record(), evidence=False)
        self.assertEqual([], self.at())

    def test_known_after_cutoff_or_impossible_order_blocked(self):
        self.ingest(record(first_seen_at='2026-10-05T13:00:00Z'))
        self.assertEqual([], self.at())
        self.assertEqual([], self.at('2026-10-06T00:00:00Z'))

    def test_duplicate_idempotent_conflicts_excluded(self):
        r = record()
        self.ingest(r); self.ingest(r)
        self.assertEqual(1, e.readiness(self.conn)['observation_count'])
        self.ingest(record(scheduled_at='2026-10-21T20:00:00Z'))
        self.assertEqual([], self.at())
        self.assertEqual(2, e.readiness(self.conn)['observation_count'])

    def test_stable_issuer_not_ticker_join(self):
        self.ingest(record(ticker='NEW'))
        self.assertEqual(1, len(self.at(issuer_id='issuer-1')))
        self.assertEqual([], self.at(issuer_id='NEW'))
        self.ingest(record(event_id='unidentified', issuer_id=None))
        self.assertEqual(1, len(self.at()))

    def test_actual_expected_prior_units_missingness(self):
        self.ingest(record(kind='macro_release', issuer_id=None, event_id='cpi:2026-09',
                           released_at='2026-10-01T13:00:00Z', actual={'value': 0, 'unit': 'pct_mom'},
                           expected={'value': .2, 'unit': 'pct_mom'}, prior=None))
        values = json.loads(self.at()[0]['values_json'])
        self.assertEqual(0, values['actual']['value'])
        self.assertIsNone(values['prior'])
        with self.assertRaises(ValueError):
            self.ingest(record(actual={'value': 4, 'unit': None}))

    def test_actual_cannot_be_future_release_or_schedule(self):
        self.ingest(record(kind='earnings_release', released_at='2026-10-03T00:00:00Z', actual={'value': 1, 'unit': 'USD_per_share'}))
        self.assertEqual([], self.at())
        self.assertEqual([], self.at('2026-10-04T00:00:00Z'))
        self.ingest(record(event_id='other', actual={'value': 1, 'unit': 'USD_per_share'}))
        self.assertEqual([], self.at())

    def test_blocked_provider_and_unreviewed_use(self):
        with self.assertRaises(ValueError):
            self.ingest(record(), source='fred_alfred')
        self.ingest(record(), source='cirrus_retained_earnings')
        self.assertEqual([], self.at())

    def test_error_atomic_and_pointer_integrity(self):
        with self.assertRaises(ValueError):
            e.ingest(self.conn, b'[{}]', source_id='synthetic_fixture', records=[('/0', record())])
        with self.assertRaises(ValueError):
            e.ingest_cirrus(self.conn, b'{"events":[{"release_at":"2026-10-20T12:30:00Z"},null]}', kind='tier1')
        self.assertEqual(0, e.readiness(self.conn)['observation_count'])

    def test_strict_json(self):
        for raw in [b'{"events":[],"events":[]}', b'{"x":NaN}', b'{"x":1e999}', b'{']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                e.strict_json(raw)

    def test_source_hash_and_scope_no_execution_tables(self):
        r = record(); self.ingest(r)
        raw = e.canonical([r]).encode()
        self.assertEqual((e.sha256(raw), raw), tuple(self.conn.execute('SELECT sha256,raw_bytes FROM event_source_artifacts').fetchone()))
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual({'event_observation_versions', 'event_source_registry', 'event_source_artifacts', 'event_observation_vintages'}, tables)
        self.assertFalse(e.readiness(self.conn)['backtest_ready'])
        self.assertFalse(e.readiness(self.conn)['execution_ready'])

    def test_registry_tampering_rejected(self):
        self.conn.execute("UPDATE event_source_registry SET permitted_use_status='allowed' WHERE source_id='fred_alfred'")
        with self.assertRaises(ValueError):
            self.ingest(record())

    def test_new_invalid_revision_does_not_fallback(self):
        self.ingest(record())
        self.ingest(record(available_at='2026-10-02T00:00:00Z', scheduled_at=None, revision_id='2'))
        self.assertEqual([], self.at('2026-10-02T01:00:00Z'))


if __name__ == '__main__':
    unittest.main()
