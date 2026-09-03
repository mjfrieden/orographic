from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from engine.orographic.shared_mart_consumers import (
    PRODUCTION_AUTHORITY,
    build_shared_mart_consumer_bundle,
    validate_shared_mart_consumer_bundle,
)
from engine.orographic.shared_mart_shadow import build_shared_mart_shadow_evidence
from engine.orographic.shared_research_mart import MART_SCHEMA_VERSION, TABLE_CONTRACTS


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_mart(root: Path) -> None:
    decision = "2026-08-21T19:00:00+00:00"
    rows = {
        "model_runs": [
            {
                "run_key": "orographic:run-1", "source_system": "orographic",
                "cohort": "primary_prospective", "source_run_id": "run-1",
                "decision_at_utc": decision, "available_at_utc": decision,
                "model_version": "oro-v1", "regime_mode": "risk_on", "regime_bias": 0.2,
                "source_bundle_id": "oro-bundle", "source_payload_json": "{}",
            },
            {
                "run_key": "cirrus:run-1", "source_system": "cirrus",
                "cohort": "cirrus_prospective", "source_run_id": "run-1",
                "decision_at_utc": decision, "available_at_utc": decision,
                "model_version": "cirrus-v1", "regime_mode": "risk_on", "regime_bias": 0.1,
                "source_bundle_id": "cirrus-bundle", "source_payload_json": "{}",
            },
        ],
        "recommendations": [
            {
                "recommendation_key": "orographic:rec-1", "run_key": "orographic:run-1",
                "source_system": "orographic", "cohort": "primary_prospective",
                "source_recommendation_id": "rec-1", "lane": "primary", "model_version": "oro-v1",
                "decision_at_utc": decision, "available_at_utc": decision,
                "underlying_symbol": "AAA", "contract_symbol": "AAA260828C00100000",
                "option_type": "call", "expiry_date": "2026-08-28", "strike": 100.0,
                "entry_bid": 1.0, "entry_ask": 1.2, "entry_mid": 1.1, "score": 0.8,
                "status": "settled", "source_bundle_id": "oro-bundle", "source_payload_json": "{}",
            },
            {
                "recommendation_key": "cirrus:rec-1", "run_key": "cirrus:run-1",
                "source_system": "cirrus", "cohort": "cirrus_prospective",
                "source_recommendation_id": "rec-1", "lane": "shadow", "model_version": "cirrus-v1",
                "decision_at_utc": decision, "available_at_utc": decision,
                "underlying_symbol": "AAA", "contract_symbol": "AAA260828C00105000",
                "option_type": "call", "expiry_date": "2026-08-28", "strike": 105.0,
                "entry_bid": 0.8, "entry_ask": 1.0, "entry_mid": 0.9, "score": 0.7,
                "status": "settled", "source_bundle_id": "cirrus-bundle", "source_payload_json": "{}",
            },
        ],
        "execution_outcomes": [
            {
                "outcome_key": "orographic:outcome-1", "recommendation_key": "orographic:rec-1",
                "source_system": "orographic", "cohort": "primary_prospective",
                "exit_policy": "friday_close", "entry_at_utc": decision,
                "exit_at_utc": "2026-08-22T19:00:00+00:00",
                "label_available_at_utc": "2026-08-22T19:00:01+00:00",
                "entry_price": 1.2, "exit_price": 1.5, "executable_return": 0.25,
                "observation_count": 2, "exit_reason": "friday_close",
                "label_contract_id": "orographic.v2", "label_contract_version": 2,
                "is_executable": True, "is_excluded": False, "source_bundle_id": "oro-bundle",
            },
            {
                "outcome_key": "cirrus:outcome-1", "recommendation_key": "cirrus:rec-1",
                "source_system": "cirrus", "cohort": "cirrus_prospective",
                "exit_policy": "strategy", "entry_at_utc": decision,
                "exit_at_utc": "2026-08-22T19:00:00+00:00",
                "label_available_at_utc": "2026-08-22T19:00:01+00:00",
                "entry_price": 1.0, "exit_price": 0.9, "executable_return": -0.1,
                "observation_count": 2, "exit_reason": "time_exit",
                "label_contract_id": "cirrus.v1", "label_contract_version": 1,
                "is_executable": True, "is_excluded": False, "source_bundle_id": "cirrus-bundle",
            },
        ],
        "option_quotes": [
            {
                "quote_key": "orographic:quote-1", "source_system": "orographic",
                "cohort": "primary_prospective", "recommendation_key": "orographic:rec-1",
                "contract_symbol": "AAA260828C00100000", "underlying_symbol": "AAA",
                "observed_at_utc": "2026-08-21T20:00:00+00:00",
                "available_at_utc": "2026-08-21T20:00:00+00:00", "quote_date": "2026-08-21",
                "quote_source": "live", "bid": 1.3, "ask": 1.4, "last_price": 1.35,
                "mid": 1.35, "executable_exit": 1.3, "open_interest": 500, "volume": 100,
                "implied_volatility": 0.4, "delta": 0.5, "gamma": 0.1,
                "theta_per_day": -0.05, "vega": 0.08, "source_bundle_id": "oro-bundle",
            },
            {
                "quote_key": "cirrus:quote-1", "source_system": "cirrus",
                "cohort": "cirrus_prospective", "recommendation_key": "cirrus:rec-1",
                "contract_symbol": "AAA260828C00105000", "underlying_symbol": "AAA",
                "observed_at_utc": "2026-08-21T20:00:00+00:00",
                "available_at_utc": "2026-08-21T20:00:00+00:00", "quote_date": "2026-08-21",
                "quote_source": "live", "bid": 0.9, "ask": 1.0, "last_price": 0.95,
                "mid": 0.95, "executable_exit": 0.9, "open_interest": 300, "volume": 80,
                "implied_volatility": 0.42, "delta": 0.4, "gamma": 0.1,
                "theta_per_day": -0.04, "vega": 0.07, "source_bundle_id": "cirrus-bundle",
            },
        ],
        "feature_snapshots": [
            {
                "feature_key": "orographic:feature-1", "recommendation_key": "orographic:rec-1",
                "source_system": "orographic", "feature_schema_version": "v1",
                "available_at_utc": decision, "features_sha256": "abc",
                "features_json": "{\"momentum\":0.2}", "source_metadata_json": "{}",
                "source_bundle_id": "oro-bundle",
            },
            {
                "feature_key": "cirrus:feature-1", "recommendation_key": "cirrus:rec-1",
                "source_system": "cirrus", "feature_schema_version": "v1",
                "available_at_utc": decision, "features_sha256": "def",
                "features_json": "{\"momentum\":0.1}", "source_metadata_json": "{}",
                "source_bundle_id": "cirrus-bundle",
            },
        ],
        "path_exclusions": [],
    }
    artifacts = {}
    for name, contract in TABLE_CONTRACTS.items():
        frame = pd.DataFrame(rows[name], columns=contract.columns)
        path = root / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        artifacts[name] = {
            "path": path.name, "rows": len(frame), "sha256": _sha(path),
            "primary_key": list(contract.primary_key), "columns": list(contract.columns),
        }
    identity = {
        "schema_version": MART_SCHEMA_VERSION,
        "sources": [
            {"source_system": "cirrus", "bundle_id": "cirrus-bundle"},
            {"source_system": "orographic", "bundle_id": "oro-bundle"},
        ],
        "artifacts": artifacts,
        "validation": {"status": "passed", "checks": {}, "failures": []},
    }
    mart_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (root / "mart_manifest.json").write_text(json.dumps({
        "artifact": "cirrus_orographic_shared_research_mart",
        "mart_id": mart_id,
        "generated_at_utc": decision,
        **identity,
    }), encoding="utf-8")


class SharedMartConsumerTests(unittest.TestCase):
    def test_builds_pinned_observation_only_views(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mart = root / "mart"
            mart.mkdir()
            _write_mart(mart)
            output = root / "consumers"

            manifest = build_shared_mart_consumer_bundle(mart, output)

            self.assertEqual(manifest["status"], "ready")
            self.assertEqual(manifest["production_authority"], PRODUCTION_AUTHORITY)
            self.assertEqual(manifest["views"]["orographic_training_v1"]["rows"], 1)
            self.assertEqual(manifest["views"]["orographic_execution_quality_v1"]["rows"], 2)
            self.assertEqual(manifest["views"]["orographic_exit_replay_v1"]["rows"], 2)
            self.assertEqual(manifest["views"]["cirrus_orographic_disagreement_v1"]["rows"], 1)
            self.assertEqual(manifest["views"]["mart_data_quality_v1"]["rows"], 2)
            disagreement = pd.read_parquet(output / "cirrus_orographic_disagreement_v1.parquet")
            self.assertEqual(disagreement.iloc[0]["comparison_cohort"], "same_side_different_contract")

            quality = pd.read_parquet(output / "mart_data_quality_v1.parquet").set_index("source_system")
            oro_quality = quality.loc["orographic"]
            self.assertEqual(oro_quality["recommendations"], 1)
            self.assertEqual(oro_quality["feature_coverage_rate"], 1.0)
            self.assertEqual(oro_quality["path_quote_coverage_rate"], 1.0)
            self.assertEqual(oro_quality["executable_outcome_coverage_rate"], 1.0)
            self.assertEqual(oro_quality["critical_null_rate"], 0.0)
            self.assertEqual(oro_quality["integrity_anomaly_rate"], 0.0)

            validate_shared_mart_consumer_bundle(output)

            shadow = build_shared_mart_shadow_evidence(output)
            self.assertEqual(shadow["status"], "collecting_shadow_evidence")
            self.assertFalse(shadow["production_changes_allowed"])
            self.assertEqual(shadow["cross_system_comparison"]["paired_executable_outcomes"], 1)
            self.assertEqual(shadow["cross_system_comparison"]["paired_market_dates"], 1)
            self.assertEqual(shadow["training_evidence"]["market_dates"], 1)
            self.assertEqual(shadow["consumer_bundle"]["source_systems"], ["cirrus", "orographic"])
            self.assertEqual(
                shadow["consumer_bundle"]["views"]["orographic_training_v1"]["rows"], 1
            )
            self.assertEqual(shadow["data_quality"]["cohorts"], 2)
            self.assertTrue(shadow["data_quality"]["integrity_clean"])
            self.assertEqual(shadow["data_quality"]["worst_critical_null_rate"], 0.0)
            self.assertEqual(shadow["data_quality"]["worst_integrity_anomaly_rate"], 0.0)

    def test_data_quality_view_flags_anomalies(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mart = root / "mart"
            mart.mkdir()
            _write_mart(mart)

            # Corrupt one Orographic recommendation (crossed entry quote, null score)
            # and one path quote (crossed) to exercise the data-quality detectors.
            recs = pd.read_parquet(mart / "recommendations.parquet")
            recs.loc[recs["recommendation_key"] == "orographic:rec-1", "entry_bid"] = 1.5
            recs.loc[recs["recommendation_key"] == "orographic:rec-1", "entry_ask"] = 1.0
            recs.loc[recs["recommendation_key"] == "orographic:rec-1", "score"] = None
            recs.to_parquet(mart / "recommendations.parquet", index=False)

            quotes = pd.read_parquet(mart / "option_quotes.parquet")
            quotes.loc[quotes["quote_key"] == "orographic:quote-1", "bid"] = 2.0
            quotes.loc[quotes["quote_key"] == "orographic:quote-1", "ask"] = 1.0
            quotes.to_parquet(mart / "option_quotes.parquet", index=False)

            # Re-pin manifest hashes/rows after mutation.
            manifest = json.loads((mart / "mart_manifest.json").read_text(encoding="utf-8"))
            for name in ("recommendations", "option_quotes"):
                path = mart / f"{name}.parquet"
                manifest["artifacts"][name]["sha256"] = _sha(path)
                manifest["artifacts"][name]["rows"] = len(pd.read_parquet(path))
            identity = {
                "schema_version": manifest["schema_version"],
                "sources": manifest["sources"],
                "artifacts": manifest["artifacts"],
                "validation": manifest["validation"],
            }
            manifest["mart_id"] = hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            (mart / "mart_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            output = root / "consumers"
            build_shared_mart_consumer_bundle(mart, output)

            quality = pd.read_parquet(output / "mart_data_quality_v1.parquet").set_index("source_system")
            oro_quality = quality.loc["orographic"]
            self.assertEqual(oro_quality["crossed_entry_rate"], 1.0)
            self.assertEqual(oro_quality["score_null_rate"], 1.0)
            self.assertEqual(oro_quality["path_crossed_quote_rate"], 1.0)
            self.assertEqual(oro_quality["integrity_anomaly_rate"], 1.0)
            self.assertEqual(oro_quality["critical_null_rate"], 1.0)

            shadow = build_shared_mart_shadow_evidence(output)
            self.assertFalse(shadow["data_quality"]["integrity_clean"])
            self.assertEqual(shadow["data_quality"]["worst_integrity_anomaly_rate"], 1.0)
            self.assertEqual(shadow["data_quality"]["cohorts_with_integrity_anomalies"], 1)
            self.assertEqual(shadow["data_quality"]["cohorts_with_null_gaps"], 1)


if __name__ == "__main__":
    unittest.main()
