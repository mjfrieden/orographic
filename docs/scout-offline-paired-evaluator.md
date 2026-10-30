# Offline paired Scout evaluator

`scripts/evaluate_scout_pairs.py` now implements the producer for v2 completion
receipts. It reuses the existing three-class Scout estimator specification via
`train_scout_model._side_estimator`; the existing training path uses that same
helper with unchanged parameters. It does not call the networked trainer or
write active model files. No new challenger/model family is introduced.

The evaluator retains one row per explicit pair, including no-trade labels,
and recomputes the exact five-fold plan and corpus hash. It runs every ready
fold and requires at least three. It fits only on each fold's purged training
rows, saves the actual model and RobustScaler before validation prediction,
and records calibration as explicitly absent. The existing side classifier is
uncalibrated; no calibrator or probability calibration is fabricated.

## Archived input bundle

Pass an externally pinned JSON file with these fields:

- `artifact: scout_archived_pair_inputs`, `schema_version: 1`,
  `evidence_kind: real` or `synthetic`.
- `comparison_policy: scout_three_class_argmax_strict_executable_returns_v1`.
- `baseline_policy_id` and `baseline_artifact_sha256`: the identity of the
  archived baseline that emitted the supplied actions. This is recorded
  provenance, not independent provider authentication.
- `outcomes`: the canonical `option_outcome_dataset` with
  `label_policy: strict_executable_quote_or_fill_v2` and original rows.
- `feature_names`: a unique, nonempty subset of the existing Scout core and
  cross-asset feature names listed in the evaluator's `FEATURES` constant.
  Outcome/score fields cannot substitute for training features.
- `rows`: exactly one archived input row per valid `pair_id`, no duplicates,
  extras or missing pairs. Each contains `symbol`, `decision_at_utc`,
  `feature_observed_at_utc`, `feature_available_at_utc`,
  `feature_source_sha256`, and `features` containing exactly the declared
  numeric feature names. Observation <= availability <= decision is required;
  decision date and symbol must match the pair. All clocks require timezones.
  Both original outcome legs must contain the same `decision_at_utc` as the
  archived feature row. A later timestamp on the same date cannot be used to
  conceal post-decision features.
- Each row also contains `baseline_action` (`call`, `put`, or `no_trade`),
  `baseline_emitted_at_utc` at or before the decision, matching
  `baseline_policy_id`/`baseline_artifact_sha256`, and
  `additional_cost_return: {call: NUMBER, put: NUMBER}`. These nonnegative,
  explicitly supplied return deductions represent any additional fees/slippage
  beyond the strict executable labels. Zero must be explicit, never inferred.

The fixed comparison rule applies classifier argmax to call/put/no-trade;
no-trade receives zero return. Candidate and baseline actions use the same
pair's executable outcome and cost convention. It averages pair returns within
each decision date before calculating mean and downside-decile returns. This
is a pair-conditioned diagnostic, not a reconstruction of portfolio sizing,
order routing or a claim of full production-policy superiority. The baseline
must come from the archived observations; never fill it with hindsight or
silently substitute a cash baseline.

## Explicit execution

```
python scripts/evaluate_scout_pairs.py --inputs ARCHIVED.json --inputs-sha256 EXPECTED_SHA256 --pair-readiness PLAN.json --output-dir NEW_RUN_DIRECTORY --evaluator-revision BASE_COMMIT_SHA --run-id UNIQUE_RUN_ID
```

The output directory must not exist. Inputs and four evaluator source files
are retained verbatim with SHA-256 identities, so local source modifications
remain identifiable in addition to the supplied base revision. Each fold has
a model bundle and a score file containing pair predictions and independent
date comparisons. `completion.json` is written last, after every ready fold
has successfully scored. Interrupted runs retain incomplete evidence and no
completion manifest; restarting cannot overwrite them.

Use the emitted manifest path and byte hash with
`build_rebuild_readiness.py --fold-evaluation-manifest ... --fold-evaluation-sha256 ...`.
The reader verifies source files, input/plan identity, fold model and score
hashes, exact prediction membership, cost arithmetic and date-weighted metrics.
Only **v2 real** evidence can satisfy the validation gate. V1 receipts remain
readable for diagnostics but cannot qualify; synthetic receipts report actual
software completion counts while keeping the gate and production authority
closed. Other existing promotion gates still apply.

No scheduled workflow automatically runs this evaluator. It must first receive
a verified archived input bundle and an approved bounded evaluation run.
Tests use real estimator fits on explicitly synthetic inputs; they establish
software behavior only. No real-data completion claim has been generated.
