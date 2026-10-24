"""Synthetic large files exercise routing, credentials and managed multipart."""
import hashlib
import json
import os
from pathlib import Path
from unittest import mock

import boto3
from botocore.config import Config
from botocore.stub import ANY, Stubber
import pytest

from scripts import r2_multipart as multipart
from scripts import upload_research_artifacts_to_r2 as upload
from engine.orographic import evidence_store


def sparse(path, size):
    with path.open("wb") as handle:
        handle.truncate(size)
    return path


def test_431_mib_routing_preserves_destination_and_file(tmp_path):
    path = sparse(tmp_path / "ledger.json", 431 * 1024 * 1024)
    with mock.patch.object(upload, "upload_large_object") as large, \
         mock.patch.object(upload.subprocess, "run") as small:
        upload._put_object("bucket", "immutable/generation/ledger.json", path)
    large.assert_called_once_with("bucket", "immutable/generation/ledger.json", path)
    small.assert_not_called()
    assert path.stat().st_size == 431 * 1024 * 1024


@pytest.mark.parametrize("delta", [-1, 0])
def test_wrangler_boundary_unchanged(tmp_path, delta):
    path = sparse(tmp_path / "boundary", multipart.WRANGLER_MAX_BYTES + delta)
    with mock.patch.object(upload, "upload_large_object") as large, \
         mock.patch.object(upload.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as small:
        upload._put_object("bucket", "key", path)
    large.assert_not_called()
    assert small.call_count == 1


@pytest.mark.parametrize("missing", ["CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"])
def test_missing_credentials_never_derives_or_discovers(missing):
    env = {"CLOUDFLARE_ACCOUNT_ID": "a" * 32,
           "R2_ACCESS_KEY_ID": "existing-id", "R2_SECRET_ACCESS_KEY": "existing-secret",
           "CLOUDFLARE_R2_API_TOKEN": "must-not-be-used"}
    del env[missing]
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("boto3.client") as factory:
        with pytest.raises(RuntimeError, match="No credentials were created or derived"):
            multipart.large_object_client()
    factory.assert_not_called()


def test_explicit_existing_credentials_fixed_origin_only():
    env = {"CLOUDFLARE_ACCOUNT_ID": "a" * 32,
           "R2_ACCESS_KEY_ID": "existing-id", "R2_SECRET_ACCESS_KEY": "existing-secret"}
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("boto3.client") as factory:
        multipart.large_object_client()
    kwargs = factory.call_args.kwargs
    assert kwargs["endpoint_url"] == "https://" + "a" * 32 + ".r2.cloudflarestorage.com"
    assert kwargs["aws_access_key_id"] == "existing-id"
    assert kwargs["aws_secret_access_key"] == "existing-secret"
    assert kwargs["config"].retries == {"mode": "standard", "total_max_attempts": 4}


def test_multipart_bounds_and_input_mutation(tmp_path):
    path = sparse(tmp_path / "ledger", 431 * 1024 * 1024)
    client = mock.Mock()
    with mock.patch.object(multipart, "large_object_client", return_value=client):
        multipart.upload_large_object("bucket", "key", path)
        config = client.upload_file.call_args.kwargs["Config"]
        assert config.multipart_chunksize == 64 * 1024 * 1024
        assert config.max_concurrency == 2
        assert config.max_io_queue == 2
        assert config.preferred_transfer_client == "classic"
        client.upload_file.side_effect = lambda *a, **kw: path.write_bytes(b"changed")
        with pytest.raises(RuntimeError, match="input changed"):
            multipart.upload_large_object("bucket", "key", path)


def test_no_automatic_part_size_growth(tmp_path):
    path = sparse(tmp_path / "too-large", multipart.PART_BYTES * multipart.MAX_PARTS + 1)
    with mock.patch.object(multipart, "large_object_client") as client:
        with pytest.raises(ValueError, match="bounded"):
            multipart.upload_large_object("bucket", "key", path)
    client.assert_not_called()


def sdk_client():
    return boto3.client("s3", endpoint_url="https://example.invalid", region_name="auto",
        aws_access_key_id="synthetic", aws_secret_access_key="synthetic",
        config=Config(request_checksum_calculation="when_required",
                      response_checksum_validation="when_required"))


def test_sdk_failure_aborts_its_own_incomplete_multipart(tmp_path):
    path = sparse(tmp_path / "ledger", 65 * 1024 * 1024)
    client = sdk_client()
    with Stubber(client) as stub:
        stub.add_response("create_multipart_upload", {"UploadId": "synthetic-upload"},
                          {"Bucket": "bucket", "Key": "immutable/key"})
        # A single worker makes the failure path deterministic while exercising
        # the real managed-transfer implementation, not a mocked upload_file.
        stub.add_client_error("upload_part", service_error_code="AccessDenied", http_status_code=403,
            expected_params={"Bucket": "bucket", "Key": "immutable/key", "UploadId": "synthetic-upload",
                             "PartNumber": 1, "Body": ANY})
        stub.add_response("abort_multipart_upload", {},
            {"Bucket": "bucket", "Key": "immutable/key", "UploadId": "synthetic-upload"})
        with mock.patch.object(multipart, "large_object_client", return_value=client), \
             mock.patch.object(multipart, "MAX_CONCURRENCY", 1):
            with pytest.raises(Exception, match="AccessDenied"):
                multipart.upload_large_object("bucket", "immutable/key", path)
        stub.assert_no_pending_responses()


def test_failure_leaves_current_manifest_untouched(tmp_path):
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{}")
    manifest = {"files": [{"path": "ledger.json"}]}
    (tmp_path / "evidence_manifest.json").write_text(json.dumps(manifest))
    with mock.patch.object(upload, "validate_canonical_bundle", return_value=manifest), \
         mock.patch.object(upload, "WRANGLER_MAX_BYTES", 1), \
         mock.patch.object(upload, "upload_large_object", side_effect=RuntimeError("blocked")) as large, \
         mock.patch.object(upload.subprocess, "run") as small:
        with pytest.raises(RuntimeError, match="blocked"):
            upload._upload_canonical(bucket="bucket", prefix="current", bundle=tmp_path)
    assert large.call_count == 1
    small.assert_not_called()


def test_compact_serialization_semantics_and_new_byte_hash(tmp_path):
    payload = {"entries": [{"id": "synthetic", "text": "snow \u2603", "nested": [None, False, 0, 1.25]}],
               "provenance": {"raw_source": "original"}}
    path = tmp_path / "ledger.json"
    pretty = json.dumps(payload, indent=2, sort_keys=True).encode()
    evidence_store._write_json(path, payload)
    compact = path.read_bytes()
    assert json.loads(compact) == json.loads(pretty) == payload
    assert compact == json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    assert len(compact) < len(pretty)
    assert evidence_store._artifact_record(path)["sha256"] == hashlib.sha256(compact).hexdigest()
    assert hashlib.sha256(compact).digest() != hashlib.sha256(pretty).digest()
