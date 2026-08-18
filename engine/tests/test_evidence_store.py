from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from engine.orographic.evidence_store import (
    build_canonical_evidence_bundle,
    merge_ledgers,
    validate_canonical_bundle,
)
from scripts.materialize_canonical_evidence_for_cirrus import materialize_cirrus_archive


class EvidenceStoreTests(unittest.TestCase):
    def test_merge_ledgers_preserves_label_and_successful_capture_state(self) -> None:
        label = {
            "label_contract": {"version": 2},
            "label_available_at_utc": "2026-08-14T20:08:27Z",
            "exit": {"quote": {"capture_delay_seconds": 507.0}},
        }
        first = {"entries": [{
            "run_generated_at_utc": "2026-08-11T17:09:33+00:00",
            "picks": [{
                "recommendation_id": "rec-1",
                "contract_symbol": "AAA1",
                "outcomes": {
                    "fixed_exit_marks": {"friday_close": {
                        "captured_at_utc": "2026-08-14T20:08:27Z",
                        "mark": 1.2,
                    }},
                    "executable_labels": {"friday_close": label},
                    "capture_attempts": {"friday_close": {
                        "status": "captured_valid",
                        "attempted_at_utc": "2026-08-14T20:08:27Z",
                    }},
                },
            }],
        }]}
        later_retry = {"entries": [{
            "run_generated_at_utc": "2026-08-11T17:09:33+00:00",
            "picks": [{
                "recommendation_id": "rec-1",
                "contract_symbol": "AAA1",
                "outcomes": {
                    "fixed_exit_marks": {"friday_close": None},
                    "executable_labels": {"friday_close": None},
                    "capture_attempts": {"friday_close": {
                        "status": "quote_missing_retryable",
                        "attempted_at_utc": "2026-08-14T20:24:43Z",
                    }},
                },
            }],
        }]}

        merged = merge_ledgers(
            [first, later_retry], artifact="canonical_prospective_pick_ledger"
        )

        outcomes = merged["entries"][0]["picks"][0]["outcomes"]
        self.assertEqual(outcomes["executable_labels"]["friday_close"], label)
        self.assertEqual(outcomes["fixed_exit_marks"]["friday_close"]["mark"], 1.2)
        self.assertEqual(
            outcomes["capture_attempts"]["friday_close"]["status"],
            "captured_valid",
        )

    def test_bundle_deduplicates_restored_and_current_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            restored = root / "restored"
            current = root / "current"
            output = root / "canonical"
            restored.mkdir()
            current.mkdir()

            ledger = {"entries": [{
                "run_generated_at_utc": "2026-08-11T17:09:33+00:00",
                "picks": [{
                    "recommendation_id": "rec-1",
                    "contract_symbol": "AAA1",
                    "outcomes": {
                        "quote_verification": {"capture_policy_version": 2},
                        "fixed_exit_marks": {"friday_close": {"mark": 1.2}},
                        "executable_labels": {"friday_close": {"label_contract": {"version": 2}}},
                    },
                }],
            }]}
            (restored / "prospective_pick_ledger.json").write_text(
                json.dumps(ledger), encoding="utf-8"
            )
            current_ledger = current / "prospective_pick_ledger.json"
            current_ledger.write_text(json.dumps(ledger), encoding="utf-8")
            moonshot_ledger = current / "moonshot_prospective_ledger.json"
            moonshot_ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")

            outcome = pd.DataFrame([{
                "recommendation_id": "rec-1",
                "fixed_exit_window": "friday_close",
                "contract_symbol": "AAA1",
                "run_generated_at_utc": "2026-08-11T17:09:33+00:00",
                "pnl": 12.0,
            }])
            outcome.to_parquet(restored / "recommendation_outcomes.parquet", index=False)
            outcome.assign(pnl=13.0).to_parquet(
                current / "recommendation_outcomes.parquet", index=False
            )
            quote = pd.DataFrame([{
                "contract_symbol": "AAA1",
                "chain_snapshot_at_utc": "2026-08-11T17:09:33+00:00",
                "bid": 1.0,
                "ask": 1.2,
                "quote_date": "2026-08-11",
                "underlying_symbol": "AAA",
            }])
            quote.to_parquet(restored / "live_option_quotes.parquet", index=False)
            chain_dir = current / "partitioned" / "quote_date=2026-08-11"
            chain_dir.mkdir(parents=True)
            quote.assign(bid=1.1).to_parquet(chain_dir / "chain.parquet", index=False)

            manifest = build_canonical_evidence_bundle(
                source_roots=[restored, current],
                current_prospective_ledger=current_ledger,
                current_moonshot_ledger=moonshot_ledger,
                payoff_evidence=None,
                strict_outcome_artifacts=[],
                output_dir=output,
            )
            repeated_manifest = build_canonical_evidence_bundle(
                source_roots=[restored, current],
                current_prospective_ledger=current_ledger,
                current_moonshot_ledger=moonshot_ledger,
                payoff_evidence=None,
                strict_outcome_artifacts=[],
                output_dir=root / "canonical_repeated",
            )

            canonical_outcomes = pd.read_parquet(output / "recommendation_outcomes.parquet")
            canonical_quotes = pd.read_parquet(output / "live_option_quotes.parquet")
            self.assertEqual(len(canonical_outcomes), 1)
            self.assertEqual(canonical_outcomes.iloc[0]["pnl"], 13.0)
            self.assertEqual(len(canonical_quotes), 1)
            self.assertEqual(canonical_quotes.iloc[0]["bid"], 1.1)
            self.assertEqual(
                manifest["evidence"]["cumulative_inventory"]["primary"]["recommendations"],
                1,
            )
            self.assertEqual(
                manifest["evidence"]["training_eligible"][
                    "strict_capture_policy_v2_primary_recommendations"
                ],
                1,
            )
            self.assertEqual(manifest["bundle_id"], repeated_manifest["bundle_id"])
            validate_canonical_bundle(output)

    def test_bundle_validation_rejects_tampered_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger = root / "prospective_pick_ledger.json"
            moonshot = root / "moonshot_prospective_ledger.json"
            ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            build_canonical_evidence_bundle(
                source_roots=[],
                current_prospective_ledger=ledger,
                current_moonshot_ledger=moonshot,
                payoff_evidence=None,
                output_dir=root / "canonical",
            )
            (root / "canonical" / "prospective_pick_ledger.json").write_text(
                "{}", encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "validation failed"):
                validate_canonical_bundle(root / "canonical")

    def test_bundle_validation_rejects_corrupt_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            source.mkdir()
            (source / "recommendation_outcomes.parquet").write_text(
                "not parquet", encoding="utf-8"
            )
            ledger = root / "ledger.json"
            moonshot = root / "moonshot.json"
            ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            output = root / "canonical"
            manifest = build_canonical_evidence_bundle(
                source_roots=[source],
                current_prospective_ledger=ledger,
                current_moonshot_ledger=moonshot,
                payoff_evidence=None,
                output_dir=output,
            )

            self.assertFalse(manifest["checks"]["inputs_readable"])
            with self.assertRaisesRegex(ValueError, "check:inputs_readable"):
                validate_canonical_bundle(output)

    def test_canonical_quotes_materialize_in_cirrus_partition_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            current = root / "current"
            current.mkdir()
            ledger = root / "ledger.json"
            moonshot = root / "moonshot.json"
            ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            pd.DataFrame([{
                "contract_symbol": "AAA260821C00100000",
                "chain_snapshot_at_utc": "2026-08-15T14:00:00Z",
                "quote_date": "2026-08-15",
                "underlying_symbol": "aaa",
                "expire_date": "2026-08-21",
                "strike": 100.0,
                "option_type": "C",
                "bid": 1.0,
                "ask": 1.2,
            }]).to_parquet(current / "chain.parquet", index=False)
            canonical = root / "canonical"
            build_canonical_evidence_bundle(
                source_roots=[current],
                current_prospective_ledger=ledger,
                current_moonshot_ledger=moonshot,
                payoff_evidence=None,
                output_dir=canonical,
            )

            result = materialize_cirrus_archive(
                canonical_dir=canonical,
                output_dir=root / "cirrus" / "partitioned",
            )

            partition = (
                root
                / "cirrus"
                / "partitioned"
                / "quote_date=2026-08-15"
                / "underlying_symbol=AAA"
                / "chain.parquet"
            )
            self.assertTrue(partition.exists())
            self.assertEqual(result["rows"], 1)
            self.assertEqual(result["expected_rows"], 1)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(len(pd.read_parquet(partition)), 1)

            stale = root / "cirrus" / "partitioned" / "quote_date=1999-01-01" / "underlying_symbol=OLD" / "chain.parquet"
            stale.parent.mkdir(parents=True)
            pd.DataFrame([{"contract_symbol": "OLD"}]).to_parquet(stale, index=False)
            materialize_cirrus_archive(
                canonical_dir=canonical,
                output_dir=root / "cirrus" / "partitioned",
            )
            self.assertFalse(stale.exists())


if __name__ == "__main__":
    unittest.main()
