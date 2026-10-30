"""Actual estimator, synthetic archived inputs; no real-data model claims."""
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine.tests.test_scout_pair_readiness import _pair_rows
from engine.orographic.scout_pair_readiness import build_scout_pair_readiness
from engine.orographic.scout_pair_evaluation import evaluate_pairs, POLICY
from engine.orographic.fold_evaluation_evidence import verify_fold_evaluation
from engine.orographic.rebuild_readiness import build_rebuild_readiness


class PairEvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        outcomes, archived = [], []
        for day in range(120):
            decision = datetime(2025, 1, 2, 15, tzinfo=UTC) + timedelta(days=day)
            for offset, label in enumerate(("call_edge", "put_edge", "no_trade")):
                number = day * 3 + offset
                pair = _pair_rows(number, label=label, decision_date=decision, regime="risk_on" if day%2 else "risk_off")
                for row in pair:
                    row["symbol"] = "AAA"  # three distinct pairs on the same symbol/date
                    row["decision_at_utc"] = decision.isoformat()
                outcomes.extend(pair)
                archived.append(dict(pair_id=f"PAIR-{number}", symbol="AAA",
                    decision_at_utc=decision.isoformat(), feature_available_at_utc=(decision-timedelta(hours=1)).isoformat(),
                    feature_observed_at_utc=(decision-timedelta(hours=2)).isoformat(),
                    feature_source_sha256="a"*64, features={"mom_5d":(number%17)/100, "rv20":.2},
                    baseline_emitted_at_utc=(decision-timedelta(minutes=5)).isoformat(),
                    baseline_artifact_sha256="b"*64, baseline_policy_id="synthetic-fixed-call",
                    baseline_action="call", additional_cost_return={"call":.01,"put":.02}))
        self.payload = dict(artifact="option_outcome_dataset", label_policy="strict_executable_quote_or_fill_v2", rows=outcomes)
        self.plan = build_scout_pair_readiness(self.payload)
        self.inputs = dict(artifact="scout_archived_pair_inputs", schema_version=1,
            evidence_kind="synthetic", comparison_policy=POLICY, baseline_artifact_sha256="b"*64,
            baseline_policy_id="synthetic-fixed-call", feature_names=["mom_5d","rv20"],
            outcomes=self.payload, rows=archived)

    def run_evaluation(self):
        raw = json.dumps(self.inputs).encode()
        path = self.root / "input.json"
        path.write_bytes(raw)
        return evaluate_pairs(path, sha256(raw).hexdigest(), self.plan, self.root/"run",
            evaluator_revision="c"*40, run_id="synthetic-integration")

    def test_actual_fit_to_reader_preserves_pairs_and_blocks_synthetic_promotion(self):
        result = self.run_evaluation()
        path = Path(result["manifest"])
        checked = verify_fold_evaluation(self.plan, path, result["sha256"])
        self.assertEqual(checked["status"], "verified")
        self.assertGreaterEqual(checked["completed_folds"], 3)
        self.assertEqual(checked["evidence_kind"], "synthetic")
        gate = build_rebuild_readiness(self.plan, {}, {}, {},
            fold_evaluation_manifest=path, fold_evaluation_sha256=result["sha256"])
        self.assertEqual(gate["gates"]["fold_frozen_validation"]["actual"], checked["completed_folds"])
        self.assertFalse(gate["gates"]["fold_frozen_validation"]["passed"])
        self.assertFalse(gate["production_model_change_allowed"])
        report = json.loads(path.read_bytes())
        fold = report["folds"][0]
        scores = json.loads((path.parent/fold["scores"]["path"]).read_bytes())
        self.assertEqual(len(scores["predictions"]), fold["validation_pairs"])
        self.assertEqual(len({r["pair_id"] for r in scores["predictions"]}), fold["validation_pairs"])
        self.assertLess(len(scores["daily_comparisons"]), fold["validation_pairs"])
        model_path = path.parent/fold["model_bundle"]["path"]
        import joblib
        state = joblib.load(model_path)  # only our own freshly generated synthetic fixture
        self.assertEqual(set(state["model"].classes_), {0,1,2})
        self.assertIsNone(state["calibrator"])
        score_path = path.parent/fold["scores"]["path"]
        original_score = score_path.read_bytes()
        original_manifest = path.read_bytes()
        scores["predictions"][0]["pair_id"] = "wrong-pair"
        score_path.write_text(json.dumps(scores), encoding="utf-8")
        fold["scores"]["sha256"] = sha256(score_path.read_bytes()).hexdigest()
        path.write_text(json.dumps(report), encoding="utf-8")
        self.assertEqual(verify_fold_evaluation(self.plan,path,sha256(path.read_bytes()).hexdigest())["reason"],
                         "prediction_pair_membership_mismatch")
        score_path.write_bytes(original_score)
        path.write_bytes(original_manifest)
        model_path.unlink()
        self.assertEqual(verify_fold_evaluation(self.plan,path,result["sha256"])["completed_folds"],0)

    def test_missing_duplicate_and_post_decision_features_rejected(self):
        import copy
        original = copy.deepcopy(self.inputs)
        for mutate in (
            lambda: self.inputs["rows"].pop(),
            lambda: self.inputs["rows"].append(self.inputs["rows"][0]),
            lambda: self.inputs["rows"][0].update(feature_available_at_utc="2026-01-01T00:00:00Z"),
            lambda: self.inputs["rows"][0].update(baseline_artifact_sha256="d"*64),
        ):
            self.inputs = copy.deepcopy(original)
            mutate()
            with self.assertRaises(ValueError):
                self.run_evaluation()
            self.assertFalse((self.root/"run/completion.json").exists())

    def test_changed_corpus_cannot_complete_old_plan(self):
        self.inputs["outcomes"]["rows"][0]["pnl_pct"] = .8
        with self.assertRaisesRegex(ValueError,"pair_corpus"):
            self.run_evaluation()

    def test_same_day_later_decision_cannot_hide_post_decision_features(self):
        row = self.inputs["rows"][0]
        row["decision_at_utc"] = "2025-01-02T17:00:00+00:00"
        row["feature_available_at_utc"] = "2025-01-02T16:00:00+00:00"
        with self.assertRaisesRegex(ValueError,"archived_decision_clock_mismatch"):
            self.run_evaluation()
        self.assertFalse((self.root/"run").exists())

    def test_external_hash_and_changed_split_fail_before_fit(self):
        raw = json.dumps(self.inputs).encode()
        path = self.root/"input.json"
        path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError,"input_bundle_hash_mismatch"):
            evaluate_pairs(path, "0"*64, self.plan, self.root/"run",
                evaluator_revision="c"*40, run_id="synthetic")
        self.plan["fold_frozen_evaluation_plan"]["folds"][0]["validation_start"] = "2025-01-01"
        with self.assertRaisesRegex(ValueError,"exact_plan_mismatch"):
            self.run_evaluation()
        self.assertFalse((self.root/"run").exists())

    def test_missing_baseline_lineage_fails_before_fit(self):
        self.inputs.pop("baseline_artifact_sha256")
        with self.assertRaisesRegex(ValueError,"invalid_archived_input_contract"):
            self.run_evaluation()
        self.assertFalse((self.root/"run").exists())

    def test_fit_failure_never_writes_completion_and_run_cannot_be_overwritten(self):
        with patch("engine.orographic.scout_pair_evaluation._side_estimator", side_effect=RuntimeError("synthetic interrupted fit")):
            with self.assertRaises(RuntimeError):
                self.run_evaluation()
        self.assertTrue((self.root/"run/inputs.json").exists())
        self.assertFalse((self.root/"run/completion.json").exists())
        with self.assertRaises(FileExistsError):
            self.run_evaluation()


if __name__ == "__main__":
    unittest.main()
