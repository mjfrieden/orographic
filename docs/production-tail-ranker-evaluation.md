# Tail-ranker research evaluation

`scripts/train_production_tail_ranker.py` trains a research candidate using a
development dataset and a separate later forward dataset. Its default outputs
are under `output/research/production_tail_ranker/`; running it no longer
overwrites the active ranker or its card by default. Explicit `--output-model`
and `--output-card` paths remain supported. Promotion of a candidate is a
separate review decision.

```bash
python scripts/train_production_tail_ranker.py \
  --development path/to/development.json \
  --forward path/to/later-forward.json
```

## Evaluation boundaries

- Every out-of-fold probability and clipped return-bucket mean is estimated
  from that fold's purged training rows. The fixed, predeclared tail gate is
  applied to those fold-specific utilities. Full-development payoff means are
  used only by the final model and the later forward evaluation.
- A missing training class has zero aligned probability and zero utility.
  Future outcomes are never used to fill a missing class.
- Training labels must be available strictly before validation dates. When
  present, executable-label availability, generic label availability, and exit
  quote capture timestamps extend the date-only exit boundary. This is
  conservative at day granularity; same-day availability is not sufficient.
- Development and forward contract/entry identities and available
  recommendation IDs must be disjoint. All development labels must be available
  before the first forward entry date. Forward scan/decision timestamps must
  match their entry dates and specify a timezone.
- Required outcomes, prices, liquidity fields, computed features, probabilities,
  and utilities must be finite. Optional absent features retain the existing
  feature-builder defaults. Invalid inputs or no usable purged folds stop the
  run before fitting or writing a candidate.
- The research card records the actual source paths and SHA-256 hashes,
  availability boundary, fold-specific bucket means/counts, and fixed gate policy.

## What this change does not establish

Existing model files and historical result cards are unchanged. Historical
development policy metrics used full-development bucket means and must be
recomputed before relying on them as out-of-fold policy evidence. Correcting
this trainer does not retroactively validate those results.

The gate constants are not retuned here. Chronological/disjoint files and a
fixed gate do not prove that the forward window was never inspected during
earlier research. Nested selection/calibration and a genuinely untouched
prospective window remain separate requirements for stronger performance
claims. This change grants no new live-trading or sizing authority.

## Offline regressions

```bash
python -m pytest -q engine/tests/test_train_production_tail_ranker.py
python -m pytest -q engine/tests
```

The focused tests include a same-class future-payoff mutation that must not
change earlier OOF selections, missing classes, delayed label availability,
source overlap, nonfinite values, frozen forward inputs, and research-default
and explicit-output CLI runs using synthetic data.
