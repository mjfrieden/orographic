# Primary Scout calibration evaluation

`engine/train_scout_model.py` now separates evaluation of the primary binary
Scout pipeline from fitting the final production calibrator and threshold.
No committed model, scaler, manifest, card, or live routing setting is changed
by this code update. Existing persisted cards retain their historical metrics;
they are not retroactively corrected or evidence for the new evaluator.

## Evaluation contract

- Outer walk-forward folds group all observations from the same UTC calendar
  date. Training feature dates precede validation; training labels must be
  available strictly before validation starts, less the embargo below.
- Within each outer training subset, inner date-grouped purged folds generate
  raw predictions. The calibrator and balanced-accuracy threshold are fitted
  only on those inner predictions and their available training-subset labels.
- Every inner and outer scaler, class/regime sample-weight normalization, and
  estimator is fitted on its own training rows. The fixed estimator definition
  is shared with the final refit, using the preexisting final model parameters
  (500 trees, learning rate 0.04); no parameter search is introduced.
- Each outer fold's calibrator and threshold are frozen before scoring its
  validation observations. Outer validation labels do not decide whether to
  predict: single-class validation windows retain Brier/log-loss evidence,
  while undefined AUC is null.
- A fold with insufficient two-class inner calibration evidence has no
  calibrated OOS score. Its raw probabilities may still be used later to fit
  the production calibrator. Reasons and row counts are recorded. If there is
  no two-class purged OOF data at all, training fails before artifact writes.

The embargo is an extra calendar-day gap applied at both levels. The default is
one day: for validation beginning January 10, available training labels must
be strictly earlier than January 9. `--embargo-days 0` retains strict
label-before-validation purging without an extra gap. Negative values fail.

For option outcomes, `label_date` uses the latest exit date and any supplied
`executable_label_available_at_utc`, `label_available_at_utc`, or
`exit_quote_observed_at_utc` timestamp. Explicit timestamps must be timezone
aware and cannot precede the exit date. The training cutoff also applies to
this latest availability date. Missing/invalid entry or exit dates are skipped
and counted; exit-before-entry and malformed explicit availability fail closed.
Underlying labels retain their realized five-day-forward label date.

## Reading the model card

Newly generated cards use `model_card_schema_version: 3`:

- `cross_validation` reports the nested split policy, embargo, outer fold
  metrics, inner fit boundaries, and each outer fold's frozen threshold.
- `calibration.raw_brier`, `calibrated_brier`, log-loss, probability buckets,
  and balanced accuracy use the same outer-OOS rows. Segment reports use those
  outer probabilities and each row's own frozen fold threshold.
- `calibration.decision_threshold` remains the production artifact's threshold
  for compatibility and is labeled `decision_threshold_scope:
  production_fit_only`. It is not used to score the outer-OOS rows.
- `calibration.production_fit_diagnostics` describes a calibrator/threshold
  refitted using all available raw OOF predictions. These metrics reuse its
  fitting labels and are explicitly not OOS performance.
- Missing calibrated OOS evidence produces null metrics, zero `oof_rows`, and
  `evaluation_status: insufficient_inner_evidence`, rather than an optimistic
  score. The printed training classification report is also labeled not OOS.

The command-line flags and serialized inference fields remain compatible; the
additional `--embargo-days` flag controls the documented gap. The existing
trainer still downloads history and writes artifacts when explicitly run. This
change does not make the training command a read-only evaluation command.
Nested fitting performs more model fits than the previous pooled evaluator.

## Scope and limitations

The primary metrics are conditional on the requested/effective target and the
eligible labeled rows selected by the existing loader. They do not evaluate
option-target fallback selection, no-trade eligibility, target selection across
experiments, or the complete trading strategy. Side and hierarchical models
retain their separate legacy reports; this patch does not validate or change
their calibration/threshold-selection methodology. No performance improvement,
profitability, promotion eligibility, or sizing readiness is asserted.

## Offline regression checks

`engine/tests/test_scout_nested_calibration.py` uses synthetic observations.
It verifies label-mutation invariance for model/scaler state, calibrator state,
thresholds and predictions under isotonic, Platt and uncalibrated modes;
fold-local weights; both date/availability/embargo boundaries; delayed labels;
insufficient evidence; metric scopes; fold thresholds in segment reports;
option availability; and temporary-path CLI/card serialization with small real
LightGBM models. No network source or active model path is required.
