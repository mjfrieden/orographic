import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pyarrow.parquet as pq
from engine.orographic import cirrus_tournament_evidence as t
from engine.orographic import event_observations as e
from engine.orographic import observation_parquet as o
from engine.orographic import shared_research_mart as native
from engine.orographic.evidence_store import build_canonical_evidence_bundle


def make_native(root):
    ledger = root / 'synthetic.json'
    ledger.write_text(json.dumps({'entries': [{'run_generated_at_utc': '2026-10-01T12:00:00Z',
        'picks': [{'recommendation_id': 'synthetic-pick', 'symbol': 'SYNTH',
        'contract_symbol': 'SYNTH261016C00100000', 'option_type': 'call',
        'strike': 100, 'emission_quote': {'captured_at_utc': '2026-10-01T12:00:00Z',
        'bid': 1.0, 'ask': 1.2}, 'scores': {'scout_score': .5}}]}]}))
    canonical = root / 'canonical'
    build_canonical_evidence_bundle(source_roots=[], current_prospective_ledger=ledger,
        current_moonshot_ledger=None, payoff_evidence=None, output_dir=canonical)
    mart = root / 'mart'
    native.build_shared_research_mart(orographic_canonical_dir=canonical, output_dir=mart)
    return mart


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.mart = make_native(self.root)
        self.input = self.root / 'source'
        self.input.mkdir()
        (self.input / 'current_scan.json').write_text(json.dumps({
            'generated_at': '2026-10-01T17:20:00Z', 'release_id': 'synthetic',
            'scan_date': '2026-10-01', 'selections': {}}))
        self.tdb = self.root / 't.sqlite'
        self.edb = self.root / 'e.sqlite'
        t.import_artifacts(self.input, self.tdb, 'synthetic')
        self.raw = b'{"events":[{"event_id":"synthetic-macro", "release_at":"2026-11-01T12:00:00Z"}]}'
        with sqlite3.connect(self.edb) as conn:
            e.ingest_cirrus(conn, self.raw, kind='tier1')
        self.before = {p.name: o.sha(p) for p in self.mart.iterdir() if p.is_file()}

    def write(self):
        return o.write_bundle(self.mart, tournament_database=self.tdb, event_database=self.edb)

    def test_native_roundtrip_idempotence_and_exact_coexistence(self):
        first = self.write()
        path = self.mart / o.NAMESPACE / o.MANIFEST
        before_manifest = path.read_bytes()
        second = self.write()
        self.assertEqual(first, second)
        self.assertEqual(before_manifest, path.read_bytes())
        self.assertEqual(self.before, {p.name: o.sha(p) for p in self.mart.iterdir() if p.is_file()})
        native.validate_shared_research_mart(self.mart)
        manifest, tables = o.read_bundle(self.mart)
        self.assertEqual(len(native.TABLE_CONTRACTS), 10)
        self.assertFalse(set(tables) & set(native.TABLE_CONTRACTS))
        self.assertEqual(manifest['summary']['eligible_for_backtest'], 0)
        self.assertEqual(tables['event_source_artifacts'].to_pylist()[0]['raw_bytes'], self.raw)
        for name, table in tables.items():
            db = self.tdb if name.startswith('cirrus_') else self.edb
            with sqlite3.connect(db) as conn:
                keys = manifest['artifacts'][name]['primary_key']
                original = conn.execute('SELECT * FROM ' + name + ' ORDER BY ' + ','.join(keys)).fetchall()
            self.assertEqual(original, [tuple(r.values()) for r in table.to_pylist()])
            disk = self.mart / o.NAMESPACE / manifest['artifacts'][name]['path']
            self.assertEqual(pq.read_table(disk), table)
        self.assertIn('available_at_unavailable', tables['event_observation_vintages'].to_pylist()[0]['unavailable_reasons_json'])

    def test_atomic_manifest_failure_preserves_previous(self):
        self.write()
        manifest = self.mart / o.NAMESPACE / o.MANIFEST
        before = manifest.read_bytes()
        real = o.os.replace
        def fail_manifest(source, destination):
            if Path(destination).name == o.MANIFEST:
                raise OSError('injected manifest failure')
            return real(source, destination)
        with patch.object(o.os, 'replace', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.write()
        self.assertEqual(before, manifest.read_bytes())
        o.read_bundle(self.mart)

    def rehash_manifest(self, manifest):
        identity = {k: v for k, v in manifest.items() if k != 'bundle_id'}
        manifest['bundle_id'] = hashlib.sha256(o.canonical(identity).encode()).hexdigest()
        (self.mart / o.NAMESPACE / o.MANIFEST).write_text(o.canonical(manifest))

    def test_source_attribution_rejection_preserves_previous_commit(self):
        self.write()
        path = self.mart / o.NAMESPACE / o.MANIFEST
        before = path.read_bytes()
        with sqlite3.connect(self.edb) as conn:
            cursor = conn.execute('SELECT * FROM event_observation_vintages')
            row = dict(zip([c[0] for c in cursor.description], cursor.fetchone()))
            row['source_id'] = 'bls_public_archive'
            row['observation_id'] = hashlib.sha256(o.canonical({k: v for k, v in row.items() if k != 'observation_id'}).encode()).hexdigest()
            conn.execute('UPDATE event_observation_vintages SET source_id=?,observation_id=?',
                         (row['source_id'], row['observation_id']))
        with self.assertRaisesRegex(ValueError, 'source attribution'):
            self.write()
        self.assertEqual(before, path.read_bytes())
        o.read_bundle(self.mart)

    def test_cell_byte_boundary_is_identical_for_writer_and_reader(self):
        self.write()
        _, tables = o.read_bundle(self.mart)
        size = sum(o._cell_bytes(row.values()) for table in tables.values() for row in table.to_pylist())
        before = (self.mart / o.NAMESPACE / o.MANIFEST).read_bytes()
        with patch.object(o, 'MAX_BYTES', size):
            self.write()
            o.read_bundle(self.mart)
        with patch.object(o, 'MAX_BYTES', size - 1):
            with self.assertRaisesRegex(ValueError, 'limits'):
                self.write()
            with self.assertRaisesRegex(ValueError, 'limits'):
                o.read_bundle(self.mart)
        self.assertEqual(before, (self.mart / o.NAMESPACE / o.MANIFEST).read_bytes())
        o.read_bundle(self.mart)

    def test_rehashed_private_fields_rejected_at_every_metadata_level(self):
        for level in ('root', 'artifact', 'summary', 'source_versions'):
            with self.subTest(level=level):
                manifest = self.write()
                target = manifest if level == 'root' else next(iter(manifest['artifacts'].values())) if level == 'artifact' else manifest[level]
                target['raw_payload'] = 'PRIVATE_SENTINEL_SYNTHETIC'
                self.rehash_manifest(manifest)
                with self.assertRaises(ValueError):
                    o.read_bundle(self.mart)

    def test_strict_manifest_json_and_types(self):
        for field in ('rows', 'eligible_for_backtest'):
            manifest = self.write()
            manifest['summary'][field] = False
            self.rehash_manifest(manifest)
            with self.assertRaisesRegex(ValueError, 'integer'):
                o.read_bundle(self.mart)
        manifest = self.write()
        path = self.mart / o.NAMESPACE / o.MANIFEST
        text = o.canonical(manifest)
        path.write_text(text[:-1] + ',"schema_version":' + json.dumps(o.VERSION) + '}')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            o.read_bundle(self.mart)
        path.write_text(text[:-1] + ',"raw_payload":NaN}')
        with self.assertRaisesRegex(ValueError, 'nonfinite'):
            o.read_bundle(self.mart)

    def test_first_export_failure_has_no_visible_manifest(self):
        real = o.os.replace
        def fail_manifest(source, destination):
            if Path(destination).name == o.MANIFEST:
                raise OSError('injected failure')
            return real(source, destination)
        with patch.object(o.os, 'replace', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.write()
        self.assertFalse((self.mart / o.NAMESPACE / o.MANIFEST).exists())
        self.assertEqual(self.before, {p.name: o.sha(p) for p in self.mart.iterdir() if p.is_file()})

    def test_schema_extension_rejected(self):
        with sqlite3.connect(self.edb) as conn:
            conn.execute('ALTER TABLE event_observation_vintages ADD COLUMN private_surprise TEXT')
        with self.assertRaisesRegex(ValueError, 'schema mismatch'):
            self.write()

    def test_summary_is_fixed_allowlist(self):
        manifest = self.write()
        self.assertEqual(set(manifest['summary']), {'rows', 'rows_with_unavailable_reasons', 'eligible_for_backtest', 'execution_ready'})
        encoded = o.canonical(manifest)
        for value in ('synthetic-macro', 'SYNTH261016C00100000', 'release_at', 'current_scan.json'):
            self.assertNotIn(value, encoded)

    def test_reject_corruption(self):
        manifest = self.write()
        path = self.mart / o.NAMESPACE / next(iter(manifest['artifacts'].values()))['path']
        path.write_bytes(b'broken')
        with self.assertRaises(ValueError):
            o.read_bundle(self.mart)

    def test_reject_event_raw_hash_tampering(self):
        with sqlite3.connect(self.edb) as conn:
            conn.execute("UPDATE event_source_artifacts SET raw_bytes=x'00'")
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.write()
        self.assertFalse((self.mart / o.NAMESPACE / o.MANIFEST).exists())

    def test_reject_witness_reason_removal(self):
        with sqlite3.connect(self.tdb) as conn:
            conn.execute("UPDATE cirrus_tournament_observations SET unavailable_reasons_json='[]'")
        with self.assertRaisesRegex(ValueError, 'witness reasons'):
            self.write()

    def test_limits_preserve_previous(self):
        self.write()
        before = (self.mart / o.NAMESPACE / o.MANIFEST).read_bytes()
        with patch.object(o, 'MAX_ROWS', 0):
            with self.assertRaisesRegex(ValueError, 'limits'):
                self.write()
        self.assertEqual(before, (self.mart / o.NAMESPACE / o.MANIFEST).read_bytes())

    def test_reject_wrong_schema_version(self):
        with sqlite3.connect(self.edb) as conn:
            conn.execute("UPDATE event_observation_versions SET version='unknown'")
        with self.assertRaisesRegex(ValueError, 'version'):
            self.write()

    def test_reject_symlink_destination(self):
        (self.mart / 'observation_bundles').symlink_to(self.input, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.write()

    def test_native_snapshot_binding(self):
        self.write()
        manifest = self.mart / 'mart_manifest.json'
        value = json.loads(manifest.read_text())
        value['generated_at_utc'] = '2026-10-02T00:00:00Z'
        manifest.write_text(json.dumps(value))
        native.validate_shared_research_mart(self.mart)
        with self.assertRaisesRegex(ValueError, 'different native snapshot'):
            o.read_bundle(self.mart)

    def test_guard_escalation_rejected(self):
        with sqlite3.connect(self.tdb) as conn:
            conn.execute('PRAGMA ignore_check_constraints=ON')
            conn.execute('UPDATE cirrus_tournament_observations SET eligible_for_backtest=1')
        with self.assertRaisesRegex(ValueError, 'eligibility'):
            self.write()


if __name__ == '__main__':
    unittest.main()
