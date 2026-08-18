from __future__ import annotations

from datetime import date
from pathlib import Path
import unittest

from engine.backtest.alpha_experiment import (
    _path_shadow_board,
    _summarize_path_shadow_week,
    apply_path_tiebreaker,
    apply_symbol_priors,
    build_symbol_priors,
    build_variants,
    default_variant_option_outcome_paths,
    estimated_cost_basis,
    filter_by_cost_basis,
)
from engine.backtest.pricer import TradeLeg
from engine.orographic.schemas import ContractCandidate


def _candidate(symbol: str = "TEST", **overrides: object) -> ContractCandidate:
    payload = {
        "symbol": symbol,
        "contract_symbol": f"{symbol}260410C00100000",
        "option_type": "call",
        "expiry": "2026-04-10",
        "strike": 100.0,
        "bid": 1.4,
        "ask": 1.5,
        "last": 1.45,
        "premium": 1.5,
        "contract_cost": 150.0,
        "spread_pct": 0.04,
        "open_interest": 500,
        "volume": 300,
        "implied_volatility": 0.25,
        "delta": 0.45,
        "moneyness": 0.0,
        "projected_move_pct": 0.03,
        "breakeven_move_pct": 0.02,
        "expected_return_pct": 0.6,
        "extrinsic_ratio": 0.7,
        "scout_score": 0.6,
        "forge_score": 0.6,
        "allocation_weight": 1.0,
        "notes": [],
    }
    payload.update(overrides)
    return ContractCandidate(**payload)


def _trade(symbol: str, exit_day: date, pnl: float, pnl_pct: float) -> TradeLeg:
    return TradeLeg(
        symbol=symbol,
        contract_symbol=f"{symbol}260410C00100000",
        option_type="call",
        strike=100.0,
        expiry="2026-04-10",
        entry_date=exit_day,
        exit_date=exit_day,
        entry_spot=100.0,
        exit_spot=101.0,
        entry_price=1.0,
        exit_price=1.2,
        contracts=1,
        cost_basis=100.0,
        exit_value=120.0,
        pnl=pnl,
        pnl_pct=pnl_pct,
        expired_worthless=False,
        forge_score=0.6,
        scout_score=0.5,
        implied_volatility=0.25,
    )


class AlphaExperimentTests(unittest.TestCase):
    def test_build_variants_includes_path_tiebreaker_variant(self) -> None:
        variants = build_variants(600.0)
        names = [row.name for row in variants]
        self.assertIn("council_cost_cap_path_tiebreaker", names)
        self.assertIn("council_cost_cap_path_tiebreaker_loose", names)

    def test_build_variants_exposes_exact_unified_ablations(self) -> None:
        variants = build_variants(600.0, unified_ablation_only=True)
        stacks = {row.model_stack for row in variants}

        self.assertEqual(
            stacks,
            {
                "current_gated",
                "unified_rnd",
                "unified_no_hierarchical",
                "unified_no_path",
                "unified_no_cost_aware",
                "unified_primary_only",
            },
        )
        self.assertTrue(all(row.council_only for row in variants))
        self.assertTrue(all(row.shadow_size == 0 for row in variants))

    def test_council_risk_ablation_includes_production_core_policy(self) -> None:
        variants = build_variants(600.0, council_risk_ablation_only=True)
        by_name = {row.name: row for row in variants}

        production = by_name["unified_production_core_policy"]
        self.assertEqual(production.live_size, 1)
        self.assertEqual(production.minimum_live_score, 0.86)
        self.assertEqual(production.minimum_put_live_score, 0.84)
        self.assertEqual(production.max_live_extrinsic_ratio, 0.90)
        self.assertEqual(by_name["unified_research_reference_live3_score57"].live_size, 3)
        self.assertEqual(by_name["unified_live1_score68"].minimum_put_live_score, 0.66)

    def test_path_shadow_board_prefers_higher_holding_quality(self) -> None:
        live = _candidate("LIVE", forge_score=0.7)
        live.path_holding_quality_score = 0.45
        alt = _candidate("ALT", forge_score=0.65)
        alt.path_holding_quality_score = 0.82
        board = _path_shadow_board([live, alt], board_size=1)

        self.assertEqual([row.symbol for row in board], ["ALT"])

    def test_path_shadow_summary_tracks_disagreement_and_pnl(self) -> None:
        chosen = [_candidate("LIVE")]
        chosen[0].path_holding_quality_score = 0.45
        path_shadow = [_candidate("ALT")]
        path_shadow[0].path_holding_quality_score = 0.82

        live_trade = _trade("LIVE", date(2026, 4, 14), 20.0, 0.20)
        alt_trade = _trade("ALT", date(2026, 4, 14), 35.0, 0.35)
        summary = _summarize_path_shadow_week(chosen, path_shadow, [live_trade], [alt_trade])

        self.assertTrue(summary["disagreement"])
        self.assertEqual(summary["chosen_contracts"], [chosen[0].contract_symbol])
        self.assertEqual(summary["path_shadow_contracts"], [path_shadow[0].contract_symbol])
        self.assertEqual(summary["chosen_week_pnl"], 20.0)
        self.assertEqual(summary["path_shadow_week_pnl"], 35.0)

    def test_apply_path_tiebreaker_boosts_high_quality_near_ties(self) -> None:
        lead = _candidate("LEAD", forge_score=0.60)
        lead.path_holding_quality_score = 0.50
        alt = _candidate("ALT", forge_score=0.595)
        alt.path_holding_quality_score = 0.90

        adjusted, diag = apply_path_tiebreaker([lead], [lead, alt], max_swaps=1, max_forge_gap=0.03, min_path_quality_edge=0.10)

        self.assertEqual(adjusted[0].symbol, "ALT")
        self.assertEqual(diag["swaps"], 1)
        self.assertEqual(diag["top_symbols_before"][0], "LEAD")
        self.assertEqual(diag["top_symbols_after"][0], "ALT")
        self.assertEqual(diag["considered_candidates"], 1)

    def test_apply_path_tiebreaker_records_near_miss_reason(self) -> None:
        lead = _candidate("LEAD", forge_score=0.60)
        lead.path_holding_quality_score = 0.50
        alt = _candidate("ALT", forge_score=0.52)
        alt.path_holding_quality_score = 0.90

        adjusted, diag = apply_path_tiebreaker([lead], [lead, alt], max_swaps=1, max_forge_gap=0.03, min_path_quality_edge=0.10)

        self.assertEqual(adjusted[0].symbol, "LEAD")
        self.assertEqual(diag["swaps"], 0)
        self.assertEqual(diag["considered_candidates"], 1)
        self.assertEqual(diag["near_miss_details"][0]["candidate_symbol"], "ALT")
        self.assertEqual(diag["near_miss_details"][0]["blocker"], "forge_gap_above_maximum")

    def test_default_variant_option_outcome_paths_target_output_directory(self) -> None:
        paths = default_variant_option_outcome_paths(
            Path("docs/alpha_experiment_results.json"),
            ["council_only", "council_cost_cap"],
        )

        self.assertEqual(
            paths["council_only"],
            Path("output/option_outcomes_alpha_experiment_council_only.json"),
        )
        self.assertEqual(
            paths["council_cost_cap"],
            Path("output/option_outcomes_alpha_experiment_council_cost_cap.json"),
        )

    def test_default_variant_option_outcome_paths_respect_custom_base_name(self) -> None:
        paths = default_variant_option_outcome_paths(
            Path("docs/walkforward_april.json"),
            ["baseline_all_candidates"],
            output_dir=Path("tmp"),
        )

        self.assertEqual(
            paths["baseline_all_candidates"],
            Path("tmp/option_outcomes_walkforward_april_baseline_all_candidates.json"),
        )

    def test_filter_by_cost_basis_drops_expensive_candidates(self) -> None:
        cheap = _candidate(symbol="CHEAP", ask=1.5, premium=1.5, contract_cost=150.0)
        expensive = _candidate(
            symbol="EXP",
            ask=3.0,
            premium=3.0,
            contract_cost=300.0,
            allocation_weight=3.0,
            scout_score=1.0,
        )

        kept, diag = filter_by_cost_basis(
            [cheap, expensive],
            500.0,
            budget=300.0,
            hard_cost_ceiling=600.0,
        )

        self.assertEqual([row.symbol for row in kept], ["CHEAP"])
        self.assertEqual(diag["dropped"], 1)
        self.assertEqual(
            estimated_cost_basis(expensive, budget=300.0, hard_cost_ceiling=600.0),
            600.0,
        )

    def test_apply_symbol_priors_boosts_winners_and_excludes_losers(self) -> None:
        monday = date(2026, 4, 14)
        trades = [
            _trade("WIN", date(2026, 3, 3), 80.0, 0.80),
            _trade("WIN", date(2026, 3, 10), 70.0, 0.70),
            _trade("WIN", date(2026, 3, 17), 60.0, 0.60),
            _trade("WIN", date(2026, 3, 24), 65.0, 0.65),
            _trade("WIN", date(2026, 3, 31), 55.0, 0.55),
            _trade("LOSE", date(2026, 3, 3), -90.0, -0.90),
            _trade("LOSE", date(2026, 3, 10), -80.0, -0.80),
            _trade("LOSE", date(2026, 3, 17), -75.0, -0.75),
            _trade("LOSE", date(2026, 3, 24), -70.0, -0.70),
            _trade("LOSE", date(2026, 3, 31), -65.0, -0.65),
        ]
        priors = build_symbol_priors(trades, monday, lookback_weeks=12, min_trades=5)

        adjusted, diag = apply_symbol_priors(
            [_candidate("WIN"), _candidate("MID"), _candidate("LOSE")],
            priors,
            top_n=1,
            bottom_n=1,
            boost=0.03,
        )

        self.assertEqual([row.symbol for row in adjusted], ["WIN", "MID"])
        win_candidate = adjusted[0]
        self.assertAlmostEqual(win_candidate.forge_score, 0.63, places=4)
        self.assertIn("WIN", diag["boosted_symbols"])
        self.assertIn("LOSE", diag["excluded_symbols"])


if __name__ == "__main__":
    unittest.main()
