from __future__ import annotations

import unittest

from engine.orographic.exit_policy import build_exit_policy_shadow_artifact, evaluate_pick_exit_policies


def _label(price: float, bid: float, ask: float, observed: str) -> dict:
    return {
        "label_available_at_utc": observed,
        "exit": {
            "execution_price": price,
            "execution_price_source": "exit_bid_proxy",
            "quote": {"bid": bid, "ask": ask, "observed_at_utc": observed},
        },
    }


class ExitPolicyTests(unittest.TestCase):
    def test_standing_limit_requires_bid_not_midpoint(self) -> None:
        pick = {
            "recommendation_id": "rec-1",
            "run_generated_at_utc": "2026-08-17T15:00:00+00:00",
            "contract_symbol": "XLP1",
            "lane": "live",
            "emission_quote": {"ask": 1.00},
            "outcomes": {
                "trajectory_marks": [
                    {"captured_at_utc": "2026-08-17T16:00:00+00:00", "bid": 1.10, "ask": 1.50, "mark": 1.30},
                    {"captured_at_utc": "2026-08-17T17:00:00+00:00", "bid": 1.25, "ask": 1.40, "mark": 1.325},
                ],
                "executable_labels": {
                    "next_day_close": _label(0.90, 0.90, 1.00, "2026-08-18T20:00:00+00:00"),
                },
            },
        }

        rows = {row["policy_id"]: row for row in evaluate_pick_exit_policies(pick)}

        self.assertEqual(rows["standing_limit_25"]["exit_price"], 1.25)
        self.assertEqual(rows["standing_limit_25"]["exit_at_utc"], "2026-08-17T17:00:00+00:00")
        self.assertEqual(rows["standing_limit_25"]["exit_reason"], "standing_limit_filled_at_recorded_bid")
        self.assertEqual(rows["standing_limit_40"]["exit_price"], 0.90)

    def test_unresolved_policy_is_not_credited(self) -> None:
        pick = {
            "recommendation_id": "rec-2",
            "contract_symbol": "XLE1",
            "lane": "live",
            "emission_quote": {"ask": 1.50},
            "outcomes": {"trajectory_marks": [{"captured_at_utc": "2026-08-19T16:00:00+00:00", "bid": 1.60, "ask": 2.00}]},
        }

        rows = evaluate_pick_exit_policies(pick)

        self.assertTrue(all(not row["is_resolved"] for row in rows))
        self.assertTrue(all(row["net_executable_return"] is None for row in rows))

    def test_artifact_is_flat_and_datamart_friendly(self) -> None:
        pick = {
            "recommendation_id": "rec-3",
            "run_generated_at_utc": "2026-08-19T15:00:00+00:00",
            "contract_symbol": "SPY1",
            "lane": "live",
            "emission_quote": {"ask": 1.00},
            "outcomes": {"executable_labels": {"next_day_close": _label(1.10, 1.10, 1.15, "2026-08-20T20:00:00+00:00")}},
        }
        artifact = build_exit_policy_shadow_artifact({"entries": [{"picks": [pick]}]})

        self.assertEqual(artifact["mode"], "shadow_only")
        self.assertEqual(artifact["summary"]["recommendations"], 1)
        self.assertEqual(len(artifact["rows"]), 4)
        self.assertTrue(all(row["recommendation_id"] == "rec-3" for row in artifact["rows"]))


if __name__ == "__main__":
    unittest.main()
