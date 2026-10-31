"""Synthetic archive integrity tests; no fits or promotion evidence."""
import hashlib
import json
import zipfile

import pytest

from scripts.audit_scout_input_archive import inspect_zip


def archive_fixture(tmp_path, *, corrupt=False):
    path = tmp_path / "retained.zip"
    raw = b'{"entries":[],"feature_available_at_utc":null}'
    manifest = {"files": [{"path": "ledger.json", "bytes": len(raw),
                           "sha256": hashlib.sha256(raw).hexdigest()}]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("output/canonical_evidence/evidence_manifest.json", json.dumps(manifest))
        z.writestr("output/canonical_evidence/ledger.json", raw + (b" " if corrupt else b""))
        z.writestr("output/research_datasets/strict_option_outcomes.json", '{"rows":[]}')
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_archive_requires_external_pin(tmp_path):
    path, _ = archive_fixture(tmp_path)
    with pytest.raises(ValueError, match="archive_hash_mismatch"):
        inspect_zip(path, "0" * 64)


def test_member_mismatch_rejected_even_with_correct_zip_pin(tmp_path):
    path, digest = archive_fixture(tmp_path, corrupt=True)
    with pytest.raises(ValueError, match="canonical_member_identity_mismatch"):
        inspect_zip(path, digest)


def test_inventory_does_not_turn_presence_into_evaluation(tmp_path):
    path, digest = archive_fixture(tmp_path)
    report = inspect_zip(path, digest)
    assert report["canonical_members_verified"] == 1
    assert report["complete_pairs"] == 0
    assert report["evaluation_performed"] is False
    assert report["completion_receipt_created"] is False
    ledger = next(row for row in report["files"] if row["path"].endswith("ledger.json"))
    assert ledger["json_key_occurrences"]["feature_available_at_utc"] == 1
    assert ledger["json_key_occurrences"]["mom_5d"] == 0
