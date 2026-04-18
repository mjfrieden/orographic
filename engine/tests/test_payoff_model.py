from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier, DummyRegressor

from engine.orographic.payoff_model import (
    FEATURE_COLS,
    feature_matrix,
    score_candidates,
    side_aligned_directional_edge,
)
from engine.orographic.schemas import ContractCandidate, MarketRegime


def _candidate(option_type: str = "call", scout_score: float = 0.4, forge_score: float = 0.55) -> ContractCandidate:
    return ContractCandidate(
        symbol="AAPL",
        contract_symbol="AAPL260424C00195000",
        option_type=option_type,
        expiry="2026-04-24",
        strike=195.0,
        bid=1.0,
        ask=1.1,
        last=1.05,
        premium=1.1,
        contract_cost=110.0,
        spread_pct=0.095,
        open_interest=500,
        volume=120,
        implied_volatility=0.32,
        delta=0.42 if option_type == "call" else -0.42,
        moneyness=0.01,
        projected_move_pct=0.035,
        breakeven_move_pct=0.025,
        expected_return_pct=0.30,
        extrinsic_ratio=0.90,
        scout_score=scout_score,
        forge_score=forge_score,
        spread_cost=1.1,
        allocation_weight=1.0,
        iv_rank=0.45,
    )


class PayoffModelTests(unittest.TestCase):
    def test_directional_edge_is_side_aligned(self) -> None:
        self.assertAlmostEqual(side_aligned_directional_edge(_candidate("call", 0.6)), 0.8)
        self.assertAlmostEqual(side_aligned_directional_edge(_candidate("put", -0.6)), 0.8)
        self.assertAlmostEqual(side_aligned_directional_edge(_candidate("put", 0.6)), 0.2)

    def test_missing_artifact_preserves_forge_score(self) -> None:
        candidate = _candidate(forge_score=0.61)
        missing = Path(tempfile.gettempdir()) / "orographic_missing_payoff_model.pkl"
        if missing.exists():
            missing.unlink()
        score_candidates([candidate], MarketRegime("neutral", 0.0, "SPY"), as_of=date(2026, 4, 18), model_path=missing)
        self.assertEqual(candidate.forge_score, 0.61)
        self.assertEqual(candidate.pre_payoff_forge_score, 0.61)
        self.assertIsNotNone(candidate.prob_positive_option_pnl)

    def test_artifact_replaces_score_with_blend(self) -> None:
        candidates = [_candidate("call", 0.4, 0.51), _candidate("put", -0.5, 0.49)]
        X = feature_matrix(candidates, MarketRegime("neutral", 0.0, "SPY"), as_of=date(2026, 4, 18), feature_cols=FEATURE_COLS)
        positive = DummyClassifier(strategy="constant", constant=1).fit(X, np.ones(len(candidates), dtype=int))
        breakeven = DummyClassifier(strategy="constant", constant=1).fit(X, np.ones(len(candidates), dtype=int))
        expected = DummyRegressor(strategy="constant", constant=0.25).fit(X, np.ones(len(candidates)))
        mfe = DummyRegressor(strategy="constant", constant=0.50).fit(X, np.ones(len(candidates)))
        adverse = DummyRegressor(strategy="constant", constant=-0.20).fit(X, np.ones(len(candidates)))
        bundle = {
            "positive_classifier": positive,
            "breakeven_classifier": breakeven,
            "expected_return_regressor": expected,
            "mfe_regressor": mfe,
            "adverse_regressor": adverse,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "payoff_model.pkl"
            joblib.dump(
                {
                    "feature_cols": FEATURE_COLS,
                    "global": bundle,
                    "by_side": {},
                    "metadata": {"label_means": {}},
                },
                model_path,
            )
            score_candidates(candidates, MarketRegime("risk_on", 0.4, "SPY"), as_of=date(2026, 4, 18), model_path=model_path)

        self.assertNotEqual(candidates[0].forge_score, 0.51)
        self.assertEqual(candidates[0].prob_positive_option_pnl, 1.0)
        self.assertEqual(candidates[0].expected_option_return_pct_model, 0.25)
        self.assertTrue(any("Payoff model" in note for note in candidates[0].notes))


if __name__ == "__main__":
    unittest.main()
