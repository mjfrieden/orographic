from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from engine.orographic.evidence_store import build_canonical_evidence_bundle
from engine.orographic.shared_research_mart import (
    build_shared_research_mart,
    validate_shared_research_mart,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_cirrus_export(root: Path) -> None:
    tables = {
        "scan_runs": pd.DataFrame([{
            "id": 1, "scan_date": "2026-08-21",
            "generated_at": "2026-08-21T19:00:00+00:00",
            "playbook": "auto", "world_mode": "risk_on", "world_risk_score": 0.2,
            "created_ts": "2026-08-21T19:00:01+00:00",
        }]),
        "tracked_picks": pd.DataFrame([{
            "id": 10, "scan_run_id": 1, "scan_date": "2026-08-21",
            "scan_generated_at": "2026-08-21T19:00:00+00:00", "lane": "shadow",
            "ticker": "AAA", "contract_symbol": "AAA260828C00100000",
            "direction": "bullish", "expiry": "2026-08-28", "strike": 100.0,
            "bid": 1.0, "ask": 1.2, "mid": 1.1, "score": 0.8, "status": "settled",
            "created_ts": "2026-08-21T19:00:01+00:00",
        }]),
        "candidate_feature_snapshots": pd.DataFrame([{
            "tracked_pick_id": 10, "feature_schema_version": "v1",
            "available_at": "2026-08-21T19:00:00+00:00", "features_json": "{}",
            "features_sha256": "abc", "source_metadata_json": "{}",
        }]),
        "option_quote_snapshots": pd.DataFrame([{
            "tracked_pick_id": 10, "observed_date": "2026-08-21",
            "observed_ts": "2026-08-21T20:00:00+00:00", "source": "live_chain_mark",
            "bid": 1.5, "ask": 1.6, "mid": 1.55, "executable_exit": 1.5,
        }]),
        "option_path_outcomes": pd.DataFrame([{
            "tracked_pick_id": 10, "observation_count": 2,
            "strategy_return": 0.25, "strategy_exit_price": 1.5,
            "strategy_exit_date": "2026-08-21", "strategy_exit_reason": "take_profit",
            "updated_ts": "2026-08-21T20:00:01+00:00",
        }]),
        "path_exclusions": pd.DataFrame(columns=[
            "tracked_pick_id", "reason_code", "details", "excluded_ts"
        ]),
        "position_legs": pd.DataFrame([{
            "tracked_pick_id": 10, "role": "long", "quantity": 1,
            "contract_symbol": "AAA260828C00100000", "strike": 100.0,
        }]),
    }
    artifacts = {}
    for name, frame in tables.items():
        path = root / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        artifacts[name] = {"rows": len(frame), "sha256": _sha256(path), "columns": list(frame.columns)}
    (root / "manifest.json").write_text(json.dumps({
        "bundle_id": "cirrus-test-bundle", "artifacts": artifacts,
    }), encoding="utf-8")


def _write_orographic_canonical(
    root: Path,
    *,
    rec_id: str,
    symbol: str,
    with_features: bool,
) -> Path:
    source = root / "orographic_source"
    source.mkdir(parents=True)
    decision = "2026-08-21T19:00:00+00:00"
    pick = {
        "recommendation_id": rec_id,
        "symbol": symbol,
        "contract_symbol": f"{symbol}260828C00100000",
        "option_type": "call",
        "expiry": "2026-08-28",
        "strike": 100.0,
        "lane": "primary",
        "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1, "captured_at_utc": decision},
        "outcomes": {"status": "settled"},
    }
    if with_features:
        pick.update({
            "days_to_expiry": 7,
            "scores": {"final_candidate_score": 0.82, "forge_score": 0.7},
            "risk_features": {"delta": 0.5, "implied_volatility": 0.4},
            "context": {"regime": {"mode": "risk_on", "bias": 0.3}, "ranker_mode": "production_v2"},
        })
    primary = root / "primary.json"
    moonshot = root / "moonshot.json"
    primary.write_text(json.dumps({"entries": [{
        "run_generated_at_utc": decision,
        "regime": {"mode": "risk_on", "bias": 0.3},
        "picks": [pick],
    }]}), encoding="utf-8")
    moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
    pd.DataFrame([{
        "recommendation_id": rec_id, "run_generated_at_utc": decision,
        "source_artifact": "prospective_pick_ledger", "fixed_exit_window": "friday_close",
        "symbol": symbol, "contract_symbol": f"{symbol}260828C00100000", "option_type": "call",
        "expiry": "2026-08-28", "strike": 100.0, "entry_price": 1.2, "exit_price": 1.5,
        "pnl_pct": 0.25, "entry_quote_observed_at_utc": decision,
        "exit_quote_observed_at_utc": "2026-08-22T19:00:00+00:00",
        "executable_label_available_at_utc": "2026-08-22T19:00:01+00:00",
        "executable_label_contract_id": "orographic.v2", "executable_label_contract_version": 2,
    }]).to_parquet(source / "recommendation_outcomes.parquet", index=False)
    pd.DataFrame([{
        "contract_symbol": f"{symbol}260828C00100000", "underlying_symbol": symbol,
        "chain_snapshot_at_utc": decision, "quote_date": "2026-08-21",
        "bid": 1.0, "ask": 1.2,
    }]).to_parquet(source / "live_option_quotes.parquet", index=False)
    canonical = root / "canonical"
    build_canonical_evidence_bundle(
        source_roots=[source], current_prospective_ledger=primary,
        current_moonshot_ledger=moonshot, payoff_evidence=None,
        strict_outcome_artifacts=[], output_dir=canonical,
    )
    return canonical


class SharedResearchMartTests(unittest.TestCase):
    def test_builds_conformed_mart_with_both_systems(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "orographic_source"
            source.mkdir()
            primary = root / "primary.json"
            moonshot = root / "moonshot.json"
            primary.write_text(json.dumps({"entries": [{
                "run_generated_at_utc": "2026-08-21T19:00:00+00:00",
                "regime": {"mode": "risk_on", "bias": 0.3},
                "picks": [{
                    "recommendation_id": "oro-1", "symbol": "BBB",
                    "contract_symbol": "BBB260828C00100000", "option_type": "call",
                    "expiry": "2026-08-28", "strike": 100.0, "lane": "shadow",
                    "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1,
                                       "captured_at_utc": "2026-08-21T19:00:00+00:00"},
                    "outcomes": {"status": "settled"},
                }],
            }]}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            pd.DataFrame([{
                "recommendation_id": "oro-1", "run_generated_at_utc": "2026-08-21T19:00:00+00:00",
                "source_artifact": "prospective_pick_ledger", "fixed_exit_window": "friday_close",
                "symbol": "BBB", "contract_symbol": "BBB260828C00100000", "option_type": "call",
                "expiry": "2026-08-28", "strike": 100.0, "entry_price": 1.2, "exit_price": 1.5,
                "pnl_pct": 0.25, "entry_quote_observed_at_utc": "2026-08-21T19:00:00+00:00",
                "exit_quote_observed_at_utc": "2026-08-22T19:00:00+00:00",
                "executable_label_available_at_utc": "2026-08-22T19:00:01+00:00",
                "executable_label_contract_id": "orographic.v2", "executable_label_contract_version": 2,
            }]).to_parquet(source / "recommendation_outcomes.parquet", index=False)
            pd.DataFrame([{
                "contract_symbol": "BBB260828C00100000", "underlying_symbol": "BBB",
                "chain_snapshot_at_utc": "2026-08-21T19:00:00+00:00",
                "quote_date": "2026-08-21", "bid": 1.0, "ask": 1.2,
            }]).to_parquet(source / "live_option_quotes.parquet", index=False)
            canonical = root / "canonical"
            build_canonical_evidence_bundle(
                source_roots=[source], current_prospective_ledger=primary,
                current_moonshot_ledger=moonshot, payoff_evidence=None,
                strict_outcome_artifacts=[], output_dir=canonical,
            )
            cirrus = root / "cirrus"
            cirrus.mkdir()
            _write_cirrus_export(cirrus)
            mart = root / "mart"

            manifest = build_shared_research_mart(
                orographic_canonical_dir=canonical,
                cirrus_export_dir=cirrus,
                output_dir=mart,
            )
            repeated = build_shared_research_mart(
                orographic_canonical_dir=canonical,
                cirrus_export_dir=cirrus,
                output_dir=root / "mart_repeated",
            )

            self.assertEqual(manifest["validation"]["status"], "passed")
            self.assertEqual(manifest["artifacts"]["model_runs"]["rows"], 2)
            self.assertEqual(manifest["artifacts"]["recommendations"]["rows"], 2)
            self.assertEqual(manifest["artifacts"]["execution_outcomes"]["rows"], 2)
            self.assertEqual(manifest["mart_id"], repeated["mart_id"])
            outcomes = pd.read_parquet(mart / "execution_outcomes.parquet")
            self.assertTrue(outcomes["is_executable"].all())
            self.assertEqual(set(outcomes["source_system"]), {"cirrus", "orographic"})
            validate_shared_research_mart(mart)

    def test_rebuilds_from_archived_cirrus_rows_with_current_orographic(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            frozen_canonical = _write_orographic_canonical(
                root / "frozen_src", rec_id="oro-old", symbol="BBB", with_features=False
            )
            cirrus = root / "cirrus"
            cirrus.mkdir()
            _write_cirrus_export(cirrus)
            frozen = root / "frozen_mart"
            frozen_manifest = build_shared_research_mart(
                orographic_canonical_dir=frozen_canonical,
                cirrus_export_dir=cirrus,
                output_dir=frozen,
            )
            current_canonical = _write_orographic_canonical(
                root / "current_src", rec_id="oro-new", symbol="CCC", with_features=True
            )
            refreshed = root / "refreshed_mart"
            manifest = build_shared_research_mart(
                orographic_canonical_dir=current_canonical,
                cirrus_mart_dir=frozen,
                output_dir=refreshed,
            )
            validate_shared_research_mart(refreshed)
            self.assertNotEqual(manifest["mart_id"], frozen_manifest["mart_id"])
            cirrus_source = next(
                row for row in manifest["sources"] if row["source_system"] == "cirrus"
            )
            self.assertEqual(cirrus_source["reused_from_mart_id"], frozen_manifest["mart_id"])
            recs = pd.read_parquet(refreshed / "recommendations.parquet")
            oro = recs[recs["source_system"] == "orographic"]
            cirrus_recs = recs[recs["source_system"] == "cirrus"]
            self.assertEqual(set(oro["source_recommendation_id"]), {"oro-new"})
            self.assertEqual(set(oro["underlying_symbol"]), {"CCC"})
            self.assertEqual(len(cirrus_recs), 1)
            self.assertEqual(cirrus_recs.iloc[0]["underlying_symbol"], "AAA")
            features = pd.read_parquet(refreshed / "feature_snapshots.parquet")
            oro_features = features[features["source_system"] == "orographic"]
            self.assertEqual(len(oro_features), 1)
            payload = json.loads(oro_features.iloc[0]["features_json"])
            self.assertIn("scores.final_candidate_score", payload)
            try:
                import duckdb  # noqa: F401
            except ImportError:
                return
            from engine.orographic.shared_mart_consumers import (
                build_shared_mart_consumer_bundle,
            )
            consumers = root / "consumers"
            consumer_manifest = build_shared_mart_consumer_bundle(refreshed, consumers)
            self.assertGreaterEqual(
                consumer_manifest["views"]["orographic_training_v1"]["rows"], 1
            )

    def test_orographic_recommendations_emit_point_in_time_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "orographic_source"
            source.mkdir()
            primary = root / "primary.json"
            moonshot = root / "moonshot.json"
            decision = "2026-08-21T19:00:00+00:00"
            primary.write_text(json.dumps({"entries": [{
                "run_generated_at_utc": decision,
                "regime": {"mode": "risk_on", "bias": 0.3},
                "picks": [{
                    "recommendation_id": "oro-1", "symbol": "BBB",
                    "contract_symbol": "BBB260828C00100000", "option_type": "call",
                    "expiry": "2026-08-28", "strike": 100.0, "lane": "primary",
                    "days_to_expiry": 7,
                    "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1,
                                       "spread_pct": 0.18, "open_interest": 500,
                                       "captured_at_utc": decision},
                    "scores": {"final_candidate_score": 0.82, "forge_score": 0.7,
                               "prob_positive_option_pnl": 0.55},
                    "risk_features": {"delta": 0.5, "implied_volatility": 0.4,
                                      "moneyness": 0.98, "extrinsic_ratio": 0.6},
                    "context": {"regime": {"mode": "risk_on", "bias": 0.3},
                                "ranker_mode": "production_v2"},
                    "outcomes": {"status": "settled",
                                 "fixed_exit_marks": {"friday_close": {"pnl_pct_from_emission": 0.25}}},
                }],
            }]}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            pd.DataFrame([{
                "recommendation_id": "oro-1", "run_generated_at_utc": decision,
                "source_artifact": "prospective_pick_ledger", "fixed_exit_window": "friday_close",
                "symbol": "BBB", "contract_symbol": "BBB260828C00100000", "option_type": "call",
                "expiry": "2026-08-28", "strike": 100.0, "entry_price": 1.2, "exit_price": 1.5,
                "pnl_pct": 0.25, "entry_quote_observed_at_utc": decision,
                "exit_quote_observed_at_utc": "2026-08-22T19:00:00+00:00",
                "executable_label_available_at_utc": "2026-08-22T19:00:01+00:00",
                "executable_label_contract_id": "orographic.v2", "executable_label_contract_version": 2,
            }]).to_parquet(source / "recommendation_outcomes.parquet", index=False)
            pd.DataFrame([{
                "contract_symbol": "BBB260828C00100000", "underlying_symbol": "BBB",
                "chain_snapshot_at_utc": decision, "quote_date": "2026-08-21",
                "bid": 1.0, "ask": 1.2,
            }]).to_parquet(source / "live_option_quotes.parquet", index=False)
            canonical = root / "canonical"
            build_canonical_evidence_bundle(
                source_roots=[source], current_prospective_ledger=primary,
                current_moonshot_ledger=moonshot, payoff_evidence=None,
                strict_outcome_artifacts=[], output_dir=canonical,
            )
            cirrus = root / "cirrus"
            cirrus.mkdir()
            _write_cirrus_export(cirrus)
            mart = root / "mart"
            manifest = build_shared_research_mart(
                orographic_canonical_dir=canonical,
                cirrus_export_dir=cirrus,
                output_dir=mart,
            )
            self.assertEqual(manifest["validation"]["status"], "passed")

            features = pd.read_parquet(mart / "feature_snapshots.parquet")
            oro_features = features[features["source_system"] == "orographic"]
            self.assertEqual(len(oro_features), 1)
            row = oro_features.iloc[0]
            self.assertEqual(row["feature_schema_version"], "orographic_pick_features_v1")
            self.assertEqual(row["recommendation_key"], "orographic|primary|oro-1")
            self.assertLessEqual(row["available_at_utc"], decision)
            payload = json.loads(row["features_json"])
            self.assertIn("scores.final_candidate_score", payload)
            self.assertIn("risk_features.delta", payload)
            self.assertIn("regime.mode", payload)
            self.assertEqual(payload["option_type"], "call")
            # Post-decision outcome fields must never leak into features. The
            # decision-time `prob_positive_option_pnl` score is allowed; realized
            # outcome fields such as `pnl_pct_from_emission` must be absent.
            self.assertFalse(any("outcomes" in key for key in payload))
            self.assertFalse(any("pnl_pct" in key for key in payload))
            self.assertFalse(any("from_emission" in key for key in payload))

            try:
                import duckdb  # noqa: F401
            except ImportError:
                self.skipTest("duckdb not installed")
            from engine.orographic.shared_mart_consumers import (
                build_shared_mart_consumer_bundle,
            )
            consumers = root / "consumers"
            consumer_manifest = build_shared_mart_consumer_bundle(mart, consumers)
            self.assertGreaterEqual(
                consumer_manifest["views"]["orographic_training_v1"]["rows"], 1
            )

    def test_validation_rejects_tampered_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "source"
            source.mkdir()
            primary = root / "primary.json"
            moonshot = root / "moonshot.json"
            primary.write_text(json.dumps({"entries": []}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            canonical = root / "canonical"
            build_canonical_evidence_bundle(
                source_roots=[source], current_prospective_ledger=primary,
                current_moonshot_ledger=moonshot, payoff_evidence=None,
                strict_outcome_artifacts=[], output_dir=canonical,
            )
            mart = root / "mart"
            build_shared_research_mart(orographic_canonical_dir=canonical, output_dir=mart)
            (mart / "model_runs.parquet").write_text("tampered", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash:model_runs"):
                validate_shared_research_mart(mart)


if __name__ == "__main__":
    unittest.main()
