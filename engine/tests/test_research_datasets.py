from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_research_datasets import (
    canonical_option_outcome_rows,
    diagnostic_spot_lookups,
    ledger_rows,
    ledger_rows_with_spots,
)


class ResearchDatasetTests(unittest.TestCase):
    def test_ledger_rows_flatten_scores_risk_and_outcomes(self) -> None:
        ledger = {
            "artifact": "prospective_pick_ledger",
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-22T14:07:00+00:00",
                    "regime": {"mode": "neutral"},
                    "model_modes": {"payoff_ranker": "active"},
                    "picks": [
                        {
                            "lane": "live",
                            "symbol": "AAA",
                            "contract_symbol": "AAA1",
                            "option_type": "call",
                            "expiry": "2026-06-05",
                            "strike": 100.0,
                            "underlying": {
                                "symbol": "AAA",
                                "spot": 101.25,
                                "quote_captured_at_utc": "2026-05-22T14:07:00+00:00",
                            },
                            "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1, "contract_cost": 110.0},
                            "scores": {"forge_score": 0.8, "path_decay_risk": 0.2},
                            "risk_features": {"delta": 0.35, "iv_rank": 0.4},
                            "context": {"ranker_artifact_sha256": "abc"},
                            "outcomes": {
                                "status": "partial",
                                "archived_quote_path": {
                                    "status": "observed",
                                    "observation_count": 2,
                                    "entry_mark": 1.1,
                                    "max_favorable_excursion_pct": 0.2727,
                                    "max_adverse_excursion_pct": -0.1,
                                    "first_hit": {
                                        "rule": "take_profit_25_pct",
                                        "captured_at_utc": "2026-05-22T15:07:00+00:00",
                                        "pnl_pct_from_emission": 0.2727,
                                    },
                                    "take_profit_25_pct_before_stop_50_pct": True,
                                    "take_profit_40_pct_before_stop_50_pct": None,
                                },
                                "fixed_exit_marks": {
                                    "one_hour": {
                                        "mark": 1.4,
                                        "pnl_pct_from_emission": 0.2727,
                                        "captured_at_utc": "2026-05-22T15:07:00+00:00",
                                    }
                                },
                                "path_rules": {"max_favorable_excursion_pct": 0.2727},
                            },
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ledger.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            rows = ledger_rows(path, source_artifact="prospective_pick_ledger")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contract_symbol"], "AAA1")
        self.assertEqual(rows[0]["underlying_spot"], 101.25)
        self.assertEqual(rows[0]["underlying_quote_captured_at_utc"], "2026-05-22T14:07:00+00:00")
        self.assertEqual(rows[0]["forge_score"], 0.8)
        self.assertEqual(rows[0]["one_hour_pnl_pct_from_emission"], 0.2727)
        self.assertEqual(rows[0]["archive_path_first_hit_rule"], "take_profit_25_pct")
        self.assertEqual(rows[0]["archive_path_mfe_pct"], 0.2727)
        self.assertEqual(rows[0]["regime_mode"], "neutral")

    def test_ledger_rows_backfill_legacy_missing_underlying_spot(self) -> None:
        ledger = {
            "artifact": "prospective_pick_ledger",
            "entries": [
                {
                    "run_generated_at_utc": "2026-06-02T18:10:10+00:00",
                    "picks": [
                        {
                            "symbol": "NKE",
                            "contract_symbol": "NKE260612P00044000",
                            "emission_quote": {"ask": 1.26},
                            "risk_features": {"premium_pct_of_spot": 0.0288},
                        },
                        {
                            "symbol": "AAPL",
                            "contract_symbol": "AAPL260610C00320000",
                            "run_generated_at_utc": "2026-06-02T22:27:08+00:00",
                            "emission_quote": {"ask": 3.35},
                            "risk_features": {"premium_pct_of_spot": 0.0106},
                        },
                    ],
                }
            ],
        }
        waterfall = {
            "artifact": "forge_rejection_waterfall",
            "generated_at_utc": "2026-06-02T22:27:08+00:00",
            "forge": {"per_symbol": [{"symbol": "AAPL", "spot": 315.2}]},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger_path = root / "ledger.json"
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
            diagnostics_dir = root / "diagnostics"
            diagnostics_dir.mkdir()
            (diagnostics_dir / "forge_rejection_waterfall_2026-06-02.json").write_text(
                json.dumps(waterfall),
                encoding="utf-8",
            )
            spot_by_run, spot_by_date = diagnostic_spot_lookups(diagnostics_dir)
            rows = ledger_rows_with_spots(
                ledger_path,
                source_artifact="prospective_pick_ledger",
                spot_by_run=spot_by_run,
                spot_by_date=spot_by_date,
            )

        self.assertEqual(rows[0]["underlying_spot"], 43.75)
        self.assertEqual(rows[1]["underlying_spot"], 315.2)

    def test_canonical_option_outcome_rows_build_trainer_ready_labels(self) -> None:
        ledger = {
            "artifact": "prospective_pick_ledger",
            "entries": [
                {
                    "run_generated_at_utc": "2026-05-22T14:07:00+00:00",
                    "regime": {"mode": "risk_on", "bias": 0.25, "source_symbol": "SPY"},
                    "picks": [
                        {
                            "lane": "live",
                            "symbol": "AAA",
                            "contract_symbol": "AAA260605C00100000",
                            "option_type": "call",
                            "expiry": "2026-06-05",
                            "strike": 100.0,
                            "underlying": {"symbol": "AAA", "spot": 101.25},
                            "emission_quote": {
                                "bid": 1.0,
                                "ask": 1.2,
                                "mid": 1.1,
                                "spread_pct": 0.08,
                                "open_interest": 500,
                                "volume": 120,
                                "entry_quote_type": "ask",
                                "entry_data_source": "real_chain",
                            },
                            "scores": {
                                "forge_score": 0.8,
                                "payoff_model_score": 0.82,
                                "final_candidate_score": 0.81,
                                "expected_option_return_pct_model": 0.18,
                                "prob_positive_option_pnl": 0.71,
                                "path_early_profit_take_prob": 0.35,
                                "path_decay_risk": 0.2,
                                "path_holding_quality_score": 0.66,
                            },
                            "risk_features": {
                                "delta": 0.35,
                                "iv_rank": 0.4,
                                "implied_volatility": 0.32,
                                "moneyness": 0.01,
                                "projected_move_pct": 0.04,
                                "breakeven_move_pct": 0.03,
                                "extrinsic_ratio": 0.8,
                                "premium_pct_of_spot": 0.011,
                                "realized_vol_20d": 0.21,
                                "atr_pct_14d": 0.024,
                            },
                            "context": {"model_modes": {"path_model": "shadow"}},
                            "outcomes": {
                                "status": "complete",
                                "quote_verification": {"outcome_quotes_captured": True},
                                "fixed_exit_marks": {
                                    "friday_close": {
                                        "mark": 1.4,
                                        "mark_source": "mid",
                                        "captured_at_utc": "2026-05-29T20:00:00+00:00",
                                        "pnl_pct_from_emission": 0.2727,
                                    }
                                },
                                "archived_quote_path": {
                                    "status": "observed",
                                    "marks": [
                                        {"pnl_pct_from_emission": 0.05},
                                        {"pnl_pct_from_emission": 0.27},
                                    ],
                                    "max_favorable_excursion_pct": 0.27,
                                    "max_adverse_excursion_pct": -0.1,
                                },
                            },
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ledger.json"
            path.write_text(json.dumps(ledger), encoding="utf-8")
            rows = canonical_option_outcome_rows(
                path,
                source_artifact="prospective_pick_ledger",
                exit_window="friday_close",
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_date"], "2026-05-22")
        self.assertEqual(rows[0]["exit_date"], "2026-05-29")
        self.assertAlmostEqual(rows[0]["entry_price"], 1.1, places=4)
        self.assertAlmostEqual(rows[0]["exit_price"], 1.4, places=4)
        self.assertEqual(rows[0]["positive_pnl_after_friction"], True)
        self.assertAlmostEqual(rows[0]["max_favorable_excursion_before_expiry"], 0.27, places=4)
        self.assertEqual(rows[0]["path_model_mode"], "shadow")


if __name__ == "__main__":
    unittest.main()
