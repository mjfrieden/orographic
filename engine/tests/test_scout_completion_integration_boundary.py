"""Synthetic proof that the existing trainer is not the pair-plan producer."""
from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from engine.orographic.fold_evaluation_evidence import verify_fold_evaluation
from engine.orographic.scout_pair_readiness import _pair_records, build_scout_pair_readiness
from engine.tests.test_scout_pair_readiness import _pair_rows
from engine.train_scout_model import _load_option_outcome_labels, _directional_option_training_frame


class ScoutCompletionIntegrationBoundaryTests(unittest.TestCase):
    def test_actual_loader_collapses_distinct_pair_labels_and_cannot_satisfy_plan(self):
        rows = []
        for number, label in ((0, "call_edge"), (20, "put_edge")):
            rows.extend(_pair_rows(number, label=label,
                decision_date=datetime(2026, 1, 2, tzinfo=UTC), regime="risk_on"))
        payload = {"artifact": "option_outcome_dataset",
                   "label_policy": "strict_executable_quote_or_fill_v2", "rows": rows}
        records, _ = _pair_records(rows)
        self.assertEqual([r["label"] for r in records], ["call_edge", "put_edge"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            actual_frame, actual_report = _load_option_outcome_labels([path])
            # The real loader averages both pairs at a single symbol/date,
            # resolving the resulting positive tie to call. It has not trained
            # or evaluated both of the planned pair records.
            self.assertEqual(len(actual_frame), 1)
            self.assertEqual(actual_frame.iloc[0]["side_label"], "call_edge")
            self.assertEqual(len(_directional_option_training_frame(actual_frame)), 1)
            path.write_text(json.dumps(actual_report), encoding="utf-8")
            from hashlib import sha256
            verified = verify_fold_evaluation(build_scout_pair_readiness(payload),
                path, sha256(path.read_bytes()).hexdigest())
            self.assertEqual(verified["completed_folds"], 0)
            self.assertEqual(verified["reason"], "invalid_evaluation_contract")


if __name__ == "__main__":
    unittest.main()
