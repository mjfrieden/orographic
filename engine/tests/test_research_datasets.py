from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_research_datasets import ledger_rows


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
                            "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1, "contract_cost": 110.0},
                            "scores": {"forge_score": 0.8, "path_decay_risk": 0.2},
                            "risk_features": {"delta": 0.35, "iv_rank": 0.4},
                            "context": {"ranker_artifact_sha256": "abc"},
                            "outcomes": {
                                "status": "partial",
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
        self.assertEqual(rows[0]["forge_score"], 0.8)
        self.assertEqual(rows[0]["one_hour_pnl_pct_from_emission"], 0.2727)
        self.assertEqual(rows[0]["regime_mode"], "neutral")


if __name__ == "__main__":
    unittest.main()
