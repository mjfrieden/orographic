from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.audit_research_data_capture import build_audit_report


class ResearchDataAuditTests(unittest.TestCase):
    def test_build_audit_report_passes_when_capture_artifacts_are_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "coverage_manifest.json"
            manifest.write_text(
                json.dumps({"summary": {"rows_archived": 12, "symbols_archived": 2}}),
                encoding="utf-8",
            )
            prospective = root / "prospective.json"
            prospective.write_text(
                json.dumps({"entries": [{"picks": [{"contract_symbol": "AAA1"}]}]}),
                encoding="utf-8",
            )
            moonshot = root / "moonshot.json"
            moonshot.write_text(
                json.dumps({"entries": [{"picks": [{"contract_symbol": "AAA2"}]}]}),
                encoding="utf-8",
            )
            recommendation_dataset = root / "option_recommendation_outcomes.parquet"
            moonshot_dataset = root / "moonshot_outcomes.parquet"
            combined_dataset = root / "all_recommendation_outcomes.parquet"
            pd.DataFrame([{"contract_symbol": "AAA1"}]).to_parquet(recommendation_dataset, index=False)
            pd.DataFrame([{"contract_symbol": "AAA2"}]).to_parquet(moonshot_dataset, index=False)
            pd.DataFrame([{"contract_symbol": "AAA1"}, {"contract_symbol": "AAA2"}]).to_parquet(combined_dataset, index=False)

            report = build_audit_report(
                live_archive_manifest=manifest,
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                recommendation_dataset=recommendation_dataset,
                moonshot_dataset=moonshot_dataset,
                combined_dataset=combined_dataset,
                min_archive_rows=1,
                min_recommendation_rows=1,
            )

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["summary"]["combined_dataset_rows"], 2)

    def test_build_audit_report_fails_empty_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "coverage_manifest.json"
            manifest.write_text(
                json.dumps({"summary": {"rows_archived": 0, "symbols_archived": 0}}),
                encoding="utf-8",
            )
            empty_ledger = root / "ledger.json"
            empty_ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")
            empty_dataset = root / "empty.parquet"
            pd.DataFrame([]).to_parquet(empty_dataset, index=False)

            report = build_audit_report(
                live_archive_manifest=manifest,
                prospective_ledger=empty_ledger,
                moonshot_ledger=empty_ledger,
                recommendation_dataset=empty_dataset,
                moonshot_dataset=empty_dataset,
                combined_dataset=empty_dataset,
                min_archive_rows=1,
            )

        self.assertEqual(report["status"], "failed")
        self.assertIn("live_archive_rows", {row["name"] for row in report["failed_checks"]})

    def test_build_audit_report_fails_when_dataset_rows_drift_from_ledgers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            manifest = root / "coverage_manifest.json"
            manifest.write_text(
                json.dumps({"summary": {"rows_archived": 12, "symbols_archived": 2}}),
                encoding="utf-8",
            )
            prospective = root / "prospective.json"
            prospective.write_text(
                json.dumps({"entries": [{"picks": [{"contract_symbol": "AAA1"}, {"contract_symbol": "AAA2"}]}]}),
                encoding="utf-8",
            )
            moonshot = root / "moonshot.json"
            moonshot.write_text(
                json.dumps({"entries": [{"picks": [{"contract_symbol": "AAA3"}]}]}),
                encoding="utf-8",
            )
            recommendation_dataset = root / "option_recommendation_outcomes.parquet"
            moonshot_dataset = root / "moonshot_outcomes.parquet"
            combined_dataset = root / "all_recommendation_outcomes.parquet"
            pd.DataFrame([{"contract_symbol": "AAA1"}]).to_parquet(recommendation_dataset, index=False)
            pd.DataFrame([{"contract_symbol": "AAA3"}]).to_parquet(moonshot_dataset, index=False)
            pd.DataFrame([{"contract_symbol": "AAA1"}, {"contract_symbol": "AAA3"}]).to_parquet(combined_dataset, index=False)

            report = build_audit_report(
                live_archive_manifest=manifest,
                prospective_ledger=prospective,
                moonshot_ledger=moonshot,
                recommendation_dataset=recommendation_dataset,
                moonshot_dataset=moonshot_dataset,
                combined_dataset=combined_dataset,
            )

        failed_names = {row["name"] for row in report["failed_checks"]}
        self.assertEqual(report["status"], "failed")
        self.assertIn("recommendation_dataset_matches_ledger", failed_names)
        self.assertIn("combined_dataset_matches_ledgers", failed_names)


if __name__ == "__main__":
    unittest.main()
