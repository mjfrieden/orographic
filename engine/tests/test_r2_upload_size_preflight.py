"""Offline canonical publication tests; no Wrangler or R2 calls."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from scripts import upload_research_artifacts_to_r2 as upload


class CanonicalUploadSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        records = []
        for name, content in (("first.json", b'{"first":1}'), ("last.json", b'{"last":2}')):
            path = self.root / name
            path.write_bytes(content)
            records.append({"path": name, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        self.manifest_path = self.root / "evidence_manifest.json"
        self.manifest_path.write_text(json.dumps({"schema_version": 1, "checks": {"valid": True}, "files": records}))
        self.original = {p.name: p.read_bytes() for p in self.root.iterdir()}
        self.subprocess = mock.patch.object(upload.subprocess, "run", side_effect=AssertionError("No subprocess permitted"))
        self.subprocess.start()
        self.addCleanup(self.subprocess.stop)

    def publish(self):
        upload._upload_canonical(bucket="offline", prefix="canonical/current", bundle=self.root)

    def assert_unchanged(self):
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_exact_limit_and_below_are_allowed(self):
        for size in (0, upload.WRANGLER_MAX_UPLOAD_BYTES - 1, upload.WRANGLER_MAX_UPLOAD_BYTES):
            with self.subTest(size=size), mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_size=size)):
                upload._preflight_upload_sizes([Path("offline.json")])

    def test_one_byte_over_limit_is_rejected(self):
        with mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_size=upload.WRANGLER_MAX_UPLOAD_BYTES + 1)):
            with self.assertRaisesRegex(ValueError, "314572801 bytes.*314572800 bytes"):
                upload._preflight_upload_sizes([Path("offline.json")])

    def test_all_oversized_paths_reported(self):
        with mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_size=315427890)):
            with self.assertRaisesRegex(ValueError, "first.json: 315427890 bytes; last.json: 315427890 bytes"):
                upload._preflight_upload_sizes([Path("first.json"), Path("last.json")])

    def test_later_oversized_artifact_prevents_every_put(self):
        actual_stat = Path.stat
        def stat(path, *args, **kwargs):
            if path.name == "last.json":
                return SimpleNamespace(st_size=upload.WRANGLER_MAX_UPLOAD_BYTES + 1)
            return actual_stat(path, *args, **kwargs)
        # Keep the real validator, then simulate a size increase before preflight.
        real_validate = upload.validate_canonical_bundle
        def validate(root):
            manifest = real_validate(root)
            guard = mock.patch.object(Path, "stat", stat)
            guard.start()
            self.addCleanup(guard.stop)
            return manifest
        with mock.patch.object(upload, "validate_canonical_bundle", side_effect=validate), mock.patch.object(upload, "_put_object") as put:
            with self.assertRaisesRegex(ValueError, "last.json"):
                self.publish()
            put.assert_not_called()
        self.assert_unchanged()

    def test_serialized_manifest_is_preflighted_before_any_put(self):
        with mock.patch.object(upload, "WRANGLER_MAX_UPLOAD_BYTES", 100), mock.patch.object(upload, "_put_object") as put:
            with self.assertRaisesRegex(ValueError, "evidence_manifest.json"):
                self.publish()
            put.assert_not_called()
        self.assert_unchanged()

    def test_success_keeps_raw_bytes_generation_keys_and_manifest_last(self):
        objects = []
        with mock.patch.object(upload, "_put_object", side_effect=lambda bucket, key, path: objects.append((key, path.read_bytes()))):
            self.publish()
        generation = hashlib.sha256(self.original["evidence_manifest.json"]).hexdigest()
        self.assertEqual([key for key, _ in objects], [f"canonical/current-bundles/{generation}/first.json", f"canonical/current-bundles/{generation}/last.json", "canonical/current/evidence_manifest.json"])
        manifest = json.loads(objects[-1][1])
        for record, (key, content) in zip(manifest["files"], objects[:-1], strict=True):
            self.assertEqual(record["object_key"], key)
            self.assertEqual(content, self.original[record["path"]])
            self.assertEqual(record["sha256"], hashlib.sha256(content).hexdigest())
        self.assert_unchanged()

    def test_failed_put_never_commits_manifest(self):
        with mock.patch.object(upload, "_put_object", side_effect=[None, RuntimeError("upload failed")]) as put:
            with self.assertRaisesRegex(RuntimeError, "upload failed"):
                self.publish()
            self.assertEqual(put.call_count, 2)
            self.assertTrue(all("current-bundles/" in call.args[1] for call in put.call_args_list))
        self.assert_unchanged()

    def test_invalid_bundle_fails_before_any_put(self):
        (self.root / "last.json").write_bytes(b"corruption")
        with mock.patch.object(upload, "_put_object") as put:
            with self.assertRaisesRegex(ValueError, "Canonical evidence validation failed"):
                self.publish()
            put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
