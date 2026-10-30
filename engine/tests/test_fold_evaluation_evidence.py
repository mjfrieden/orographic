"""Synthetic contract tests; these fixtures establish no real model edge."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from engine.orographic.fold_evaluation_evidence import verify_fold_evaluation
from engine.orographic.rebuild_readiness import build_rebuild_readiness


class FoldEvaluationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / "evaluation.json"
        self.pairs = {
            "source": {"label_policy": "strict_executable_quote_or_fill_v2"},
            "coverage": {"evidence_sha256": "a" * 64},
            "fold_frozen_evaluation_plan": {"ready_folds": 5, "folds": []},
        }
        self.report = {
            "artifact": "scout_fold_frozen_evaluation", "schema_version": 1,
            "status": "completed", "execution_effect": "none_research_only",
            "active_model_change_allowed": False, "source_evidence_sha256": "a" * 64,
            "label_policy": "strict_executable_quote_or_fill_v2",
            "run_id": "synthetic-test-run", "candidate_id": "synthetic-test-candidate",
            "evaluator_revision": "b" * 40, "completed_at_utc": "2026-10-01T12:00:00Z", "folds": [],
        }
        for number in range(1, 4):
            plan = {
                "fold": number, "ready": True, "validation_pairs": 20,
                "training_evidence_sha256": str(number) * 64,
                "validation_evidence_sha256": str(number + 3) * 64,
                "training_labels_available_through": f"2026-09-0{number}",
                "validation_start": f"2026-09-0{number + 1}",
            }
            self.pairs["fold_frozen_evaluation_plan"]["folds"].append(plan)
            model = self.write(f"model-{number}.bin", f"synthetic model {number}".encode())
            scores = {
                "artifact": "scout_fold_validation_scores", "status": "completed",
                "fold": number, "model_bundle_sha256": model["sha256"],
                **{key: self.report[key] for key in ("run_id", "candidate_id", "evaluator_revision")},
                **{key: plan[key] for key in ("training_evidence_sha256", "validation_evidence_sha256")},
                "evaluated_pairs": 20,
                "metrics": {key: 0.0 for key in ("mean_after_cost_return", "downside_decile", "baseline_mean_after_cost_return", "baseline_downside_decile")},
            }
            fold = {
                **plan, "status": "completed", "model_bundle": model,
                "scores": self.write(f"scores-{number}.json", json.dumps(scores).encode()),
                "frozen_at_utc": "2026-10-01T10:00:00Z",
                "scoring_started_at_utc": "2026-10-01T10:01:00Z",
                "completed_at_utc": "2026-10-01T11:00:00Z",
            }
            self.report["folds"].append(fold)

    def write(self, name, raw):
        (self.root / name).write_bytes(raw)
        return {"path": name, "sha256": sha256(raw).hexdigest()}

    def save(self):
        return self.write(self.path.name, json.dumps(self.report).encode())["sha256"]

    def verify(self):
        return verify_fold_evaluation(self.pairs, self.path, self.save())

    def test_completed_immutable_evidence_counts(self):
        self.assertEqual(self.verify()["completed_folds"], 3)
        result = build_rebuild_readiness(self.pairs, {}, {}, {},
            fold_evaluation_manifest=self.path, fold_evaluation_sha256=self.save())
        # Legacy/unclassified receipts are diagnosable, not production evidence.
        self.assertFalse(result["gates"]["fold_frozen_validation"]["passed"])
        self.assertFalse(result["production_model_change_allowed"])

    def test_planned_only_and_missing_manifest_fail_closed(self):
        for path, digest in ((None, None), (self.path, "c" * 64)):
            self.assertEqual(verify_fold_evaluation(self.pairs, path, digest)["completed_folds"], 0)

    def test_missing_or_changed_artifact_fails_closed(self):
        (self.root / "model-1.bin").unlink()
        self.assertEqual(self.verify()["completed_folds"], 0)
        (self.root / "model-1.bin").write_bytes(b"changed")
        self.assertEqual(self.verify()["reason"], "artifact_hash_mismatch")

    def test_changed_manifest_requires_external_pin(self):
        digest = self.save()
        self.path.write_bytes(self.path.read_bytes() + b" ")
        self.assertEqual(verify_fold_evaluation(self.pairs, self.path, digest)["reason"], "manifest_hash_mismatch")

    def test_stale_corpus_or_fold_provenance_fails_closed(self):
        self.pairs["coverage"]["evidence_sha256"] = "c" * 64
        self.assertEqual(self.verify()["reason"], "source_evidence_mismatch")
        self.pairs["coverage"]["evidence_sha256"] = "a" * 64
        self.report["folds"][0]["validation_evidence_sha256"] = "c" * 64
        self.assertEqual(self.verify()["reason"], "fold_evidence_mismatch")

    def test_duplicate_unfinished_future_or_unfrozen_folds_fail_closed(self):
        original = deepcopy(self.report)
        mutations = [
            lambda r: r["folds"].append(deepcopy(r["folds"][0])),
            lambda r: r["folds"][0].update(status="planned"),
            lambda r: r.update(completed_at_utc="2099-10-01T12:00:00Z"),
            lambda r: r["folds"][0].update(frozen_at_utc="2026-10-01T10:02:00Z"),
            lambda r: r["folds"][0].update(model_bundle=r["folds"][1]["model_bundle"]),
            lambda r: r["folds"][0]["model_bundle"].update(path="../outside.bin"),
        ]
        for mutate in mutations:
            self.report = deepcopy(original)
            mutate(self.report)
            with self.subTest(report=self.report):
                self.assertEqual(self.verify()["completed_folds"], 0)

    def test_wrong_score_identity_or_incomplete_scores_fail_closed(self):
        fold = self.report["folds"][0]
        score_path = self.root / fold["scores"]["path"]
        original = json.loads(score_path.read_bytes())
        for changes in ({"candidate_id": "wrong"}, {"evaluated_pairs": 0}, {"metrics": {}}, {"evaluated_pairs": True}):
            fold["scores"] = self.write(score_path.name, json.dumps({**original, **changes}).encode())
            self.assertEqual(self.verify()["completed_folds"], 0)

    def test_malformed_document_fails_closed(self):
        for doc in ([], None, {"artifact": "scout_fold_frozen_evaluation"}):
            digest = self.write(self.path.name, json.dumps(doc).encode())["sha256"]
            self.assertEqual(verify_fold_evaluation(self.pairs, self.path, digest)["completed_folds"], 0)

    def test_existing_producer_reports_are_not_completion_receipts(self):
        repo = Path(__file__).resolve().parents[2]
        for relative in (
            "engine/orographic/models/scout_model_card.json",
            "web/data/diagnostics/payoff_stack_fold_frozen_audit_latest.json",
            "web/data/diagnostics/scout_pair_readiness_latest.json",
        ):
            with self.subTest(report=relative):
                digest = self.write(self.path.name, (repo / relative).read_bytes())["sha256"]
                verified = verify_fold_evaluation(self.pairs, self.path, digest)
                self.assertEqual(verified["completed_folds"], 0)
                self.assertEqual(verified["reason"], "invalid_evaluation_contract")


if __name__ == "__main__":
    unittest.main()
