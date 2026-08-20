from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_dashboard_prospective_summary import build_dashboard_summary


def _pick(*, lane: str, status: str, value: float | None, run: str) -> dict:
    fixed_marks = {}
    if value is not None:
        fixed_marks["one_hour"] = {
            "pnl_pct_from_emission": value,
            "bid": 1.0,
            "ask": 1.2,
            "captured_at_utc": run,
        }
    return {
        "run_generated_at_utc": run,
        "lane": lane,
        "symbol": "SPY",
        "contract_symbol": "SPY260821C00600000",
        "emission_quote": {"bid": 1.0, "ask": 1.2, "mid": 1.1},
        "outcomes": {
            "status": status,
            "fixed_exit_marks": fixed_marks,
            "trajectory_marks": [{"captured_at_utc": run, "mark": 1.1}] * 100,
            "executable_labels": {"one_hour": {"large": "payload"}},
            "path_rules": {
                "take_profit_40_pct_before_stop_50_pct": value is not None and value >= 0.4,
                "first_hit": {"rule": "stop_50_pct", "window": "one_hour"} if value is not None and value <= -0.5 else None,
            },
        },
    }


class DashboardProspectiveSummaryTests(unittest.TestCase):
    def test_keeps_global_metrics_but_only_recent_compact_entries(self) -> None:
        ledger = {
            "artifact": "prospective_pick_ledger",
            "updated_at_utc": "2026-08-20T16:45:00Z",
            "aggregate": {"runs": 3},
            "outcome_policy": {"purpose": "Forward evidence"},
            "entries": [
                {"run_generated_at_utc": "2026-08-20T14:00:00Z", "picks": [_pick(lane="live", status="complete", value=0.5, run="2026-08-20T14:00:00Z")]},
                {"run_generated_at_utc": "2026-08-20T15:00:00Z", "picks": [_pick(lane="shadow", status="partial", value=-0.6, run="2026-08-20T15:00:00Z")]},
                {"run_generated_at_utc": "2026-08-20T16:00:00Z", "picks": [_pick(lane="shadow", status="pending", value=None, run="2026-08-20T16:00:00Z")]},
            ],
        }

        rendered = build_dashboard_summary(ledger, recent_entries=2)

        self.assertEqual(rendered["source_entry_count"], 3)
        self.assertEqual(len(rendered["entries"]), 2)
        self.assertEqual(rendered["dashboard_summary"]["picks"], 3)
        self.assertEqual(rendered["dashboard_summary"]["marked"], 2)
        self.assertEqual(rendered["dashboard_summary"]["take_profit_hits"], 1)
        self.assertEqual(rendered["dashboard_summary"]["stop_hits"], 1)
        compact = rendered["entries"][0]["picks"][0]["outcomes"]
        self.assertNotIn("trajectory_marks", compact)
        self.assertNotIn("executable_labels", compact)
        self.assertEqual(compact["fixed_exit_marks"]["one_hour"], {"pnl_pct_from_emission": -0.6})

    def test_real_ledger_projection_stays_well_below_pages_limit(self) -> None:
        source = Path("web/data/diagnostics/prospective_pick_ledger.json")
        if not source.exists():
            self.skipTest("Repository seed ledger is unavailable")
        ledger = json.loads(source.read_text(encoding="utf-8"))
        rendered = build_dashboard_summary(ledger)
        encoded = json.dumps(rendered).encode("utf-8")
        self.assertLess(len(encoded), 2 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
