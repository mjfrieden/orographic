from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier, DummyRegressor

from engine.orographic.path_model import FEATURE_COLS, feature_matrix, score_candidates, summarize_candidates
from engine.orographic.schemas import ContractCandidate, MarketRegime


def _candidate(symbol: str = "AAPL", option_type: str = "call") -> ContractCandidate:
    return ContractCandidate(
        symbol=symbol,
        contract_symbol=f"{symbol}260424{'C' if option_type == 'call' else 'P'}00195000",
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
        scout_score=0.4 if option_type == "call" else -0.4,
        forge_score=0.55,
        allocation_weight=1.0,
        iv_rank=0.45,
        realized_vol_20d=0.22,
        atr_pct_14d=0.025,
        premium_pct_of_spot=0.011,
        vrp_gap=0.10,
        expected_edge_after_friction_pct=0.14,
        regime_alignment_score=0.8,
        sentinel_holding_window_fit=0.8,
        sentinel_confidence=0.7,
        sentinel_call_relevance=0.9,
        sentinel_put_relevance=0.1,
        sentinel_no_trade_relevance=0.05,
        sentinel_spot_effect=1.0,
        sentinel_iv_effect=0.0,
    )


class PathModelTests(unittest.TestCase):
    def test_heuristic_shadow_scores_candidates(self) -> None:
        candidates = [_candidate("AAPL", "call"), _candidate("NVDA", "put")]

        score_candidates(candidates, MarketRegime("risk_on", 0.3, "SPY"), as_of=date(2026, 4, 18))
        summary = summarize_candidates(candidates)

        self.assertEqual(candidates[0].path_model_mode, "shadow")
        self.assertIsNotNone(candidates[0].path_early_profit_take_prob)
        self.assertIsNotNone(candidates[0].path_expected_mfe_pct)
        self.assertIsNotNone(candidates[0].path_decay_risk)
        self.assertIsNotNone(candidates[0].path_holding_quality_score)
        self.assertGreater(summary["avg_holding_quality_score"] or 0.0, 0.0)
        self.assertTrue(any("Path model shadow" in note for note in candidates[0].notes))

    def test_artifact_can_drive_shadow_scores(self) -> None:
        candidates = [_candidate("AAPL", "call"), _candidate("TSLA", "put")]
        X = feature_matrix(candidates, MarketRegime("neutral", 0.0, "SPY"), as_of=date(2026, 4, 18), feature_cols=FEATURE_COLS)
        early = DummyClassifier(strategy="constant", constant=1).fit(X, np.ones(len(candidates), dtype=int))
        mfe = DummyRegressor(strategy="constant", constant=0.25).fit(X, np.ones(len(candidates)))
        decay = DummyRegressor(strategy="constant", constant=0.35).fit(X, np.ones(len(candidates)))
        bundle = {
            "early_take_profit_classifier": early,
            "mfe_regressor": mfe,
            "decay_risk_regressor": decay,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "path_model.pkl"
            joblib.dump(
                {
                    "feature_cols": FEATURE_COLS,
                    "global": bundle,
                    "by_side": {},
                    "metadata": {"label_means": {}},
                },
                model_path,
            )
            score_candidates(
                candidates,
                MarketRegime("neutral", 0.0, "SPY"),
                as_of=date(2026, 4, 18),
                model_path=model_path,
            )

        self.assertEqual(candidates[0].path_model_mode, "shadow")
        self.assertEqual(candidates[0].path_early_profit_take_prob, 1.0)
        self.assertEqual(candidates[0].path_expected_mfe_pct, 0.25)
        self.assertEqual(candidates[0].path_decay_risk, 0.35)


if __name__ == "__main__":
    unittest.main()
