from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from engine.orographic.counterfactual_veto_evidence import build_counterfactual_veto_evidence


def _pick(
    run_at: datetime,
    candidate: int,
    *,
    no_trade: float = 0.80,
    call: float = 0.10,
    put: float = 0.10,
    net_return: float | None = -0.20,
    side: str = "call",
    regime: str = "risk_on",
    model_hash: str = "side-v2",
    contract_symbol: str | None = None,
    label_version: int = 2,
) -> dict:
    outcomes: dict = {"executable_labels": {}}
    if net_return is not None:
        outcomes = {
            "executable_labels": {
                "friday_close": {
                    "label_contract": {"version": label_version},
                    "net_executable_return": net_return,
                    "net_executable_pnl_usd": net_return * 100,
                    "is_net_profitable": net_return > 0,
                },
            },
            "capture_attempts": {"friday_close": {"status": "captured_valid"}},
        }
    return {
        "lane": "counterfactual_observation" if no_trade >= 0.70 else "shadow",
        "symbol": f"SYM{candidate % 7}",
        "contract_symbol": contract_symbol or f"OPT{run_at:%Y%m%d}{candidate:03d}",
        "option_type": side,
        "risk_features": {
            "scout_call_edge_prob": call,
            "scout_put_edge_prob": put,
            "scout_no_trade_prob": no_trade,
        },
        "context": {
            "regime": {"mode": regime},
            "model_artifacts": {"scout_side_model": {"sha256": model_hash}},
        },
        "outcomes": outcomes,
    }


def _entry(run_at: datetime, picks: list[dict]) -> dict:
    return {"run_generated_at_utc": run_at.isoformat(), "picks": picks}


class CounterfactualVetoEvidenceTests(unittest.TestCase):
    def test_empty_cohort_is_advisory_and_collecting(self) -> None:
        artifact = build_counterfactual_veto_evidence({"entries": []})

        self.assertEqual(artifact["decision"], "collecting_evidence")
        self.assertEqual(artifact["execution_effect"], "none_advisory_only")
        self.assertEqual(artifact["coverage"]["independent_recommendations"], 0)
        self.assertIn("cannot alter thresholds", artifact["policy"]["authority"])

    def test_repeated_intraday_contract_is_counted_once(self) -> None:
        first = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
        contract = "SPY260814C00600000"
        ledger = {"entries": [
            _entry(first, [_pick(first, 1, contract_symbol=contract, net_return=-0.2)]),
            _entry(first + timedelta(hours=2), [_pick(first, 1, contract_symbol=contract, net_return=0.9)]),
        ]}

        artifact = build_counterfactual_veto_evidence(ledger)

        self.assertEqual(artifact["coverage"]["raw_scored_recommendations"], 2)
        self.assertEqual(artifact["coverage"]["independent_recommendations"], 1)
        self.assertEqual(artifact["coverage"]["repeated_scan_rows_excluded"], 1)
        self.assertAlmostEqual(
            artifact["current_rule"]["vetoed"]["mean_net_executable_return"],
            -0.2,
        )

    def test_strict_v2_labels_fail_closed(self) -> None:
        run_at = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
        legacy = _pick(run_at, 1, label_version=1)
        artifact = build_counterfactual_veto_evidence({"entries": [_entry(run_at, [legacy])]})

        self.assertEqual(artifact["coverage"]["independent_recommendations"], 1)
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 0)
        self.assertEqual(artifact["coverage"]["resolved_current_rule_vetoes"], 0)

    def test_friction_vetoes_are_outside_the_scout_threshold_estimand(self) -> None:
        run_at = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
        pick = _pick(run_at, 1, net_return=9.0)
        pick["lane"] = "friction_veto"
        artifact = build_counterfactual_veto_evidence({"entries": [_entry(run_at, [pick])]})

        self.assertEqual(artifact["coverage"]["independent_recommendations"], 0)
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 0)

    def test_latest_side_model_cohort_is_not_pooled(self) -> None:
        start = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
        old = _pick(start, 1, model_hash="old")
        new = _pick(start + timedelta(days=1), 2, model_hash="new")
        artifact = build_counterfactual_veto_evidence({"entries": [
            _entry(start, [old]),
            _entry(start + timedelta(days=1), [new]),
        ]})

        self.assertEqual(artifact["model_cohort"]["scout_side_model_sha256"], "new")
        self.assertEqual(artifact["coverage"]["independent_recommendations"], 1)
        self.assertEqual(artifact["coverage"]["older_side_model_rows_excluded"], 1)

    def test_well_powered_loss_avoiding_rule_becomes_review_eligible(self) -> None:
        start = datetime(2026, 6, 1, 14, tzinfo=timezone.utc)
        entries = []
        for day in range(40):
            run_at = start + timedelta(days=day)
            picks = []
            for candidate in range(3):
                picks.append(_pick(
                    run_at,
                    candidate,
                    net_return=-0.10 - candidate * 0.05,
                    side="call" if candidate % 2 == 0 else "put",
                    regime="risk_on" if day % 2 == 0 else "risk_off",
                ))
            entries.append(_entry(run_at, picks))

        artifact = build_counterfactual_veto_evidence({"entries": entries})

        self.assertEqual(artifact["decision"], "eligible_for_policy_review")
        self.assertEqual(artifact["coverage"]["resolved_current_rule_vetoes"], 120)
        self.assertEqual(artifact["coverage"]["independent_veto_trading_days"], 40)
        self.assertTrue(all(artifact["gates"]["sample"].values()))
        self.assertTrue(all(artifact["gates"]["performance"].values()))
        self.assertGreater(
            artifact["current_rule"]["veto_benefit"]["confidence_interval_95"]["lower"],
            0,
        )

    def test_frontier_includes_current_rule_and_retained_candidates(self) -> None:
        run_at = datetime(2026, 8, 3, 14, tzinfo=timezone.utc)
        vetoed = _pick(run_at, 1, no_trade=0.8, call=0.1, put=0.1)
        retained = _pick(run_at, 2, no_trade=0.6, call=0.3, put=0.1, net_return=0.2)
        artifact = build_counterfactual_veto_evidence({"entries": [_entry(run_at, [vetoed, retained])]})
        current = next(
            row for row in artifact["threshold_frontier"]
            if row["no_trade_threshold"] == 0.7 and row["margin_threshold"] == 0.2
        )

        self.assertEqual(current["vetoed"]["observations"], 1)
        self.assertEqual(current["retained"]["observations"], 1)


if __name__ == "__main__":
    unittest.main()
