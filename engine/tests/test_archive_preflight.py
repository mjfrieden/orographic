"""Software-only upload preflight and manifest commit-point regressions."""
import json
import os
from unittest.mock import patch

import pytest

from scripts import r2_multipart as multipart
from scripts import upload_research_artifacts_to_r2 as upload
from scripts.preflight_research_archive import inspect_archive
from engine.tests.test_r2_transfer_contracts import make_bundle


def test_mixed_archive_missing_s3_blocks_before_small_object_write(tmp_path):
    (tmp_path / "small").write_bytes(b"x")
    large = tmp_path / "large"
    with large.open("wb") as handle:
        handle.truncate(multipart.WRANGLER_MAX_BYTES + 1)
    with patch.dict(os.environ, {"CLOUDFLARE_API_TOKEN": "existing-rest-only"}, clear=True), \
            patch.object(upload, "_put_object") as put, patch("boto3.client") as client:
        with pytest.raises(RuntimeError, match="before remote writes"):
            upload._upload_archive(bucket="bucket", prefix="prefix", paths=[tmp_path],
                                   account_id="", api_token="")
    put.assert_not_called()
    client.assert_not_called()


def test_preflight_reports_exact_transport_and_names_without_secrets(tmp_path):
    small = tmp_path / "small"
    small.write_bytes(b"x")
    large = tmp_path / "large"
    with large.open("wb") as handle:
        handle.truncate(multipart.WRANGLER_MAX_BYTES + 1)
    with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "a" * 32,
                                "R2_ACCESS_KEY_ID": "private-value"}, clear=True):
        result = multipart.preflight_files([small, large])
    assert result["missing_or_invalid_configuration"] == ["R2_SECRET_ACCESS_KEY"]
    assert result["status"] == "blocked"
    assert [r["transport"] for r in result["files"]] == ["wrangler", "multipart"]
    assert "private-value" not in json.dumps(result)
    assert result["publication_verified"] is False
    assert result["remote_requests_performed"] is False


def test_small_archive_needs_no_s3_and_preflight_is_not_publication(tmp_path):
    bundle, _ = make_bundle(tmp_path)
    with patch.dict(os.environ, {}, clear=True), patch("boto3.client") as client:
        result = inspect_archive(bundle, [tmp_path / "source.json"])
    assert result["status"] == "configuration_ready"
    assert result["publication_verified"] is False
    client.assert_not_called()


@pytest.mark.parametrize("kind", ["missing", "empty"])
def test_missing_and_empty_archives_never_publish(tmp_path, kind):
    paths = [tmp_path / "absent"] if kind == "missing" else [tmp_path]
    with patch.object(upload, "_put_object") as put:
        with pytest.raises((ValueError, FileNotFoundError)):
            upload._upload_archive(bucket="bucket", prefix="prefix", paths=paths,
                                   account_id="", api_token="")
    put.assert_not_called()


def test_mutated_archive_cannot_publish_manifest(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"original")
    written = []
    def mutate(bucket, key, path):
        written.append(key)
        source.write_bytes(b"changed")
    with patch.object(upload, "_put_object", side_effect=mutate):
        with pytest.raises(RuntimeError, match="manifest not published"):
            upload._upload_archive(bucket="bucket", prefix="prefix", paths=[source],
                                   account_id="", api_token="")
    assert len(written) == 1
    assert not any(key.endswith("/manifest.json") for key in written)


def test_canonical_missing_config_does_not_publish_manifest(tmp_path):
    bundle, _ = make_bundle(tmp_path)
    with patch.object(multipart, "WRANGLER_MAX_BYTES", 1), \
            patch.dict(os.environ, {}, clear=True), patch.object(upload, "_put_object") as put, \
            patch.object(upload.shutil, "copyfile") as copy:
        with pytest.raises(RuntimeError, match="before remote writes"):
            upload._upload_canonical(bucket="bucket", prefix="current", bundle=bundle)
    put.assert_not_called()
    copy.assert_not_called()


def test_scan_preflight_covers_canonical_before_either_upload():
    from pathlib import Path
    workflow = Path(".github/workflows/orographic_scan.yml").read_text()
    block = workflow.split("- name: Upload research data to R2", 1)[1].split("- name:", 1)[0]
    assert block.index("preflight_research_archive.py") < block.index("upload_research_artifacts_to_r2.py")
    assert "--canonical output/canonical_evidence" in block
    assert "output/r2_archive_preflight.json" in workflow.split("id: canonical-artifact", 1)[1]


def test_archive_failure_cannot_suppress_independent_operational_publication():
    from pathlib import Path
    workflow = Path(".github/workflows/orographic_scan.yml").read_text()
    operational = workflow.split("- name: Publish mutable research ledgers to R2", 1)[1].split("- name:", 1)[0]
    assert workflow.index("- name: Publish mutable research ledgers to R2") < workflow.index("preflight_research_archive.py")
    assert "sync_operational_ledgers_r2.py push" in operational
    assert "upload_r2" not in operational and "R2_ACCESS_KEY_ID" not in operational
    archive = workflow.split("- name: Upload research data to R2", 1)[1].split("- name:", 1)[0]
    assert "continue-on-error: true" in archive
    fallback = workflow.split("- name: Upload live research data artifact", 1)[1].split("- name:", 1)[0]
    assert "always()" in fallback
    capture = Path(".github/workflows/orographic_outcome_capture.yml").read_text()
    assert "sync_operational_ledgers_r2.py push" in capture
    assert "preflight_research_archive.py" not in capture


@pytest.mark.parametrize("mode", ["canonical", "archive"])
def test_missing_bucket_is_not_success(mode):
    from argparse import Namespace
    with patch.object(upload, "parse_args", return_value=Namespace(bucket="", mode=mode)), \
            patch.object(upload, "_put_object") as put:
        assert upload.main() == 1
    put.assert_not_called()


@pytest.mark.parametrize("invalid", [False, True])
def test_cli_persists_blocker_and_returns_failure(tmp_path, invalid):
    from scripts import preflight_research_archive as cli
    output = tmp_path / "preflight.json"
    argv = ["preflight", "--canonical", "canonical", "--output", str(output), "archive"]
    report = {"status": "blocked", "publication_verified": False,
              "missing_or_invalid_configuration": ["R2_ACCESS_KEY_ID"]}
    with patch("sys.argv", argv), patch.object(cli, "inspect_archive",
            side_effect=ValueError("invalid manifest") if invalid else None, return_value=report):
        assert cli.main() == 1
    saved = json.loads(output.read_text())
    assert saved["status"] == ("invalid_local_inputs" if invalid else "blocked")
    assert saved["publication_verified"] is False
