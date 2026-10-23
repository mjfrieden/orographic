"""Independent local-only failure/receipt contracts; never contact any provider."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Callable

import pytest

from scripts import sync_operational_ledgers_r2 as sync

BUCKET = "synthetic-research"
PREFIX = "test/operational/v1"
MANIFEST_KEY = f"{PREFIX}/manifest.json"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalStore:
    def __init__(self, paths: tuple[Path, ...]):
        self.paths = paths
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.on_call: Callable | None = None
        records = []
        for index, path in enumerate(paths):
            raw = (json.dumps({"entries": [{"id": index}], "heartbeat": "original"}, indent=2) + "\n").encode()
            compressed = gzip.compress(raw, mtime=0)
            key = sync._object_key(PREFIX, path, digest(raw))
            self.objects[key] = compressed
            records.append({"path": str(path), "object_key": key, "sha256": digest(raw),
                            "bytes": len(raw), "compressed_bytes": len(compressed)})
        self.manifest = {"artifact": "orographic_operational_ledger_manifest", "schema_version": 1,
                         "prefix": PREFIX, "generated_at_utc": "2026-10-01T00:00:00+00:00", "ledgers": records}
        self.commit_manifest()

    def commit_manifest(self):
        self.objects[MANIFEST_KEY] = (json.dumps(self.manifest, indent=2) + "\n").encode()

    def call(self, action, *, bucket, key, file_path):
        assert bucket == BUCKET
        self.calls.append((action, key))
        if self.on_call:
            self.on_call(action, key, file_path)
        if action == "get":
            if key not in self.objects:
                raise sync.MissingObject(key)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(self.objects[key])
        elif action == "put":
            self.objects[key] = file_path.read_bytes()
        else:
            raise AssertionError(action)

    @property
    def puts(self):
        return [key for action, key in self.calls if action == "put"]


@pytest.fixture(autouse=True)
def deny_external_subprocess(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("A unit test attempted an external subprocess")
    monkeypatch.setattr(sync.subprocess, "run", deny)


@pytest.fixture
def setup_store(tmp_path, monkeypatch):
    paths = (tmp_path / "prospective.json", tmp_path / "research.json")
    for path in paths:
        path.write_bytes(b'{"entries": [], "sentinel": "local seed"}')
    state = tmp_path / "state" / "receipt.json"
    store = LocalStore(paths)
    monkeypatch.setattr(sync, "_wrangler_object", store.call)
    return store, paths, state


def pull(store, paths, state, **kwargs):
    return sync.pull_ledgers(bucket=BUCKET, prefix=PREFIX, paths=paths, state_path=state, **kwargs)


def push(store, paths, state, **kwargs):
    return sync.push_ledgers(bucket=BUCKET, prefix=PREFIX, paths=paths, state_path=state, **kwargs)


def read_state(state):
    return json.loads(state.read_text())


@pytest.mark.parametrize("message", ["HTTP 404", "NoSuchBucket", "Authentication error [code: 10000]", "503 Service unavailable", "connection reset", "request timed out", "NoSuchKey"])
def test_unrecognized_wrangler_failures_never_become_bootstrap(message, tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["npx", "wrangler"], output="", stderr=message)
    monkeypatch.setattr(sync.subprocess, "run", fail)
    state = tmp_path / "receipt.json"
    with pytest.raises(subprocess.CalledProcessError):
        sync.pull_ledgers(bucket=BUCKET, prefix=PREFIX, allow_missing=True, paths=(), state_path=state)
    assert read_state(state) == {"verified": False}


def test_specific_missing_key_requires_explicit_bootstrap(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["npx", "wrangler"], output="", stderr="[ERROR] The specified key does not exist.")
    monkeypatch.setattr(sync.subprocess, "run", fail)
    state = tmp_path / "receipt.json"
    with pytest.raises(sync.MissingObject):
        sync.pull_ledgers(bucket=BUCKET, prefix=PREFIX, paths=(), state_path=state)
    assert read_state(state)["verified"] is False
    assert sync.pull_ledgers(bucket=BUCKET, prefix=PREFIX, paths=(), state_path=state, allow_missing=True) == 0
    assert read_state(state)["bootstrap"] is True


def test_missing_key_text_on_put_is_not_a_missing_read(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, ["npx", "wrangler"], stderr="The specified key does not exist.")
    monkeypatch.setattr(sync.subprocess, "run", fail)
    with pytest.raises(subprocess.CalledProcessError):
        sync._wrangler_object("put", bucket=BUCKET, key=MANIFEST_KEY, file_path=tmp_path / "file")


@pytest.mark.parametrize("mutation", [
    lambda m: m.update(ledgers=[]),
    lambda m: m.update(ledgers=None),
    lambda m: m["ledgers"].append(None),
    lambda m: m["ledgers"].append(dict(m["ledgers"][0])),
    lambda m: m.update(schema_version=2),
    lambda m: m.update(prefix="different/prefix"),
    lambda m: m["ledgers"][0].update(object_key="unrelated/private/key"),
    lambda m: m["ledgers"][0].update(sha256="not-a-digest"),
    lambda m: m["ledgers"][0].update(bytes=True),
    lambda m: m["ledgers"][0].update(compressed_bytes=-1),
])
def test_invalid_manifest_cannot_restore_any_ledger(setup_store, mutation):
    store, paths, state = setup_store
    before = [path.read_bytes() for path in paths]
    mutation(store.manifest)
    store.commit_manifest()
    with pytest.raises((RuntimeError, ValueError)):
        pull(store, paths, state)
    assert [path.read_bytes() for path in paths] == before
    assert store.calls == [("get", MANIFEST_KEY)]
    assert read_state(state)["verified"] is False


def test_second_blob_failure_preserves_every_local_destination(setup_store):
    store, paths, state = setup_store
    before = [path.read_bytes() for path in paths]
    second_key = store.manifest["ledgers"][1]["object_key"]
    def fail_second(action, key, file_path):
        if action == "get" and key == second_key:
            raise subprocess.CalledProcessError(1, ["synthetic"], stderr="503")
    store.on_call = fail_second
    with pytest.raises(subprocess.CalledProcessError):
        pull(store, paths, state)
    assert [path.read_bytes() for path in paths] == before
    assert read_state(state)["verified"] is False
    with pytest.raises(RuntimeError, match="successful restore receipt"):
        push(store, paths, state)
    assert store.puts == []


def test_second_blob_checksum_failure_preserves_every_local_destination(setup_store):
    store, paths, state = setup_store
    before = [path.read_bytes() for path in paths]
    record = store.manifest["ledgers"][1]
    changed = gzip.compress(b'{"entries": ["corrupt"]}', mtime=0)
    store.objects[record["object_key"]] = changed
    record["compressed_bytes"] = len(changed)
    store.commit_manifest()
    with pytest.raises(RuntimeError, match="size mismatch|Checksum"):
        pull(store, paths, state)
    assert [path.read_bytes() for path in paths] == before
    assert read_state(state)["verified"] is False


def test_partial_install_never_issues_successful_receipt(setup_store, monkeypatch):
    store, paths, state = setup_store
    original_replace = Path.replace
    def fail_second(self, target):
        if Path(target) == paths[1]:
            raise OSError("synthetic local disk failure")
        return original_replace(self, target)
    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(OSError, match="local disk failure"):
        pull(store, paths, state)
    assert read_state(state)["verified"] is False
    with pytest.raises(RuntimeError, match="successful restore receipt"):
        push(store, paths, state)
    assert store.puts == []


def test_failed_new_restore_invalidates_previous_success(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    assert read_state(state)["verified"] is True
    def failed_read(action, key, file_path):
        if action == "get":
            raise subprocess.CalledProcessError(1, ["synthetic"], stderr="403")
    store.on_call = failed_read
    with pytest.raises(subprocess.CalledProcessError):
        pull(store, paths, state, allow_missing=True)
    assert read_state(state)["verified"] is False
    with pytest.raises(RuntimeError, match="successful restore receipt"):
        push(store, paths, state)
    assert store.puts == []


@pytest.mark.parametrize("scope_key, replacement", [("bucket", "different-bucket"), ("prefix", "other/v1"), ("paths", [])])
def test_scope_mismatch_rejected_before_provider_read(setup_store, scope_key, replacement):
    store, paths, state = setup_store
    pull(store, paths, state)
    receipt = read_state(state)
    receipt["scope"][scope_key] = replacement
    state.write_text(json.dumps(receipt))
    store.calls.clear()
    with pytest.raises(RuntimeError, match="exact storage scope"):
        push(store, paths, state)
    assert store.calls == []


def test_receipt_manifest_hash_mismatch_rejected_before_provider_read(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    receipt = read_state(state)
    receipt["manifest"] += " "
    state.write_text(json.dumps(receipt))
    store.calls.clear()
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        push(store, paths, state)
    assert store.calls == []


def test_stale_baseline_rejected_before_any_put(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    paths[0].write_text('{"entries":["new"]}')
    store.manifest["generated_at_utc"] = "2026-10-02T00:00:00+00:00"
    store.commit_manifest()
    with pytest.raises(RuntimeError, match="changed after restore"):
        push(store, paths, state)
    assert store.puts == []


def test_mid_upload_competing_manifest_never_overwritten(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    paths[0].write_text('{"entries":["new"]}')
    competing = None
    def race(action, key, file_path):
        nonlocal competing
        if action == "put" and "/objects/" in key:
            store.manifest["generated_at_utc"] = "2026-10-02T00:00:00+00:00"
            store.commit_manifest()
            competing = store.objects[MANIFEST_KEY]
    store.on_call = race
    with pytest.raises(RuntimeError, match="changed after restore"):
        push(store, paths, state)
    assert competing is not None
    assert store.objects[MANIFEST_KEY] == competing
    assert MANIFEST_KEY not in store.puts


def test_true_noop_does_not_publish_but_heartbeat_change_does(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    baseline = store.objects[MANIFEST_KEY]
    push(store, paths, state)
    assert store.puts == []
    payload = json.loads(paths[0].read_text())
    payload["last_capture_attempt_at_utc"] = "2026-10-23T00:00:00Z"
    payload["last_mark_summary"] = {"ledger_changed": 0}
    changed = (json.dumps(payload, indent=2) + "\n").encode()
    paths[0].write_bytes(changed)
    published = push(store, paths, state)
    assert len([key for key in store.puts if "/objects/" in key]) == 1
    assert store.puts[-1] == MANIFEST_KEY
    assert store.objects[f"{PREFIX}/manifests/{digest(baseline)}.json"] == baseline
    new_manifest = store.objects[MANIFEST_KEY]
    assert store.objects[f"{PREFIX}/manifests/{digest(new_manifest)}.json"] == new_manifest
    row = next(r for r in published["ledgers"] if r["path"] == str(paths[0]))
    assert gzip.decompress(store.objects[row["object_key"]]) == changed
    assert read_state(state)["manifest_sha256"] == digest(new_manifest)


def test_checkpoint_failure_leaves_current_pointer_and_receipt_unchanged(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    baseline = store.objects[MANIFEST_KEY]
    original_receipt = state.read_bytes()
    paths[0].write_text('{"entries":["new"]}')
    def fail_checkpoint(action, key, file_path):
        if action == "put" and "/manifests/" in key:
            raise OSError("synthetic checkpoint upload failure")
    store.on_call = fail_checkpoint
    with pytest.raises(OSError, match="checkpoint"):
        push(store, paths, state)
    assert store.objects[MANIFEST_KEY] == baseline
    assert state.read_bytes() == original_receipt
    assert MANIFEST_KEY not in store.puts


def test_uncertain_manifest_commit_retry_is_not_blind(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    baseline = store.objects[MANIFEST_KEY]
    original_receipt = state.read_bytes()
    paths[0].write_text('{"entries":["new"]}')
    def uncertain(action, key, file_path):
        if action == "put" and key == MANIFEST_KEY:
            store.objects[key] = file_path.read_bytes()
            raise OSError("synthetic lost response after committed PUT")
    store.on_call = uncertain
    with pytest.raises(OSError, match="lost response"):
        push(store, paths, state)
    assert store.objects[MANIFEST_KEY] != baseline
    assert state.read_bytes() == original_receipt
    store.on_call = None
    store.calls.clear()
    with pytest.raises(RuntimeError, match="changed after restore"):
        push(store, paths, state)
    assert store.puts == []


def test_bootstrap_cannot_overwrite_manifest_that_appears_later(setup_store):
    store, paths, state = setup_store
    baseline = store.objects.pop(MANIFEST_KEY)
    pull(store, paths, state, allow_missing=True)
    store.objects[MANIFEST_KEY] = baseline
    with pytest.raises(RuntimeError, match="changed after restore"):
        push(store, paths, state)
    assert store.puts == []


def test_oversize_archive_fails_before_first_put(setup_store, monkeypatch):
    store, paths, state = setup_store
    pull(store, paths, state)
    paths[0].write_text('{"entries":["new"]}')
    monkeypatch.setattr(sync, "MAX_ARCHIVE_BYTES", 5)
    with pytest.raises(RuntimeError, match="upload limit"):
        push(store, paths, state)
    assert store.puts == []


def test_missing_local_file_fails_before_first_put(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    paths[0].write_text('{"entries":["new"]}')
    # Rename within test sandbox avoids deleting any evidence.
    paths[1].rename(paths[1].with_suffix(".saved"))
    with pytest.raises(FileNotFoundError):
        push(store, paths, state)
    assert store.puts == []


def test_restore_checkpoint_has_exact_original_bytes(setup_store):
    store, paths, state = setup_store
    pull(store, paths, state)
    before = [path.read_bytes() for path in paths]
    baseline = store.objects[MANIFEST_KEY]
    paths[0].write_text('{"entries":["new"]}')
    push(store, paths, state)
    # An explicit operator-controlled pointer selection, simulated locally only.
    store.objects[MANIFEST_KEY] = store.objects[f"{PREFIX}/manifests/{digest(baseline)}.json"]
    assert pull(store, paths, state) == 2
    assert [path.read_bytes() for path in paths] == before


def test_workflow_lock_is_global_and_mart_offline_checks_do_not_acquire_it():
    import re
    root = Path(__file__).resolve().parents[2]
    groups = []
    for name in ("orographic_scan.yml", "orographic_outcome_capture.yml", "recover_canonical_evidence.yml"):
        text = (root / ".github" / "workflows" / name).read_text()
        match = re.search(r"(?ms)^concurrency:\n(?P<body>(?:^[ \t]+[^\n]*\n)+)", text)
        assert match, name
        body = match.group("body")
        group = re.search(r"^  group: (.+)$", body, re.M).group(1)
        assert "github.ref" not in group
        assert "${{" not in group
        assert "  cancel-in-progress: false" in body
        groups.append(group)
    mart = (root / ".github/workflows/sync_shared_research_mart.yml").read_text()
    assert re.search(r"^concurrency:", mart, re.M) is None
    offline = mart.split("  offline-pr-check:\n", 1)[1].split("\n  sync:\n", 1)[0]
    assert "concurrency:" not in offline
    sync_job = mart.split("\n  sync:\n", 1)[1]
    assert re.search(r"^    concurrency:\n      group: (.+)\n      cancel-in-progress: false", sync_job, re.M)
    groups.append(re.search(r"^      group: (.+)$", sync_job, re.M).group(1))
    assert len(set(groups)) == 1
    assert "if: ${{ github.event_name == 'schedule' || github.event_name == 'workflow_dispatch' }}" in sync_job


def test_production_restore_never_bootstraps_and_capture_publication_uses_restore_receipt_gate():
    root = Path(__file__).resolve().parents[2]
    for name in ("orographic_scan.yml", "orographic_outcome_capture.yml"):
        text = (root / ".github/workflows" / name).read_text()
        step = text.split("      - name: Restore mutable research ledgers from R2\n", 1)[1].split("\n      - name:", 1)[0]
        assert "id: operational_restore" in step
        assert "sync_operational_ledgers_r2.py pull" in step
        assert "--allow-missing" not in step
        assert "--bootstrap-from-seed" not in step
    outcome = (root / ".github/workflows/orographic_outcome_capture.yml").read_text()
    publication = outcome.split("      - name: Publish mutable research ledgers to R2\n", 1)[1].split("\n      - name:", 1)[0]
    assert "if: ${{ !cancelled() && steps.operational_restore.outcome == 'success' }}" in publication
    assert "steps.capture_prospective" not in publication


def test_staged_source_with_preserved_mtime_cannot_publish_unvalidated_bytes(setup_store, monkeypatch):
    import os
    store, paths, state = setup_store
    pull(store, paths, state)
    paths[0].write_bytes(paths[0].read_bytes().replace(b"original", b"changed!"))
    original_stat = paths[0].stat()
    original_size = original_stat.st_size
    original_load = sync.json.load
    mutated = False
    def change_after_validation(stream, *args, **kwargs):
        nonlocal mutated
        payload = original_load(stream, *args, **kwargs)
        if not mutated and getattr(stream, "name", None) == str(paths[0]):
            mutated = True
            # Simulate an in-place writer preserving file length and timestamp.
            paths[0].write_bytes(b'[]' + b' ' * (original_size - 2))
            os.utime(paths[0], ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return payload
    monkeypatch.setattr(sync.json, "load", change_after_validation)
    with pytest.raises((RuntimeError, ValueError)):
        push(store, paths, state)
    assert mutated
    assert store.puts == []


def load_original_v1_module():
    import importlib.util
    # Exact prior repository implementation, bundled without data or secrets.
    path = Path(__file__).parent / "fixtures/original_operational_ledgers_v1.py"
    spec = importlib.util.spec_from_file_location("original_operational_v1_review", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_new_reader_consumes_exact_original_v1_writer_output_with_retired_paths(tmp_path, monkeypatch):
    old = load_original_v1_module()
    monkeypatch.chdir(tmp_path)
    active = sync.LEDGER_PATHS
    legacy_paths = active + tuple(sorted(sync.RETIRED_LEDGER_PATHS))
    expected = {}
    for index, path in enumerate(legacy_paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = (json.dumps({"artifact": "synthetic-legacy", "entries": [{"id": index}], "unicode": "✓"}, ensure_ascii=False, indent=2) + "\n").encode()
        path.write_bytes(raw)
        expected[path] = raw
    store = LocalStore(active)
    monkeypatch.setattr(old, "_wrangler_object", store.call)
    monkeypatch.setattr(sync, "_wrangler_object", store.call)
    old_manifest = old.push_ledgers(bucket=BUCKET, prefix=PREFIX, paths=legacy_paths)
    assert len(old_manifest["ledgers"]) == 4
    for path in active:
        path.write_text('{"seed":"must be replaced"}')
    state = tmp_path / "state/receipt.json"
    assert sync.pull_ledgers(bucket=BUCKET, prefix=PREFIX, paths=active, state_path=state) == 2
    assert [path.read_bytes() for path in active] == [expected[path] for path in active]
    active[0].write_text('{"entries":["one new observation"]}')
    current = sync.push_ledgers(bucket=BUCKET, prefix=PREFIX, paths=active, state_path=state)
    assert len(current["ledgers"]) == 4
    retired_before = [r for r in old_manifest["ledgers"] if Path(r["path"]) in sync.RETIRED_LEDGER_PATHS]
    retired_after = [r for r in current["ledgers"] if Path(r["path"]) in sync.RETIRED_LEDGER_PATHS]
    assert retired_after == retired_before


def test_original_v1_reader_consumes_exact_new_v1_writer_output(setup_store, monkeypatch):
    store, paths, state = setup_store
    old = load_original_v1_module()
    monkeypatch.setattr(old, "_wrangler_object", store.call)
    pull(store, paths, state)
    changed = b'{"entries":[{"utf8":"\xe2\x9c\x93","spelling":1.00}],"last_capture_attempt_at_utc":"2026-10-23T00:00:00Z"}\n'
    paths[0].write_bytes(changed)
    expected = [path.read_bytes() for path in paths]
    push(store, paths, state)
    for path in paths:
        path.write_text('{"seed":"replace"}')
    assert old.pull_ledgers(bucket=BUCKET, prefix=PREFIX, paths=paths) == 2
    assert [path.read_bytes() for path in paths] == expected
