# Scout fold completion evidence

**October 30 update:** the offline paired producer now exists. See
`scout-offline-paired-evaluator.md` for its v2 input/output contract and current
gate behavior. The v1 proposal and integration-gap notes below are retained as
historical design context; v1 receipts no longer qualify for readiness.

`ready_folds` in `scout_pair_readiness` measures whether a split has enough
training/validation pairs. It does not mean training or scoring occurred.
Rebuild readiness now requires at least three verified completed folds (the
existing threshold), independently of how many folds are planned.

Supply `scripts/build_rebuild_readiness.py` with both
`--fold-evaluation-manifest PATH` and `--fold-evaluation-sha256 SHA256`.
The latter must be the expected byte hash from the evaluation's retained run
identity, not a hash silently recomputed by the readiness reader. Omitting
either leaves the gate closed. Current scheduled invocations omit both and
therefore remain closed until a completed run is deliberately connected.

The v1 manifest must contain:

- `artifact: scout_fold_frozen_evaluation`, integer `schema_version: 1`,
  `status: completed`, `execution_effect: none_research_only`, and
  `active_model_change_allowed: false`.
- Nonempty `run_id` and `candidate_id`, the evaluator's full 40-character Git
  `evaluator_revision`, timezone-aware `completed_at_utc`,
  `label_policy: strict_executable_quote_or_fill_v2`, and
  `source_evidence_sha256` equal to the current pair corpus hash.
- `folds`: unique completed fold receipts. Each must match a ready plan's
  integer `fold`, `training_evidence_sha256`, `validation_evidence_sha256`,
  `training_labels_available_through`, and `validation_start`.
- Each fold records ordered, timezone-aware `frozen_at_utc`,
  `scoring_started_at_utc`, and `completed_at_utc`. These are execution clocks
  for a historical evaluation, not claims of prospective model availability.
- Each fold references `model_bundle` and `scores` using objects with relative
  `path` and byte `sha256`. Paths must resolve inside the manifest directory.
  Retain the model, scaler and calibrator together as one immutable per-fold
  bundle. A bundle hash cannot be reused for another fold. Verification reads
  bytes only and never deserializes executable models.

The score file is JSON with `artifact: scout_fold_validation_scores`,
`status: completed`, and matching `fold`, `run_id`, `candidate_id`,
`evaluator_revision`, `model_bundle_sha256`, `training_evidence_sha256`, and
`validation_evidence_sha256`. Its integer `evaluated_pairs` must equal the
plan's positive `validation_pairs`. `metrics` must include finite numerical
`mean_after_cost_return`, `downside_decile`, `baseline_mean_after_cost_return`,
and `baseline_downside_decile`. Retain detailed predictions alongside the
evaluation for independent review; the readiness reader checks the pinned
completion receipt rather than rerunning the model.

Any missing file, changed bytes, malformed receipt, duplicated fold, stale
corpus/split, future completion, leaked training-label boundary, incomplete
score count or mismatched provenance invalidates the supplied run. Merely
regenerating a plan timestamp with identical evidence does not make a pinned
run stale. The gate exposes the failure reason and verified completion count.

This is a new receipt contract: the existing plan and the separate payoff-stack
audit are not completion receipts and cannot be upgraded by relabeling them.
No completed real Scout evaluation was found during this repair. The existing
evaluation producer must emit these receipts and retain its actual artifacts
when the pre-registered evaluation is run. Synthetic test fixtures demonstrate
software behavior only. Passing completion does not establish model edge,
independent-date after-cost improvement or a verified production rollback.

## Existing producer integration gap (verified at f6b6fed)

There is currently **no production evaluator that emits this manifest format**.
This patch is a fail-closed reader and proposed receipt interface, not a
completed end-to-end repair of Scout evaluation publication.

`engine/train_scout_model.py::_primary_cv_predictions` returns in-memory raw and
calibrated probabilities, thresholds and fold summaries. It applies a nested
calibration split with a configurable calendar-day embargo. Its training path
later saves final active artifacts, not immutable bundles for the outer folds.
`_load_option_outcome_labels` aggregates outcomes by symbol/date, whereas the
readiness plan hashes individual explicit pair records. These are different
corpora and cannot share a completion hash or pair count without a verified
mapping. The model card's `cross_validation` explicitly describes a primary
binary target, not completed evaluation of the matched-pair plan.

`_side_cv_report` and `_hierarchical_cv_report` also discard per-fold models and
scalers; their fold counts adapt to row count. The payoff-stack audit fits a
different model on TradeExamples and explicitly discards its frozen fold
artifacts. None can be converted into these receipts by renaming fields.

The next producer change must be designed against the chosen Scout target:
pin the explicit-pair feature mapping and exact split membership; run the
existing model family with calibration/thresholds confined to training data;
retain each fold's model/scaler/calibrator before validation scoring; retain
predictions, after-cost baseline comparisons, and their identities; then write
the completion manifest once all intended folds have actually finished. The
producer and reader need an end-to-end synthetic test before a real run. A
summary-to-receipt adapter would falsely attest missing artifacts, so none is
provided. Scheduled readiness must remain closed until that work is complete.
