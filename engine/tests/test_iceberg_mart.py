from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
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

    def test_inspect_skips_without_catalog_credentials(self) -> None:
        from engine.orographic.iceberg_mart import inspect_iceberg_source

        with mock.patch.dict(os.environ, {}, clear=True):
            info = inspect_iceberg_source(source_system="cirrus")
        self.assertEqual(info["status"], "skipped_missing_credentials")
        self.assertIn("catalog_uri", info["missing"])
        self.assertFalse(info.get("current_export"))

    def test_inspect_reads_cirrus_recency_without_marking_current(self) -> None:
        from engine.orographic.iceberg_mart import inspect_iceberg_source

        class FakeConnection:
            def execute(self, sql, params=None):
                self.last_sql = sql
                self.last_params = params
                if "mart_publications" in sql:
                    return FakeResult(("bfc84a0", "2026-08-24T03:25:26+00:00"))
                return FakeResult((89, "2026-07-01T00:00:00+00:00", "2026-08-21T00:00:00+00:00", 12))

            def close(self):
                return None

        class FakeResult:
            def __init__(self, row):
                self.row = row

            def fetchone(self):
                return self.row

        fake_duckdb = mock.Mock()
        fake_duckdb.connect.return_value = FakeConnection()
        with (
            mock.patch.dict(
                os.environ,
                {
                    "OROGRAPHIC_R2_DATA_CATALOG_URI": "https://catalog.example/mart",
                    "OROGRAPHIC_R2_DATA_CATALOG_WAREHOUSE": "warehouse",
                    "OROGRAPHIC_R2_DATA_CATALOG_TOKEN": "token",
                },
                clear=True,
            ),
            mock.patch.dict(sys.modules, {"duckdb": fake_duckdb}),
        ):
            info = inspect_iceberg_source(source_system="cirrus")
        self.assertEqual(info["status"], "inspected")
        self.assertEqual(info["recommendation_rows"], 89)
        self.assertEqual(info["max_decision_at_utc"], "2026-08-21T00:00:00+00:00")
        self.assertEqual(info["latest_publication_mart_id"], "bfc84a0")
        self.assertFalse(info["current_export"])


if __name__ == "__main__":
    unittest.main()
