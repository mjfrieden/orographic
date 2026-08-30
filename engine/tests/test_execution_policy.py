from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import tempfile
import unittest
from pathlib import Path

from engine.orographic.execution_policy import (
    LiveExecutionPolicy,
    apply_live_execution_policy,
    load_recent_live_exposures,
)
from engine.orographic.council import select_board
from engine.orographic.schemas import ContractCandidate, MarketRegime


def _candidate(**overrides: object) -> ContractCandidate:
    values: dict[str, object] = {
        "symbol": "XLP",
        "contract_symbol": "XLP260828C00084500",
        "option_type": "call",
        "expiry": "2026-08-28",
        "strike": 84.5,
        "bid": 1.10,
        "ask": 1.17,
        "last": 1.14,
        "premium": 1.17,
        "contract_cost": 117.0,
        "spread_pct": 0.0617,
        "open_interest": 800,
        "volume": 120,
        "implied_volatility": 0.22,
        "delta": 0.42,
        "moneyness": 0.01,
        "projected_move_pct": 0.04,
        "breakeven_move_pct": 0.025,
        "expected_return_pct": 0.30,
        "extrinsic_ratio": 0.8,
        "scout_score": 0.8,
        "forge_score": 0.9,
        "expected_edge_after_friction_pct": 0.14,
        "last_trade_age_seconds": 120.0,
    }
    values.update(overrides)
    return ContractCandidate(**values)  # type: ignore[arg-type]


class LiveExecutionPolicyTests(unittest.TestCase):
    def test_liquid_candidate_passes_and_records_conservative_exit(self) -> None:
        candidate = _candidate()
        result = apply_live_execution_policy([candidate])

        self.assertEqual(result["kept"], 1)
        self.assertTrue(candidate.execution_policy_passed)
        self.assertEqual(candidate.conservative_exit_bid, 1.10)
        self.assertEqual(candidate.execution_policy_reasons, [])

    def test_wide_market_and_weak_edge_are_vetoed_but_retained_for_research(self) -> None:
        candidate = _candidate(spread_pct=0.20, expected_edge_after_friction_pct=0.01)
        result = apply_live_execution_policy([candidate])

        self.assertEqual(result["dropped"], 1)
        self.assertFalse(candidate.execution_policy_passed)
        self.assertEqual(candidate.execution_policy_reasons, ["after_friction_edge", "entry_spread"])
        self.assertIn("execution_policy", candidate.council_risk_flags)

    def test_positive_tail_utility_can_replace_legacy_edge_gate(self) -> None:
        candidate = _candidate(
            expected_edge_after_friction_pct=0.01,
            expected_tail_utility=0.60,
            tail_gate_passed=True,
        )
        result = apply_live_execution_policy([candidate])

        self.assertEqual(result["kept"], 1)
        self.assertTrue(candidate.execution_policy_passed)
        self.assertNotIn("after_friction_edge", candidate.execution_policy_reasons)

    def test_same_contract_higher_premium_reentry_is_blocked(self) -> None:
        now = datetime(2026, 8, 18, 15, tzinfo=timezone.utc)
        candidate = _candidate(ask=1.88, contract_cost=188.0, expected_edge_after_friction_pct=0.16)
        prior = [{
            "contract_symbol": candidate.contract_symbol,
            "emitted_at_utc": now - timedelta(hours=24),
            "ask": 1.17,
            "expected_edge_after_friction_pct": 0.14,
        }]
        result = apply_live_execution_policy([candidate], prior_exposures=prior, as_of_utc=now)

        self.assertEqual(result["dropped"], 1)
        self.assertTrue(candidate.reentry_blocked)
        self.assertIn("same_contract_cooldown", candidate.execution_policy_reasons)
        self.assertEqual(candidate.prior_entry_ask, 1.17)

    def test_materially_stronger_non_chasing_reentry_can_override(self) -> None:
        now = datetime(2026, 8, 18, 15, tzinfo=timezone.utc)
        candidate = _candidate(ask=1.20, expected_edge_after_friction_pct=0.26)
        prior = [{
            "contract_symbol": candidate.contract_symbol,
            "emitted_at_utc": now - timedelta(hours=24),
            "ask": 1.17,
            "expected_edge_after_friction_pct": 0.14,
        }]
        result = apply_live_execution_policy([candidate], prior_exposures=prior, as_of_utc=now)

        self.assertEqual(result["kept"], 1)
        self.assertFalse(candidate.reentry_blocked)

    def test_history_loader_supports_legacy_contract_cost(self) -> None:
        now = datetime(2026, 8, 18, 15, tzinfo=timezone.utc)
        payload = {"entries": [{
            "run_generated_at_utc": (now - timedelta(hours=20)).isoformat(),
            "live_board": [{
                "symbol": "XLP",
                "contract_symbol": "XLP260828C00084500",
                "contract_cost": 117.0,
            }],
        }]}
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            rows = load_recent_live_exposures(path, as_of_utc=now, lookback_hours=72)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ask"], 1.17)

    def test_council_cannot_promote_execution_policy_veto(self) -> None:
        candidate = _candidate(spread_pct=0.20)
        apply_live_execution_policy([candidate])

        result = select_board(
            [candidate],
            MarketRegime(mode="neutral", bias=0.0, source_symbol="SPY"),
            live_size=1,
            shadow_size=0,
            minimum_live_score=0.0,
            max_live_extrinsic_ratio=1.0,
            fetch_live_corr=False,
        )

        self.assertTrue(result.abstain)
        self.assertEqual(result.summary["abstain_audit"]["primary_reason"], "execution_policy")


if __name__ == "__main__":
    unittest.main()
