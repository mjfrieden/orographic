from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from engine.orographic.iceberg_mart import build_iceberg_publication_plan, verify_iceberg_mart
from engine.orographic.shared_research_mart import TABLE_CONTRACTS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IcebergMartTests(unittest.TestCase):
    def _mart(self, root: Path, *, sources: tuple[str, ...]) -> Path:
        artifacts = {}
        for name, contract in TABLE_CONTRACTS.items():
            frame = pd.DataFrame(columns=contract.columns)
            path = root / f"{name}.parquet"
            frame.to_parquet(path, index=False)
            artifacts[name] = {
                "path": path.name, "rows": 0, "sha256": _sha(path),
                "primary_key": list(contract.primary_key), "columns": list(contract.columns),
            }
        identity = {
            "schema_version": "cirrus_orographic_research_mart_v1",
            "sources": [{"source_system": source} for source in sources],
            "artifacts": artifacts,
            "validation": {"status": "passed", "checks": {}, "failures": []},
        }
        mart_id = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        manifest = {
            "artifact": "cirrus_orographic_shared_research_mart",
            "mart_id": mart_id, "generated_at_utc": "2026-08-21T00:00:00+00:00",
            **identity,
        }
        (root / "mart_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_plan_requires_both_sources_and_commits_manifest_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mart = self._mart(Path(tmpdir), sources=("cirrus", "orographic"))
            plan = build_iceberg_publication_plan(mart_dir=mart)
            self.assertEqual(plan["status"], "ready")
            self.assertEqual(plan["commit_order"][-1], "mart_publications")
            self.assertEqual({table["name"] for table in plan["tables"]}, set(TABLE_CONTRACTS))

    def test_plan_refuses_partial_shared_mart(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mart = self._mart(Path(tmpdir), sources=("orographic",))
            with self.assertRaisesRegex(ValueError, "cirrus"):
                build_iceberg_publication_plan(mart_dir=mart)

    def test_plan_rejects_unsafe_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mart = self._mart(Path(tmpdir), sources=("cirrus", "orographic"))
            with self.assertRaisesRegex(ValueError, "Invalid namespace"):
                build_iceberg_publication_plan(mart_dir=mart, namespace="bad-name")

    def test_verify_requires_environment(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "Missing Iceberg publication configuration"):
                verify_iceberg_mart(manifest={})


if __name__ == "__main__":
    unittest.main()
