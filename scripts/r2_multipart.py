"""Bounded, byte-preserving R2 upload using already provisioned S3 credentials.

Never derive credentials from the REST token or use ambient AWS/metadata
credential discovery. Missing configuration fails before any external request.
"""
from __future__ import annotations

import os
from pathlib import Path
import re

WRANGLER_MAX_BYTES = 300 * 1024 * 1024
PART_BYTES = 64 * 1024 * 1024
MAX_PARTS = 10_000
MAX_CONCURRENCY = 2


def large_object_client():
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not access_key or not secret_key:
        raise RuntimeError(
            "Large R2 upload requires existing CLOUDFLARE_ACCOUNT_ID, "
            "R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY. No credentials were "
            "created or derived; the current manifest has not advanced."
        )
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3", endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        region_name="auto",
        config=Config(
            retries={"mode": "standard", "total_max_attempts": 4},
            connect_timeout=30, read_timeout=120,
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def upload_large_object(bucket: str, object_key: str, file_path: Path) -> None:
    before = file_path.stat()
    if before.st_size > PART_BYTES * MAX_PARTS:
        # s3transfer otherwise enlarges parts automatically, breaking our bound.
        raise ValueError("R2 object exceeds the bounded 64 MiB / 10,000-part limit")
    client = large_object_client()
    from boto3.s3.transfer import TransferConfig

    client.upload_file(
        str(file_path), bucket, object_key,
        Config=TransferConfig(
            multipart_threshold=PART_BYTES, multipart_chunksize=PART_BYTES,
            max_concurrency=MAX_CONCURRENCY, max_io_queue=2,
            io_chunksize=1024 * 1024, preferred_transfer_client="classic",
        ),
    )
    after = file_path.stat()
    identity = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    if identity(before) != identity(after):
        raise RuntimeError("R2 upload input changed; refusing to publish its manifest")
