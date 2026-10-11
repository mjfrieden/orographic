"""Synthetic sidecar fixtures only. No genuine BLS/Fed capture or live request."""
import base64
import copy
import json
import sqlite3
import unittest

from engine.orographic import event_observations as e
from engine.orographic import official_macro_import as m

BLS = b'''CONSUMER PRICE INDEX
September 2026
Consumer Price Index for All Urban Consumers
seasonally adjusted
0.2 percent
Transmission embargoed until 8:30 a.m. (ET)
'''
JOBS = b'''THE EMPLOYMENT SITUATION
September 2026
Total nonfarm payroll employment
seasonally adjusted
143,000 jobs
'''
FED = b'''Federal Reserve issues FOMC statement
September 16, 2026
For release at 2:00 p.m. EDT
'''


def span(raw, text):
    start = raw.index(text.encode())
    return {'start': start, 'end': start + len(text.encode()), 'text': text}


def fixture(raw=BLS, family='cpi'):
    fed = family == 'fomc_statement'
    title = {'cpi': 'CONSUMER PRICE INDEX', 'empsit': 'THE EMPLOYMENT SITUATION',
             'fomc_statement': 'Federal Reserve issues FOMC statement'}[family]
    metric = None
    if not fed:
        metric = {'name': 'cpi_all_items_sa_mom' if family == 'cpi' else 'nonfarm_payroll_sa_change',
                  'period': span(raw, 'September 2026'),
                  'context': span(raw, 'Consumer Price Index for All Urban Consumers' if family == 'cpi' else 'Total nonfarm payroll employment'),
                  'adjustment': span(raw, 'seasonally adjusted'),
                  'unit': span(raw, 'percent' if family == 'cpi' else 'jobs'),
                  'actual': span(raw, '0.2' if family == 'cpi' else '143,000')}
    return {'schema_version': m.VERSION,
            'source_url': 'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm' if fed else 'https://www.bls.gov/news.release/archives/' + family + '_10092026.htm',
            'artifact_sha256': e.sha256(raw), 'review_reference': 'synthetic:extraction-test',
            'rights_review_reference': 'synthetic:document-rights-test',
            'vintage_class': 'first_release_claim', 'title': span(raw, title),
            'metric': metric, 'publication_claim': span(raw, 'For release at 2:00 p.m. EDT') if fed else None,
            'receipt_claim': None}


def encode(sidecar):
    return e.canonical(sidecar).encode()


class MacroImport(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        e.initialize(self.conn)

    def load(self, raw=BLS, sidecar=None):
        return m.ingest(self.conn, raw, encode(sidecar or fixture(raw)))

    def row(self, ident):
        cursor = self.conn.execute('SELECT * FROM event_observation_vintages WHERE observation_id=?', (ident,))
        cursor.row_factory = sqlite3.Row
        return dict(cursor.fetchone())

    def test_reviewed_bls_value_preserves_missingness_and_blocks_asof(self):
        row = self.row(self.load())
        values = json.loads(row['values_json'])
        self.assertEqual(values, {'actual': {'value': .2, 'unit': 'pct_mom'}, 'expected': None, 'prior': None})
        for field in e.TIME_FIELDS:
            self.assertIsNone(row[field])
        self.assertEqual(e.as_of(self.conn, '2099-01-01T00:00:00Z', allow_fixtures=True), [])
        self.assertIn('provenance_review_unavailable', json.loads(row['unavailable_reasons_json']))
        self.assertEqual(json.loads(row['payload_json'])['reference_period'], '2026-09')

    def test_payroll_units_no_inferred_thousands(self):
        row = self.row(self.load(JOBS, fixture(JOBS, 'empsit')))
        self.assertEqual(json.loads(row['values_json'])['actual'], {'value': 143000, 'unit': 'persons_change'})

    def test_fomc_metadata_is_not_meeting_or_rate(self):
        row = self.row(self.load(FED, fixture(FED, 'fomc_statement')))
        self.assertEqual(row['source_id'], 'fed_public_statement')
        self.assertEqual(row['event_id'], 'fomc_statement_document:2026-09-16')
        self.assertTrue(all(v is None for v in json.loads(row['values_json']).values()))
        self.assertIsNone(row['event_date']); self.assertIsNone(row['released_at'])
        self.assertIn('source_use_not_reviewed', json.loads(row['unavailable_reasons_json']))

    def test_embargo_or_release_text_never_becomes_publication_clock(self):
        sidecar = fixture(); sidecar['publication_claim'] = span(BLS, 'Transmission embargoed until 8:30 a.m. (ET)')
        row = self.row(self.load(sidecar=sidecar))
        self.assertTrue(all(row[k] is None for k in e.TIME_FIELDS))

    def test_receipt_claim_does_not_create_historical_first_seen(self):
        sidecar = fixture()
        sidecar['receipt_claim'] = {'source_url': sidecar['source_url'], 'final_url': sidecar['source_url'],
                                    'artifact_sha256': sidecar['artifact_sha256'], 'status': 200, 'complete': True,
                                    'request_at': '2026-10-11T12:00:00-04:00', 'received_at': '2026-10-11T16:00:01Z'}
        row = self.row(self.load(sidecar=sidecar))
        self.assertIsNone(row['first_seen_at']); self.assertIsNone(row['available_at'])
        self.assertEqual(json.loads(row['provenance_json'])['receipt_claim']['received_at'], '2026-10-11T16:00:01.000000+00:00')

    def test_receipt_hash_url_redirect_timezone_and_order_fail(self):
        sidecar = fixture()
        receipt = {'source_url': sidecar['source_url'], 'final_url': sidecar['source_url'],
                   'artifact_sha256': sidecar['artifact_sha256'], 'status': 200, 'complete': True,
                   'request_at': '2026-10-11T16:00:00Z', 'received_at': '2026-10-11T16:00:01Z'}
        for changes in ({'artifact_sha256': 'a'*64}, {'final_url': 'https://evil.test'}, {'status': 302}, {'complete': False},
                        {'received_at': '2026-10-11T15:59:59Z'}, {'request_at': '2026-10-11'}, {'request_at': None},
                        {'received_at': '2026-10-11T16:00:01'}):
            sidecar['receipt_claim'] = dict(receipt, **changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_raw_and_sidecar_retained_byte_for_byte(self):
        sidecar = fixture(); encoded = encode(sidecar)
        ident = m.ingest(self.conn, BLS, encoded)
        raw = self.conn.execute('SELECT raw_bytes FROM event_source_artifacts WHERE sha256=?', (self.row(ident)['artifact_sha256'],)).fetchone()[0]
        envelope = json.loads(raw)
        self.assertEqual(base64.b64decode(envelope['raw_base64']), BLS)
        self.assertEqual(base64.b64decode(envelope['sidecar_base64']), encoded)
        self.assertEqual(envelope['sidecar_sha256'], e.sha256(encoded))
        self.assertEqual(e.resolve_pointer(envelope, '/event'), json.loads(self.row(ident)['payload_json']))

    def test_repeated_import_idempotent_and_revisions_immutable(self):
        first = self.load(); self.assertEqual(first, self.load())
        raw = BLS.replace(b'0.2', b'0.3')
        sidecar = fixture(); sidecar['artifact_sha256'] = e.sha256(raw)
        sidecar['metric']['actual'] = span(raw, '0.3'); sidecar['vintage_class'] = 'revision_claim'
        second = self.load(raw, sidecar)
        self.assertNotEqual(first, second)
        self.assertEqual(self.row(first)['event_id'], self.row(second)['event_id'])
        self.assertEqual(self.row(first)['revision_id'], e.sha256(BLS))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM official_macro_imports').fetchone()[0], 2)
        for statement in ('DELETE FROM official_macro_imports', 'UPDATE official_macro_imports SET artifact_sha256="x"',
                          'DELETE FROM event_observation_vintages', 'UPDATE event_observation_vintages SET available_at="2020-01-01T00:00:00Z"',
                          'DELETE FROM event_source_artifacts', 'UPDATE event_source_artifacts SET raw_bytes=x\'00\''):
            with self.subTest(statement=statement), self.assertRaises(sqlite3.IntegrityError): self.conn.execute(statement)

    def test_span_mismatch_offsets_boolean_and_empty_fail(self):
        for changes in ({'text': '0.9'}, {'start': -1}, {'end': len(BLS)+1}, {'start': True}, {'end': 0}, {'text': ''}):
            sidecar = fixture(); sidecar['metric']['actual'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_wrong_units_context_adjustment_and_family_fail(self):
        for key, text in (('unit', 'jobs'), ('context', 'Total nonfarm payroll employment'), ('adjustment', 'not adjusted')):
            raw = BLS + text.encode()
            sidecar = fixture(raw); sidecar['metric'][key] = span(raw, text)
            with self.subTest(key=key), self.assertRaises(ValueError): self.load(raw, sidecar)
        sidecar = fixture(); sidecar['metric']['name'] = 'nonfarm_payroll_sa_change'
        with self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_reference_period_never_inferred_from_input_or_url(self):
        for period in ('September', '2026-09', 'Sept 2026', 'October 2026', 'September 2027'):
            raw = BLS + period.encode(); sidecar = fixture(raw); sidecar['metric']['period'] = span(raw, period)
            with self.subTest(period=period), self.assertRaises(ValueError): self.load(raw, sidecar)

    def test_ambiguous_or_nonfinite_values_fail(self):
        for value in ('1,23', '1e9', 'NaN', '+0.2', '0.2%', '0.20.1', '--1', '01', '9999999999999'):
            raw = BLS + value.encode(); sidecar = fixture(raw); sidecar['metric']['actual'] = span(raw, value)
            with self.subTest(value=value), self.assertRaises(ValueError): self.load(raw, sidecar)

    def test_numeric_substring_is_rejected(self):
        sidecar = fixture(); sidecar['metric']['actual'] = span(BLS, '2')
        with self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_unicode_and_accounting_signs_never_become_positive(self):
        for prefix, suffix in (('−', ''), ('＋', ''), ('﹣', ''), ('− ', ''), ('+ ', ''),
                               ('(', ')'), ('（', '）'), ('( ', ' )'), ('', '−'), ('– ', ''), ('− \n', '')):
            raw = BLS + (prefix + '0.2' + suffix).encode()
            sidecar = fixture(raw)
            start = len(BLS) + len(prefix.encode())
            sidecar['metric']['actual'] = {'start': start, 'end': start + 3, 'text': '0.2'}
            with self.subTest(prefix=prefix, suffix=suffix), self.assertRaises(ValueError): self.load(raw, sidecar)

    def test_ascii_only_numbers_periods_and_source_dates(self):
        for number in ('1٢', '١٢', '1２', '0.٢'):
            raw = BLS + number.encode(); sidecar = fixture(raw)
            sidecar['metric']['actual'] = {'start': len(BLS), 'end': len(raw), 'text': number}
            with self.subTest(number=number), self.assertRaises(ValueError): self.load(raw, sidecar)
        raw = BLS + 'September ２０２６'.encode(); sidecar = fixture(raw)
        sidecar['metric']['period'] = span(raw, 'September ２０２６')
        with self.assertRaises(ValueError): self.load(raw, sidecar)
        sidecar = fixture(); sidecar['source_url'] = sidecar['source_url'].replace('10092026', '1009２０２６')
        with self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_replace_cannot_mutate_protected_rows_with_recursive_triggers_off(self):
        ident = self.load()
        self.conn.execute('PRAGMA recursive_triggers=OFF')
        self.assertEqual(self.conn.execute('PRAGMA recursive_triggers').fetchone()[0], 0)
        row = self.row(ident); digest = row['artifact_sha256']
        tests = [('INSERT OR REPLACE INTO event_source_artifacts VALUES(?,?,?)', (digest, 'bls_public_archive', b'changed')),
                 ('INSERT OR REPLACE INTO official_macro_imports VALUES(?,?)', (ident, '0'*64))]
        changed = dict(row, available_at='2020-01-01T00:00:00Z')
        keys = tuple(changed)
        tests.append(('INSERT OR REPLACE INTO event_observation_vintages (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', tuple(changed[k] for k in keys)))
        original = self.conn.execute('SELECT raw_bytes FROM event_source_artifacts WHERE sha256=?', (digest,)).fetchone()[0]
        for sql, args in tests:
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError): self.conn.execute(sql, args)
        self.assertEqual(self.row(ident), row)
        self.assertEqual(self.conn.execute('SELECT raw_bytes FROM event_source_artifacts WHERE sha256=?', (digest,)).fetchone()[0], original)
        self.assertEqual(self.conn.execute('SELECT artifact_sha256 FROM official_macro_imports WHERE observation_id=?', (ident,)).fetchone()[0], digest)
        self.assertEqual(self.load(), ident)
        self.conn.execute('INSERT OR REPLACE INTO event_source_artifacts VALUES(?,?,?)', (digest, 'bls_public_archive', original))
        self.assertEqual(self.row(ident), row)

    def test_negative_zero_and_integer_values(self):
        for value, expected in (('-0.2', -.2), ('0', 0), ('1', 1)):
            raw = BLS + b'Value ' + value.encode(); sidecar = fixture(raw)
            sidecar['metric']['actual'] = {'start': len(BLS) + 6, 'end': len(raw), 'text': value}
            self.assertEqual(json.loads(self.row(self.load(raw, sidecar))['values_json'])['actual']['value'], expected)

    def test_unknown_unbounded_and_blocked_sources_fail(self):
        url = fixture()['source_url']
        for candidate in (url+'?x=1', url+'#x', url.replace('https:', 'http:'), url.replace('www.bls.gov', 'www.bls.gov.evil.test'),
                          url.replace('www.bls.gov', 'user:pw@www.bls.gov'), url.replace('10092026', '02302026'),
                          'https://fred.stlouisfed.org/series/CPIAUCSL', 'https://www.bls.gov/news.release/cpi.htm'):
            sidecar = fixture(); sidecar['source_url'] = candidate
            with self.subTest(url=candidate), self.assertRaises(ValueError): self.load(sidecar=sidecar)

    def test_no_injected_consensus_time_or_fomc_rate(self):
        for key in ('expected', 'available_at', 'first_public_at', 'released_at', 'generated_at'):
            sidecar = fixture(); sidecar[key] = 'anything'
            with self.subTest(key=key), self.assertRaises(ValueError): self.load(sidecar=sidecar)
        sidecar = fixture(FED, 'fomc_statement'); sidecar['metric'] = {'rate': 4}
        with self.assertRaises(ValueError): self.load(FED, sidecar)

    def test_strict_json_and_input_bounds(self):
        for sidecar in (b'{}', b'{"x":1,"x":2}', b'{"x":NaN}', b'x'*(m.MAX_SIDECAR_BYTES+1), b''):
            with self.assertRaises(ValueError): m.ingest(self.conn, BLS, sidecar)
        for raw in (b'', b'\xff', b'x'*(m.MAX_BYTES+1)):
            with self.assertRaises(ValueError): m.ingest(self.conn, raw, encode(fixture()))

    def test_review_and_hash_assertions_required_but_not_trusted(self):
        for key, value in (('review_reference', ''), ('rights_review_reference', None), ('artifact_sha256', '0'*64),
                           ('schema_version', 'next'), ('vintage_class', 'first_release_verified')):
            sidecar = fixture(); sidecar[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): self.load(sidecar=sidecar)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM event_observation_vintages').fetchone()[0], 0)

    def test_baseline_registry_and_readiness_not_relaxed(self):
        self.load(); self.assertFalse(e.readiness(self.conn)['backtest_ready'])
        self.assertFalse(e.readiness(self.conn)['execution_ready'])
        self.assertEqual(e.REGISTRY['fred_alfred'][0], 'blocked_permission_required')
        self.assertEqual(e.REGISTRY['fed_public_statement'][0], 'review_required')


if __name__ == '__main__':
    unittest.main()
