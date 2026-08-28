from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.validate_snapshot_smoke import SnapshotSmokeError, validate_snapshot_artifacts


class SnapshotSmokeTests(unittest.TestCase):
    def _fixture(self, root: Path, *, abstain: bool) -> tuple[Path, Path, Path]:
        diagnostics = root / "diagnostics"
        diagnostics.mkdir()
        snapshot = root / "latest.json"
        prospective = root / "prospective.json"
        snapshot.write_text(json.dumps({
            "scan_settings": {"model_stack": "production_v2"},
            "summary": {"scout_signal_count": 0, "forge_candidate_count": 0},
            "regime": {"mode": "risk_on"},
            "scout_signals": [],
            "forge_candidates": [],
            "council": {
                "abstain": abstain,
                "live_board": [],
                "shadow_board": [],
                "summary": {"candidate_count": 0, "live_count": 0, "shadow_count": 0},
            },
            "model_artifacts": {
                "required": {"required": True, "present": True, "sha256": "abc"},
                "optional": {"required": False, "present": False, "sha256": None},
            },
        }), encoding="utf-8")
        prospective.write_text(json.dumps({"artifact": "prospective_pick_ledger"}), encoding="utf-8")
        (diagnostics / "board_recommendation_history.json").write_text("{}", encoding="utf-8")
        return snapshot, prospective, diagnostics

    def test_zero_signal_explicit_abstention_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = self._fixture(Path(tmpdir), abstain=True)
            result = validate_snapshot_artifacts(*paths)

        self.assertEqual(result["scout_signal_count"], 0)
        self.assertTrue(result["abstain"])

    def test_zero_signal_snapshot_without_abstention_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = self._fixture(Path(tmpdir), abstain=False)
            with self.assertRaisesRegex(SnapshotSmokeError, "explicit Council abstention"):
                validate_snapshot_artifacts(*paths)


if __name__ == "__main__":
    unittest.main()
