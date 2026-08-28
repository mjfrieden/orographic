from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression

from engine.orographic.production_ranker import score_production_candidates
from engine.orographic.schemas import ContractCandidate, MarketRegime


def _candidate(option_type: str, *, spread_pct: float = 0.04) -> ContractCandidate:
    return ContractCandidate(
        symbol="AAPL" if option_type == "call" else "MSFT",
        contract_symbol=f"TEST-{option_type}",
        option_type=option_type,
        expiry="2026-09-04",
        strike=100.0,
        bid=1.0,
        ask=1.05,
        last=1.02,
        premium=1.05,
        contract_cost=105.0,
        spread_pct=spread_pct,
        open_interest=900,
        volume=300,
        implied_volatility=0.30,
        delta=0.40 if option_type == "call" else -0.40,
        moneyness=0.01,
        projected_move_pct=0.05,
        breakeven_move_pct=0.025,
        expected_return_pct=0.35,
        extrinsic_ratio=0.70,
        scout_score=0.7 if option_type == "call" else -0.7,
        forge_score=0.5,
        spread_cost=1.05,
        allocation_weight=1.0,
        iv_rank=0.45,
    )


class ProductionRankerTests(unittest.TestCase):
    def _artifact(self, path: Path) -> None:
        model = LogisticRegression().fit(
            np.array([[0.0], [0.0], [1.0], [1.0]]),
            np.array([0, 0, 1, 1]),
        )
        joblib.dump(
            {
                "artifact": "production_payoff_ranker",
                "mode": "production_rank_only",
                "feature_cols": ["option_type_is_call"],
                "base_model": model,
                "calibrator": 0.0,
            },
            path,
        )

    def test_single_model_controls_within_scan_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "production.pkl"
            self._artifact(model_path)
            candidates = [_candidate("put"), _candidate("call")]
            score_production_candidates(
                candidates,
                MarketRegime("neutral", 0.0, "SPY"),
                as_of=date(2026, 8, 27),
                model_path=model_path,
            )

        self.assertEqual(candidates[0].option_type, "call")
        self.assertTrue(all(row.ranker_mode == "production_v2" for row in candidates))
        self.assertTrue(all(row.path_model_mode == "retired" for row in candidates))
        self.assertTrue(all(row.payoff_shadow_rank is None for row in candidates))
        self.assertGreater(candidates[0].forge_score, candidates[1].forge_score)

    def test_missing_production_artifact_fails_closed(self) -> None:
        missing = Path(tempfile.gettempdir()) / "orographic-production-ranker-missing.pkl"
        if missing.exists():
            missing.unlink()
        with self.assertRaises(FileNotFoundError):
            score_production_candidates([_candidate("call")], model_path=missing)

    def test_observation_only_artifact_cannot_gain_production_authority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "observer.pkl"
            self._artifact(model_path)
            artifact = joblib.load(model_path)
            artifact["mode"] = "observation_only_never_used_for_routing"
            joblib.dump(artifact, model_path)
            with self.assertRaises(ValueError):
                score_production_candidates([_candidate("call")], model_path=model_path)


if __name__ == "__main__":
    unittest.main()
