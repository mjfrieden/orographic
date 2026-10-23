"""Synthetic-only storage tests; no providers or credentials."""
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from scripts import sync_operational_ledgers_r2 as s

class MemoryStore:
    def __init__(self): self.objects, self.calls, self.fail_put = {}, [], None
    def object(self, action, *, bucket, key, file_path):
        self.calls.append((action, key))
        if action == "put":
            if self.fail_put and self.fail_put(key): raise RuntimeError("synthetic put failure")
            self.objects[key] = file_path.read_bytes()
        else:
            if key not in self.objects: raise s.MissingObject(key)
            file_path.write_bytes(self.objects[key])

class OperationalLedgersR2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.paths = (self.root / "prospective.json", self.root / "research.json")
        for i,p in enumerate(self.paths): p.write_text(json.dumps({"artifact":"synthetic", "entries":[{"id":i}], "unicode":"✓"},indent=2)+"\n")
        self.state = self.root/"receipt.json"; self.store = MemoryStore()
        self.mock = patch.object(s,"_wrangler_object",side_effect=self.store.object); self.mock.start(); self.addCleanup(self.mock.stop)
    def pull(self, **kw): return s.pull_ledgers(bucket="research",prefix="operational/v1",paths=self.paths,state_path=self.state,**kw)
    def push(self): return s.push_ledgers(bucket="research",prefix="operational/v1",paths=self.paths,state_path=self.state)
    def bootstrap(self): self.pull(allow_missing=True); return self.push()
    def puts(self): return [key for action,key in self.store.calls if action == "put"]
    def test_content_addressed_round_trip_publishes_manifest_last(self):
        expected = [p.read_bytes() for p in self.paths]; manifest = self.bootstrap()
        self.assertEqual(self.puts()[-1],"operational/v1/manifest.json")
        self.assertTrue(any("/manifests/" in key for key in self.puts()))
        for p in self.paths: p.write_text("stale local data")
        self.assertEqual(self.pull(),2); self.assertEqual([p.read_bytes() for p in self.paths],expected)
        for rec,raw in zip(manifest["ledgers"],expected): self.assertEqual(gzip.decompress(self.store.objects[rec["object_key"]]),raw)
    def test_unchanged_uploads_nothing(self):
        previous=self.bootstrap(); self.store.calls.clear()
        self.assertEqual(self.push(),previous); self.assertEqual([a for a,k in self.store.calls],["get"])
    def test_changed_uploads_one_archive_and_preserves_manifest_history(self):
        previous=self.bootstrap(); old=self.store.objects["operational/v1/manifest.json"]
        self.paths[0].write_text('{"entries":[1,2]}'); self.store.calls.clear(); current=self.push()
        self.assertEqual(sum("/objects/" in key for key in self.puts()),1)
        self.assertEqual(current["ledgers"][1],previous["ledgers"][1])
        self.assertIn(old,[v for k,v in self.store.objects.items() if "/manifests/" in k])
        self.assertEqual(self.puts()[-1],"operational/v1/manifest.json")
    def test_push_requires_successful_restore(self):
        with self.assertRaises(FileNotFoundError): self.push()
        self.assertEqual(self.store.calls,[])
        with self.assertRaises(s.MissingObject): self.pull()
        with self.assertRaisesRegex(RuntimeError,"successful restore"): self.push()
    def test_only_explicit_missing_key_can_bootstrap(self):
        for error in (subprocess.CalledProcessError(1,["wrangler"],stderr="403"), TimeoutError(), subprocess.CalledProcessError(1,["wrangler"],stderr="404")):
            with self.subTest(error=error), patch.object(s,"_wrangler_object",side_effect=error), self.assertRaises(type(error)): self.pull(allow_missing=True)
            self.assertFalse(json.loads(self.state.read_text())["verified"])
    def test_missing_second_object_does_not_partially_restore(self):
        manifest=self.bootstrap(); self.store.objects.pop(manifest["ledgers"][1]["object_key"])
        for p in self.paths: p.write_text('{"local":"must stay"}')
        before=[p.read_bytes() for p in self.paths]
        with self.assertRaises(s.MissingObject): self.pull(allow_missing=True)
        self.assertEqual([p.read_bytes() for p in self.paths],before)
        with self.assertRaises(RuntimeError): self.push()
    def test_invalid_or_missing_local_file_blocks_all_puts(self):
        self.bootstrap(); self.paths[0].write_text('{"changed":true}')
        for raw in ("bad json","[]"):
            self.paths[1].write_text(raw); self.store.calls.clear()
            with self.assertRaises((ValueError,RuntimeError)): self.push()
            self.assertEqual(self.puts(),[])
        self.paths[1].unlink(); self.store.calls.clear()
        with self.assertRaises(FileNotFoundError): self.push()
        self.assertEqual(self.puts(),[])
    def test_stale_baseline_blocks_upload(self):
        self.bootstrap(); self.paths[0].write_text('{"changed":true}'); self.store.objects["operational/v1/manifest.json"] += b" "
        self.store.calls.clear()
        with self.assertRaisesRegex(RuntimeError,"changed after restore"): self.push()
        self.assertEqual(self.puts(),[])
    def test_failed_archive_or_checkpoint_keeps_pointer(self):
        for segment in ("/objects/","/manifests/"):
            self.store.fail_put=None; self.bootstrap(); old=self.store.objects["operational/v1/manifest.json"]
            self.paths[0].write_text(json.dumps({"change":segment})); self.store.fail_put=lambda key: segment in key
            with self.assertRaisesRegex(RuntimeError,"synthetic put failure"): self.push()
            self.assertEqual(self.store.objects["operational/v1/manifest.json"],old)
    def test_archive_size_limit_checked_before_put(self):
        self.bootstrap(); self.paths[0].write_text('{"changed":"exceeds tiny test limit"}'); self.store.calls.clear()
        with patch.object(s,"MAX_ARCHIVE_BYTES",10), self.assertRaisesRegex(RuntimeError,"upload limit"): self.push()
        self.assertEqual(self.puts(),[])
    def test_invalid_manifest_blocks_all_object_reads(self):
        original=self.bootstrap()
        for mode in ("key","duplicate","partial","version"):
            m=json.loads(json.dumps(original))
            if mode=="key": m["ledgers"][0]["object_key"]="other/private"
            elif mode=="duplicate": m["ledgers"].append(m["ledgers"][0])
            elif mode=="partial": m["ledgers"].pop()
            else: m["schema_version"]=2
            self.store.objects["operational/v1/manifest.json"]=json.dumps(m).encode(); self.store.calls.clear()
            with self.assertRaises(RuntimeError): self.pull()
            self.assertEqual(len(self.store.calls),1)
            self.assertFalse(json.loads(self.state.read_text())["verified"])

class WranglerClassificationTests(unittest.TestCase):
    def test_only_known_missing_key_on_get_is_bootstrap_eligible(self):
        for action,message,expected in (("get","[ERROR] The specified key does not exist.",s.MissingObject),("get","HTTP 404 Not Found",subprocess.CalledProcessError),("get","The specified bucket does not exist.",subprocess.CalledProcessError),("get","Authentication error",subprocess.CalledProcessError),("put","The specified key does not exist.",subprocess.CalledProcessError)):
            with self.subTest(action=action,message=message):
                error=subprocess.CalledProcessError(1,["wrangler"],stderr=message)
                with patch.object(s.subprocess,"run",side_effect=error), self.assertRaises(expected): s._wrangler_object(action,bucket="synthetic",key="manifest.json",file_path=Path("unused"))
