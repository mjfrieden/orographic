from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_research_datasets import diagnostic_spot_lookups, ledger_rows, ledger_rows_with_spots


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


if __name__ == "__main__":
    unittest.main()
