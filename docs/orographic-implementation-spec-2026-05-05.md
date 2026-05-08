# Orographic Implementation Spec

Date: 2026-05-05

Purpose: convert the consultant roadmap into a repo-ready execution plan with concrete tickets, file-level changes, and acceptance tests. This spec also marks the first implementation slice now landed in the repo: Sprint 1 observability and research-ledger foundations.

## Delivery strategy

The work is intentionally sequenced so each sprint leaves behind:

- a stable data contract
- a testable acceptance boundary
- enough observability to judge whether the next layer deserves promotion

## Sprint 1: Observability foundation

### Goal

Make every run explainable and replayable at the metadata level:

- which artifacts were active
- which modes were active or shadow
- what the board looked like
- what was vetoed or held out
- why Council abstained or selected

### Tickets

#### `SPR1-1` Snapshot contract expansion

Add canonical scan settings and model-mode metadata to the main snapshot and downstream diagnostics.

Files:

- [engine/orographic/pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/pipeline.py)
- [engine/run_scan.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/run_scan.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

Changes:

- emit `scan_settings` on every snapshot
- emit `model_modes` on every snapshot
- propagate both into attribution/waterfall artifacts
- keep `model_artifacts` on every snapshot and artifact

Acceptance tests:

- snapshot contains `scan_settings.live_size`, `shadow_size`, `forge_intake`, and `universe_size`
- snapshot contains normalized `model_modes` for directional Scout, side-aware Scout, Sentinel, and payoff ranker
- attribution artifact and Forge waterfall artifact retain the same metadata

#### `SPR1-2` Canonical research ledger

Create a per-run research ledger that records live board, shadow board, vetoes, holdouts, rejection counts, model hashes, and execution settings.

Files:

- [engine/orographic/pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/pipeline.py)
- [engine/run_scan.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/run_scan.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

Changes:

- add `build_research_run_ledger_entry`
- add `append_research_run_ledger`
- wire default output to `web/data/diagnostics/research_run_ledger.json`
- allow CLI override and disable flags

Acceptance tests:

- appending a run creates `artifact = research_run_ledger`
- aggregate counts track runs, abstains, live picks, shadow picks, friction vetoes, holdouts, and pre-Forge rejections
- each entry stores `scan_settings`, `model_modes`, `model_artifacts`, `live_board`, `shadow_board`, `vetoed_candidates`, and `council_holdouts`

#### `SPR1-3` Regression guards for metadata truthfulness

Strengthen tests so silent metadata regressions fail fast.

Files:

- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

Changes:

- extend run-scan truthfulness tests to assert metadata presence
- add ledger tests for canonical research entries

Acceptance tests:

- run-scan fixture verifies counts remain truthful and metadata exists
- research ledger fixture verifies aggregate and entry contract

## Sprint 2: Option-native labels and targets

### Goal

Make the learning target answer “was this option trade good after friction?” rather than mainly “was the underlying up in five days?”

### Tickets

#### `SPR2-1` Unified option-outcome dataset builder

Files:

- [engine/backtest/results.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/results.py)
- [engine/backtest/runner.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/runner.py)
- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)

Changes:

- add dataset export with per-trade labels:
  - `positive_pnl_after_friction`
  - `breakeven_after_friction`
  - `hold_period_return_after_friction`
  - `max_favorable_excursion`
  - `adverse_excursion`
- track exact quote-mark coverage and side coverage in export

Acceptance tests:

- dataset rows contain the new labels
- friction-adjusted labels differ from raw labels when spread/slippage is nonzero

#### `SPR2-2` Reframe Scout as directional prior

Files:

- [engine/train_scout_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_scout_model.py)
- [engine/orographic/scout.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py)
- [engine/orographic/models/scout_model_card.json](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/models/scout_model_card.json)

Changes:

- preserve current directional target
- expose it explicitly as a directional prior
- add clearer model-card warnings when option-native targets are not primary

Acceptance tests:

- model card identifies active target and whether it is option-native
- snapshot records directional Scout mode truthfully

#### `SPR2-3` Option-native payoff evaluation first

Files:

- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)
- [engine/orographic/models/payoff_model_card.json](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/models/payoff_model_card.json)

Changes:

- reorder reporting so friction-adjusted option metrics are primary
- segment all metrics by side and by regime
- add minimum put-sample warnings

Acceptance tests:

- model card reports option-native metrics first
- side segments are always present even when weak

## Sprint 3: Sentinel v2 structured event forecasts

### Goal

Replace the headline multiplier with a structured event forecast that can be matched against holding window and contract structure.

### Tickets

#### `SPR3-1` Structured Sentinel response contract

Files:

- [functions/api/ai/sentinel.js](/Users/mjfrieden/Desktop/2026/Orographic/functions/api/ai/sentinel.js)
- [engine/orographic/sentinel.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/sentinel.py)
- [engine/tests/test_scout.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_scout.py)

Changes:

- return:
  - `direction_1d`
  - `direction_3d`
  - `direction_5d`
  - `magnitude_bucket`
  - `decay_half_life`
  - `spot_vs_iv_effect`
  - `call_relevance`
  - `put_relevance`
  - `no_trade_relevance`
  - `confidence`

Acceptance tests:

- engine handles structured Sentinel payloads
- fallback stays neutral when fields are absent

#### `SPR3-2` Shadow-only Scout/Forge integration

Files:

- [engine/orographic/scout.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py)
- [engine/orographic/forge.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

Changes:

- feed Sentinel v2 features into Scout shadow diagnostics
- feed holding-window compatibility features into Forge shadow diagnostics

Acceptance tests:

- snapshot captures event-horizon fields
- shadow diagnostics log incompatibility between catalyst timing and DTE

## Sprint 4: Friction- and turnover-aware board utility

### Goal

Stop optimizing only for fresh candidate rank; optimize for net action quality after spreads, slippage, and board churn.

### Tickets

#### `SPR4-1` Council turnover penalties

Files:

- [engine/orographic/council.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/council.py)
- [engine/tests/test_council.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_council.py)

Changes:

- compare current board candidate set with prior board
- penalize switch cost and churn
- emit board-change rationale

Acceptance tests:

- unchanged board wins when replacement uplift is below friction threshold
- board-change rationale is emitted in summary notes

#### `SPR4-2` Final utility reranker

Files:

- [engine/orographic/payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/payoff_model.py)
- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)

Changes:

- train/score on utility after friction
- include stability penalties and board-action framing

Acceptance tests:

- reranker score drops for churn-heavy replacements with small raw edge

## Sprint 5: Feature expansion and simple ensembles

### Goal

Increase option-native feature quality before increasing model complexity.

### Tickets

#### `SPR5-1` Option-native feature expansion

Files:

- [engine/orographic/payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/payoff_model.py)
- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)

Changes:

- add short-horizon option momentum
- add realized spread behavior
- add recent contract-path behavior
- add symbol-level option regime features

Acceptance tests:

- new features appear in model artifact feature list
- training succeeds when some path features are missing

#### `SPR5-2` Baseline and ensemble ladder

Files:

- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)
- [engine/tests/test_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_payoff_model.py)

Changes:

- train regularized linear baseline
- train boosted-tree baseline
- keep current boosted model
- optional stacked ensemble only if strictly superior out of sample

Acceptance tests:

- report compares baseline families under identical walk-forward windows
- ensemble is not activated automatically without out-of-sample uplift

## Sprint 6: Sequence model shadow path

### Goal

If simpler models plateau, add a shadow-only path model for weekly option trajectory quality.

### Tickets

#### `SPR6-1` Path model shadow integration

Files:

- `engine/orographic/path_model.py` (new)
- [engine/orographic/forge.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

Changes:

- predict path-sensitive outcomes:
  - early profit-taking likelihood
  - expected MFE before expiry
  - decay-risk likelihood
- keep shadow-only until disagreement evidence exists

Acceptance tests:

- Forge consumes path-model outputs without affecting live board by default
- shadow diagnostics compare path-model disagreement cohorts

## Current implementation status

Implemented in this pass:

- `SPR1-1` snapshot contract expansion
- `SPR1-2` canonical research ledger
- part of `SPR1-3` regression guards

Still pending after this pass:

- remaining Sprint 1 polish if dashboard/UI consumption is desired
- all Sprints 2 through 6
