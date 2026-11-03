from __future__ import annotations
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts.sync_operational_ledgers_r2 import pull_ledgers, push_ledgers


class OperationalLedgersR2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = tuple(self.root / name for name in ('prospective_pick_ledger.json', 'research_run_ledger.json'))
        for p in self.paths:
            p.write_text('{"seed": true}')
        self.objects = {}
        self.calls = []
        self.records = []
        for i, p in enumerate(self.paths):
            raw = json.dumps({'entries': [{'id': i}], 'recovered': True}).encode()
            digest = hashlib.sha256(raw).hexdigest()
            key = f'operational/v1/objects/{p.name}/{digest}.json.gz'
            self.objects[key] = gzip.compress(raw)
            self.records.append({'path': str(p), 'object_key': key, 'sha256': digest})
        self.manifest = {'artifact': 'orographic_operational_ledger_manifest', 'schema_version': 1, 'ledgers': self.records}
        self.error_key = None
        self.error = None

    def transfer(self, action, *, bucket, key, file_path):
        self.calls.append((action, key))
        if key == self.error_key:
            raise self.error
        if action == 'get':
            raw = json.dumps(self.manifest).encode() if key.endswith('/manifest.json') else self.objects[key]
            file_path.write_bytes(raw)
        else:
            self.objects[key] = file_path.read_bytes()

    def pull(self):
        with patch('scripts.sync_operational_ledgers_r2._wrangler_object', side_effect=self.transfer):
            return pull_ledgers(bucket='research', prefix='operational/v1', paths=self.paths)

    def assert_seed_unchanged(self):
        for p in self.paths:
            self.assertEqual(json.loads(p.read_bytes()), {'seed': True})
        self.assertFalse(any(action == 'put' for action, _ in self.calls))

    def test_successful_complete_restore_and_manifest_last_publication(self):
        self.assertEqual(self.pull(), 2)
        for p in self.paths:
            self.assertTrue(json.loads(p.read_bytes())['recovered'])
        with patch('scripts.sync_operational_ledgers_r2._wrangler_object', side_effect=self.transfer):
            m = push_ledgers(bucket='research', prefix='operational/v1', paths=self.paths)
        self.assertEqual(len(m['ledgers']), 2)
        self.assertEqual(self.calls[-1], ('put', 'operational/v1/manifest.json'))

    def test_second_ledger_500_does_not_replace_either_seed(self):
        self.error_key = self.records[1]['object_key']
        self.error = subprocess.CalledProcessError(1, ['mock'], stderr='500 Internal Server Error')
        with self.assertRaises(subprocess.CalledProcessError): self.pull()
        self.assert_seed_unchanged()

    def test_manifest_failures_never_fall_back(self):
        for message in ('500', '403', '404'):
            with self.subTest(message=message):
                self.error_key = 'operational/v1/manifest.json'
                self.error = subprocess.CalledProcessError(1, ['mock'], stderr=message)
                with self.assertRaises(subprocess.CalledProcessError): self.pull()
                self.assert_seed_unchanged()
        self.error = subprocess.TimeoutExpired(['mock'], 1)
        with self.assertRaises(subprocess.TimeoutExpired): self.pull()
        self.assert_seed_unchanged()

    def test_empty_missing_duplicate_unknown_and_malformed_records(self):
        variants = [[], self.records[:1], self.records + self.records[:1], self.records + [None],
                    self.records + [dict(self.records[0], path='unexpected.json')], 'invalid']
        for records in variants:
            with self.subTest(records=repr(records)[:80]):
                self.manifest['ledgers'] = records
                with self.assertRaises(RuntimeError): self.pull()
                self.assert_seed_unchanged()

    def test_retired_records_do_not_replace_required_ledgers(self):
        self.records.append({'path': 'web/data/diagnostics/moonshot_prospective_ledger.json'})
        self.assertEqual(self.pull(), 2)

    def test_nonobject_manifest_fails_closed(self):
        self.manifest = []
        with self.assertRaises(RuntimeError): self.pull()
        self.assert_seed_unchanged()

    def test_second_ledger_timeout_preserves_local_seeds(self):
        self.error_key = self.records[1]['object_key']
        self.error = subprocess.TimeoutExpired(['mock'], 1)
        with self.assertRaises(subprocess.TimeoutExpired): self.pull()
        self.assert_seed_unchanged()

    def test_invalid_manifest_identity(self):
        for field, value in [('artifact', 'wrong'), ('schema_version', 2)]:
            with self.subTest(field=field):
                old = self.manifest[field]; self.manifest[field] = value
                with self.assertRaises(RuntimeError): self.pull()
                self.assert_seed_unchanged(); self.manifest[field] = old

    def test_bad_hash_and_foreign_object_key(self):
        for field, value in [('sha256', 'bad'), ('object_key', 'other/key')]:
            with self.subTest(field=field):
                old = self.records[1][field]; self.records[1][field] = value
                with self.assertRaises(RuntimeError): self.pull()
                self.assert_seed_unchanged(); self.records[1][field] = old

    def test_second_ledger_checksum_mismatch_and_bad_gzip(self):
        key = self.records[1]['object_key']
        for payload in (gzip.compress(b'{}'), b'not gzip'):
            with self.subTest(payload=payload[:10]):
                self.objects[key] = payload
                with self.assertRaises((RuntimeError, gzip.BadGzipFile)): self.pull()
                self.assert_seed_unchanged()

    def test_invalid_json_and_nonobject_payload(self):
        for raw in (b'not json', b'[]'):
            digest = hashlib.sha256(raw).hexdigest()
            key = f'operational/v1/objects/{self.paths[1].name}/{digest}.json.gz'
            self.records[1].update(sha256=digest, object_key=key)
            self.objects[key] = gzip.compress(raw)
            with self.assertRaises((ValueError, RuntimeError)): self.pull()
            self.assert_seed_unchanged()

    def test_missing_local_ledger_cannot_advance_manifest(self):
        self.paths[1].unlink()
        with patch('scripts.sync_operational_ledgers_r2._wrangler_object', side_effect=self.transfer):
            with self.assertRaises(RuntimeError):
                push_ledgers(bucket='research', prefix='operational/v1', paths=self.paths)
        self.assertNotIn(('put', 'operational/v1/manifest.json'), self.calls)

    def test_failed_second_upload_cannot_advance_manifest(self):
        self.pull()
        self.calls.clear()
        self.error_key = self.records[1]['object_key']
        self.error = subprocess.CalledProcessError(1, ['mock'])
        with patch('scripts.sync_operational_ledgers_r2._wrangler_object', side_effect=self.transfer):
            with self.assertRaises(subprocess.CalledProcessError):
                push_ledgers(bucket='research', prefix='operational/v1', paths=self.paths)
        self.assertNotIn(('put', 'operational/v1/manifest.json'), self.calls)


class WorkflowRestoreGatesTests(unittest.TestCase):
    def blocks(self, name):
        root = Path(__file__).resolve().parents[2]
        return (root / '.github/workflows' / name).read_text().split('\n      - name: ')

    def test_normal_workflows_require_complete_restore(self):
        for name in ('orographic_scan.yml', 'orographic_outcome_capture.yml'):
            blocks = self.blocks(name)
            restore = next(x for x in blocks if x.startswith('Restore mutable research ledgers'))
            self.assertIn('id: restore_ledgers', restore)
            self.assertNotIn('--allow-missing', restore)
            publish = next(x for x in blocks if x.startswith('Publish mutable research ledgers'))
            self.assertIn("steps.restore_ledgers.outcome == 'success'", publish)

    def test_capture_ledger_derived_outputs_require_restore(self):
        for block in self.blocks('orographic_outcome_capture.yml'):
            if any(block.startswith(n) for n in ('Refresh production', 'Build capture', 'Build compact', 'Publish mutable', 'Deploy refreshed')):
                self.assertIn("steps.restore_ledgers.outcome == 'success'", block)
        deploy = next(x for x in self.blocks('orographic_outcome_capture.yml') if x.startswith('Deploy refreshed'))
        self.assertIn("steps.publish_ledgers.outcome == 'success'", deploy)

    def test_scan_always_run_health_publication_requires_restore(self):
        for block in self.blocks('orographic_scan.yml'):
            if any(block.startswith(n) for n in ('Write scan health', 'Commit scan health', 'Push scan health', 'Deploy scan health')):
                self.assertIn("steps.restore_ledgers.outcome == 'success'", block)

    def test_restore_failure_remains_visible_without_stale_health(self):
        block = next(x for x in self.blocks('orographic_outcome_capture.yml') if x.startswith('Surface operational restore failure'))
        self.assertIn("steps.restore_ledgers.outcome != 'success'", block)
        self.assertIn('exit 1', block)


if __name__ == '__main__': unittest.main()
