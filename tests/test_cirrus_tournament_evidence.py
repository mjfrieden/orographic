import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'engine/orographic/cirrus_tournament_evidence.py'
spec = importlib.util.spec_from_file_location('bridge', MODULE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'input'
        self.root.mkdir()
        self.db = Path(self.temp.name) / 'mart.sqlite'

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return path

    def run_import(self):
        return bridge.import_artifacts(self.root, self.db, 'fixture')

    def query(self, sql):
        with sqlite3.connect(self.db) as db:
            return db.execute(sql).fetchall()

    def decision(self):
        return {'generated_at': '2026-10-01T17:20:00Z', 'release_id': 'old',
                'scan_date': '2026-10-01', 'candidate_count': 3,
                'selections': {'m': {'contract_symbol': 'TEST', 'score': .9,
                'status': 'selected', 'entry_at': '2026-10-01T12:19:00-05:00'}}}

    def test_idempotent_and_current_alias_deduplicated(self):
        self.write('2026-10-01-decision.json', self.decision())
        self.write('current_scan.json', self.decision())
        first = self.run_import()
        second = self.run_import()
        self.assertEqual(first['batch_artifacts'], 1)
        self.assertEqual(first['batch_observations'], 2)
        self.assertTrue(all(v == 0 for v in second['inserted'].values()))
        self.assertEqual(self.query('SELECT COUNT(*) FROM cirrus_tournament_sources'), [(2,)])
        self.assertEqual(self.query("SELECT observed_at FROM cirrus_tournament_observations WHERE kind='selection'"), [('2026-10-01T17:19:00+00:00',)])

    def test_release_same_id_different_bytes_preserved(self):
        self.write('release/manifest.json', {'release_id': 'same', 'generated_at': '2026-09-30T01:00:00Z'})
        self.run_import()
        self.write('release/manifest.json', {'release_id': 'same', 'generated_at': '2026-09-30T02:00:00Z'})
        self.run_import()
        self.assertEqual(self.query('SELECT * FROM cirrus_tournament_release_collisions'), [('same', 2)])
        self.assertEqual(self.query('SELECT COUNT(*) FROM cirrus_tournament_sources'), [(2,)])

    def test_old_new_release_distinct_and_no_core_writes(self):
        self.write('2026-10-01-decision.json', self.decision())
        self.run_import()
        new = self.decision(); new['release_id'] = 'new'
        self.write('2026-10-01-decision.json', new)
        self.run_import()
        self.assertEqual(self.query("SELECT DISTINCT release_id FROM cirrus_tournament_observations ORDER BY release_id"), [('new',), ('old',)])
        self.assertEqual(self.query("SELECT name FROM sqlite_master WHERE name='execution_outcomes'"), [])
        self.assertEqual(self.query('SELECT SUM(eligible_for_backtest) FROM cirrus_tournament_observations'), [(0,)])

    def test_binary_inventory_never_deserialized(self):
        (self.root / 'models.joblib').write_bytes(b'not executable')
        (self.root / 'scores.parquet').write_bytes(b'PAR1not decoded')
        self.run_import()
        self.assertEqual(self.query('SELECT COUNT(*) FROM cirrus_tournament_observations'), [(0,)])
        self.assertEqual(self.query("SELECT COUNT(*) FROM cirrus_tournament_artifacts WHERE decoding_status='inventory_only'"), [(2,)])

    def test_naive_and_malformed_timestamps_reject_atomic(self):
        for invalid in ['2026-10-01T17:20:00', 'not a time', 123, '2026-10-01']:
            with self.subTest(invalid=invalid):
                data = self.decision(); data['generated_at'] = invalid
                self.write('current_scan.json', data)
                with self.assertRaises(ValueError): self.run_import()
                self.assertFalse(self.db.exists())

    def test_bad_date_shape_duplicate_key_and_nan(self):
        path = self.root / 'current_scan.json'
        for text in ['{"generated_at":1,"generated_at":2}', '{"score":NaN}', '{"scan_date":"2026-02-30"}', '[]', '{"selections": []}']:
            with self.subTest(text=text):
                path.write_text(text)
                with self.assertRaises(ValueError): self.run_import()
                self.assertFalse(self.db.exists())

    def test_late_exit_is_proxy_not_fill(self):
        self.write('ledger.json', {'positions': [{'contract_symbol': 'TEST', 'release_id': 'old', 'entry_at': '2026-10-01T17:19:00Z', 'exit_at': '2026-10-03T17:19:00Z', 'status': 'closed', 'entry_ask': 1, 'exit_bid': .5, 'net_return': -.5}]})
        self.run_import()
        row = self.query('SELECT kind, payload_json, unavailable_reasons_json FROM cirrus_tournament_observations')[0]
        self.assertEqual(row[0], 'quote_proxy_position')
        self.assertEqual(json.loads(row[1])['net_return'], -.5)
        self.assertIn('execution_fills_not_retained', json.loads(row[2]))

    def test_reversed_exit_rejected(self):
        self.write('ledger.json', {'positions': [{'entry_at': '2026-10-02T17:19:00Z', 'exit_at': '2026-10-01T17:19:00Z'}]})
        with self.assertRaises(ValueError): self.run_import()

    def test_existing_collision_rolls_back(self):
        self.write('current_scan.json', self.decision())
        self.run_import()
        with sqlite3.connect(self.db) as db:
            db.execute("UPDATE cirrus_tournament_observations SET payload_json='{}'")
        self.write('delivery.json', {'recorded_at': '2026-10-01T18:00:00Z'})
        with self.assertRaisesRegex(ValueError, 'immutable evidence collision'): self.run_import()
        self.assertEqual(self.query("SELECT COUNT(*) FROM cirrus_tournament_artifacts WHERE kind='delivery'"), [(0,)])

    def test_raw_quote_no_implied_release(self):
        import gzip
        (self.root / '2026-10-01-raw.json.gz').write_bytes(gzip.compress(json.dumps([{'snapshot': '2026-10-01T17:00:00Z', 'quote': {'symbol': 'TEST'}}]).encode()))
        self.run_import()
        self.assertEqual(self.query('SELECT release_id, contract_symbol FROM cirrus_tournament_observations'), [(None, 'TEST')])

    def test_invalid_symlink_rejected(self):
        (self.root / 'ledger.json').symlink_to('/not-used')
        with self.assertRaisesRegex(ValueError, 'symlink'): self.run_import()

    def test_symlink_root_rejected(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink source root'):
            bridge.import_artifacts(alias, self.db, 'fixture')

    def test_bounded_exact_limit_and_limit_plus_one(self):
        from dataclasses import replace
        import gzip
        from unittest.mock import patch
        self.write('current_scan.json', self.decision())
        self.run_import()
        before = self.db.read_bytes()
        path = self.root / 'current_scan.json'
        raw = path.read_bytes()
        exact = replace(bridge.Limits(), encoded_file_bytes=len(raw), decoded_file_bytes=len(raw),
                        encoded_batch_bytes=len(raw), decoded_batch_bytes=len(raw))
        bridge.import_artifacts(self.root, self.db, 'fixture', exact)
        for field in ['encoded_file_bytes', 'decoded_file_bytes', 'encoded_batch_bytes', 'decoded_batch_bytes']:
            with self.subTest(field=field):
                with patch.object(bridge.json, 'loads', side_effect=AssertionError('must refuse before JSON parse')):
                    with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                        bridge.import_artifacts(self.root, self.db, 'fixture', replace(exact, **{field:len(raw)-1}))
                self.assertEqual(before, self.db.read_bytes())
        path.unlink()
        body = json.dumps([{'snapshot': '2026-10-01T17:00:00Z', 'quote': {'symbol':'X', 'note':'x'*1000}}]).encode()
        (self.root / '2026-10-01-raw.json.gz').write_bytes(gzip.compress(body))
        with patch.object(bridge.json, 'loads', side_effect=AssertionError('gzip must refuse before parse')):
            with self.assertRaisesRegex(ValueError, 'decoded bytes limit exceeded'):
                bridge.import_artifacts(self.root, self.db, 'fixture', replace(bridge.Limits(), decoded_file_bytes=len(body)-1))
        self.assertEqual(before, self.db.read_bytes())
        bridge.import_artifacts(self.root, self.db, 'fixture', replace(bridge.Limits(), decoded_file_bytes=len(body)))

    def test_binary_batch_counts_and_payload_limits_atomic(self):
        from dataclasses import replace
        self.write('current_scan.json', self.decision())
        self.run_import()
        before = self.db.read_bytes()
        (self.root/'models.joblib').write_bytes(b'12345')
        for limits in [replace(bridge.Limits(), encoded_file_bytes=4),
                       replace(bridge.Limits(), source_files=1),
                       replace(bridge.Limits(), directory_entries=1),
                       replace(bridge.Limits(), observations=1),
                       replace(bridge.Limits(), retained_payload_bytes=1)]:
            with self.subTest(limits=limits):
                with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                    bridge.import_artifacts(self.root, self.db, 'fixture', limits)
                self.assertEqual(before, self.db.read_bytes())
        (self.root/'models.joblib').unlink()
        raw=(self.root/'current_scan.json').read_bytes()
        (self.root/'2026-10-01-decision.json').write_bytes(raw)
        for field in ['encoded_batch_bytes','decoded_batch_bytes']:
            with self.assertRaisesRegex(ValueError, 'limit exceeded'):
                bridge.import_artifacts(self.root,self.db,'fixture',replace(bridge.Limits(), **{field:len(raw)*2-1}))
            self.assertEqual(before,self.db.read_bytes())
        bridge.import_artifacts(self.root,self.db,'fixture',replace(bridge.Limits(), encoded_batch_bytes=len(raw)*2, decoded_batch_bytes=len(raw)*2))

    def test_binary_exact_limit_streamed_without_json(self):
        from dataclasses import replace
        from unittest.mock import patch
        (self.root/'models.joblib').write_bytes(b'12345')
        with patch.object(bridge.json,'loads',side_effect=AssertionError('binary parsed')):
            bridge.import_artifacts(self.root,self.db,'fixture',replace(bridge.Limits(),encoded_file_bytes=5))
        before=self.db.read_bytes()
        (self.root/'models.joblib').write_bytes(b'123456')
        with self.assertRaisesRegex(ValueError,'encoded bytes limit exceeded'):
            bridge.import_artifacts(self.root,self.db,'fixture',replace(bridge.Limits(),encoded_file_bytes=5))
        self.assertEqual(before,self.db.read_bytes())

    def test_limits_require_nonnegative_integers(self):
        for value in [-1, True, 1.5, '10']:
            with self.assertRaises(ValueError): bridge.Limits(observations=value)


if __name__ == '__main__': unittest.main()
