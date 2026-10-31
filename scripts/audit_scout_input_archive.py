"""Audit an externally hash-pinned retained ZIP; never fit or manufacture inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.scout_pair_readiness import _pair_records, _record_hash

FIELDS = ("mom_5d", "mom_10d", "rv20", "feature_observed_at_utc",
          "feature_available_at_utc", "feature_source_sha256", "model_artifacts")
PATTERNS = {name: re.compile(rb'"' + name.encode() + rb'"\s*:') for name in FIELDS}


def inspect_zip(path: Path, expected_sha256: str) -> dict:
    with path.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or digest != expected_sha256:
        raise ValueError("archive_hash_mismatch")
    rows = []
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate_archive_members")
        prefix = "output/canonical_evidence/"
        manifest = json.loads(archive.read(prefix + "evidence_manifest.json"))
        for record in manifest["files"]:
            relative = record["path"]
            if Path(relative).is_absolute() or ".." in Path(relative).parts or "\\" in relative:
                raise ValueError("unsafe_manifest_path")
            info = archive.getinfo(prefix + relative)
            with archive.open(info) as source:
                observed = hashlib.file_digest(source, "sha256").hexdigest()
            if info.file_size != record["bytes"] or observed != record["sha256"]:
                raise ValueError("canonical_member_identity_mismatch")
        # JSON key inventory scans bounded chunks, including boundary overlaps.
        # It is discovery evidence, never proof that a feature is timely/valid.
        for info in archive.infolist():
            if info.is_dir():
                continue
            row = {"path": info.filename, "bytes": info.file_size}
            if info.filename.endswith(".json"):
                counts = dict.fromkeys(FIELDS, 0)
                with archive.open(info) as source:
                    tail = b""
                    while chunk := source.read(1024 * 1024):
                        combined = tail + chunk
                        for name, pattern in PATTERNS.items():
                            counts[name] += sum(match.end() > len(tail) for match in pattern.finditer(combined))
                        tail = combined[-128:]
                row["json_key_occurrences"] = counts
            elif info.filename.endswith(".parquet"):
                import pyarrow.parquet as pq
                with archive.open(info) as source:
                    row["columns"] = pq.ParquetFile(source).schema_arrow.names
            rows.append(row)
        outcomes = json.loads(archive.read("output/research_datasets/strict_option_outcomes.json"))
        pairs, _ = _pair_records(outcomes["rows"])
    return {"artifact": "scout_archive_input_inventory", "schema_version": 1,
            "archive_sha256": digest, "canonical_members_verified": len(manifest["files"]),
            "complete_pairs": len(pairs), "independent_dates": len({r["decision_date"] for r in pairs}),
            "pair_corpus_sha256": _record_hash(pairs), "files": rows,
            "evidence_classification": "retained_real_data_inventory_not_evaluation",
            "evaluation_performed": False, "completion_receipt_created": False,
            "qualification": "Key/schema presence is discovery only. Missing core vectors or clocks must be recovered from original archives; scores and same-date clocks are not replacements."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect_zip(args.archive, args.sha256)
    # Preserve earlier audit observations; callers choose a new output identity.
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(json.dumps({k: v for k, v in report.items() if k != "files"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
