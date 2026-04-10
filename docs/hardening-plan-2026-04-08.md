# Orographic Hardening Plan

Date: 2026-04-08

## Objective

Turn Orographic from a promising research prototype into a replayable, auditable weekly-options process with:

- real-options-data parity between live and backtest
- payoff-aligned model targets instead of stock-only direction labels
- deterministic event features that can be used in both live and historical runs
- a stricter walk-forward evaluation pack that makes overfitting and simulation drift obvious

## Current Reality

The current stack has real strengths:

- clean separation between Scout, Forge, Council, and replay
- a sensible short-horizon feature set for direction
- useful live diagnostics in Forge

The current weak points are concentrated in four areas:

1. Backtests can price trades on synthetic Black-Scholes chains when real historical options data is missing.
2. Scout is trained on `positive 5-day stock return`, which is not the same thing as `option structure beats breakeven`.
3. Live scoring includes event-sensitive overlays like Sentinel, but replay does not have historical event parity.
4. The walk-forward pack is informative, but still too permissive and too small-sample to be treated as capital-allocation evidence.

## Success Criteria

Orographic is not "hardened" until all of the following are true:

- default research runs use real historical option chains or fail closed with explicit coverage gaps
- every backtest trade records whether entry and exit were priced from real chain data or fallback logic
- Scout and/or downstream ranking is trained against option-payoff-aware targets
- all live-only overlays have replayable historical equivalents or are disabled in research mode
- the evaluation pack reports regime, event, and data-coverage slices by default
- the main walk-forward result is stable across multiple windows and not dominated by a few outlier trades

## Workstream 1: Real Options Data Parity

### Goal

Eliminate hidden simulation optimism from the backtest path.

### Scope

Primary files:

- `engine/backtest/options_provider.py`
- `engine/backtest/replay.py`
- `engine/backtest/pricer.py`
- `engine/backtest/results.py`
- `engine/backtest/runner.py`
- `engine/backtest/alpha_experiment.py`

### Changes

1. Add explicit pricing source metadata.
   Fields to attach to each replayed candidate and priced trade:
   - `entry_data_source`: `real_chain`, `synthetic_chain`, `hybrid`
   - `exit_data_source`: `real_chain`, `synthetic_chain`, `hybrid`
   - `entry_quote_type`: `bid`, `ask`, `mid`, `modeled`
   - `exit_quote_type`: `bid`, `ask`, `mid`, `modeled`
   - `options_data_coverage_pct`

2. Split "strict" and "research fallback" modes.
   - Strict mode: fail or skip when the real chain is missing.
   - Research fallback mode: allow synthetic chains, but label every trade and every summary metric clearly.

3. Add a coverage gate to the runners.
   Default expectation for production-style research:
   - at least `90%` of selected trades priced from real chain data at entry
   - at least `90%` priced from real chain data at exit
   - otherwise the run is marked `coverage_failed`

4. Upgrade options ingestion format.
   Replace the current "scan all CSVs" loader with a partitioned local store keyed by:
   - `quote_date`
   - `underlying_symbol`
   - `expire_date`
   - `option_type`

   Preferred storage:
   - Parquet partitions under `engine/data/optionsdx/`
   - a small manifest file summarizing date coverage by symbol

5. Add replay/live parity snapshots.
   For any given Monday, persist:
   - top scout names
   - Forge rejection waterfall
   - selected contracts
   - pricing source mix

### Acceptance Criteria

- A strict replay on a window with inadequate chain coverage fails loudly instead of silently synthesizing trades.
- Results JSON includes coverage and pricing-source breakdowns.
- The dashboard and docs distinguish `strict_real_data` runs from `fallback_research` runs.
- We can answer "what percent of P&L came from synthetic pricing?" from one artifact.

### First Implementation Slice

1. Extend `TradeLeg` in `engine/backtest/pricer.py`.
2. Extend JSON output in `engine/backtest/results.py`.
3. Add `strict_options_data` and `min_real_coverage_pct` flags to the runners.
4. Ship a coverage summary before changing storage layout.

## Workstream 2: Payoff-Aligned Labels

### Goal

Train the model on outcomes that matter to the actual options trade, not just the underlying stock direction.

### Scope

Primary files:

- `engine/train_scout_model.py`
- `engine/orographic/scout.py`
- `engine/orographic/forge.py`
- `engine/orographic/schemas.py`
- new training and evaluation modules under `engine/backtest/` or `engine/orographic/models/`

### Current Problem

Scout currently learns whether the stock's 5-day forward return is positive. That target ignores:

- option premium paid
- breakeven distance
- IV expansion or crush
- whether a debit spread or long option was chosen
- whether the move occurred early enough to be monetized

### Target Redesign

Keep one simple directional model if useful, but add contract-aware targets:

1. `prob_hit_breakeven`
   - probability the chosen structure exceeds breakeven by Friday close

2. `expected_option_return_pct`
   - expected percentage return on the selected structure

3. `prob_positive_option_pnl`
   - probability the selected structure finishes above zero P&L

4. `prob_hit_1r_before_expiry`
   - probability the structure reaches a `+1R` target before expiry

5. Optional:
   - separate models by contract policy
   - naked-long policy
   - debit-spread policy

### Modeling Plan

1. Keep Scout focused on symbol-level ranking.
   - target: `prob_underlying_move_exceeds_threshold`
   - threshold should be side-aware and event-aware, not just `> 0`

2. Add a second-stage model for contract viability.
   - inputs:
     - Scout features
     - contract features from Forge
     - event features
     - option-surface features
   - outputs:
     - `prob_positive_option_pnl`
     - `expected_option_return_pct`

3. Rank final candidates using payoff-aware expectations.
   Example:
   - `final_score = 0.35 * scout_edge + 0.40 * option_success_prob + 0.25 * expected_option_return`

### Label Construction

Use replayed historical structures to create labels from realized option outcomes:

- entry quote from real historical ask when available
- exit quote from real historical bid when available
- strict coverage filter for training labels
- do not train payoff models on synthetic-labeled trades unless explicitly marked as lower-trust

### Acceptance Criteria

- New training artifact logs both directional and payoff metrics.
- Calibration charts show whether predicted probabilities are honest.
- Final ranking improves `prob_positive_option_pnl` and drawdown versus the current stock-direction-only setup.
- The model card explicitly states which labels used real versus fallback pricing.

### First Implementation Slice

1. Add a new dataset builder that emits replayed contract outcomes.
2. Train a simple baseline `prob_positive_option_pnl` model.
3. Compare it against the current Scout score inside walk-forward evaluation before replacing live ranking.

## Workstream 3: Event Features

### Goal

Capture the blind spots most likely to matter for weekly options.

### Scope

Primary files:

- `engine/orographic/scout.py`
- `engine/train_scout_model.py`
- `engine/orographic/sentinel.py`
- `engine/orographic/schemas.py`
- new event loaders under `engine/orographic/` or `engine/data/`

### Feature Families

1. Corporate event features
   - `days_to_earnings`
   - `is_earnings_week`
   - `days_since_earnings`
   - `dividend_within_7d`
   - `split_or_guidance_flag` if available

2. Macro event features
   - `days_to_fomc`
   - `days_to_cpi`
   - `days_to_nfp`
   - `macro_event_density_5d`
   - `risk_event_week_flag`

3. Option-surface features
   - front-week IV percentile
   - IV term slope: front week versus next expiry
   - call/put skew around selected delta
   - open-interest concentration near strike
   - gamma cluster distance

4. Tape and path features
   - overnight gap share of the prior 5-day move
   - intraday reversal score
   - realized gap volatility
   - percent of move driven by 1-day impulse

5. Sector and cross-asset features
   - sector ETF relative strength
   - sector ETF realized vol
   - rates proxy
   - credit-spread proxy
   - dollar trend proxy

### Replayability Rules

Every event feature added to live must meet one of these conditions:

1. Fully replayable from timestamped historical data.
2. Disabled in research mode.
3. Logged as an exogenous overlay and excluded from headline backtest metrics.

That rule is especially important for Sentinel. If headline intelligence remains in live scoring, we need one of:

- a historical headline archive with timestamped sentiment outputs
- a frozen daily event cache persisted at scan time
- or Sentinel excluded from research and treated as discretionary overlay

### Acceptance Criteria

- Every feature has a deterministic timestamped source.
- Feature generation works both in live scans and historical replay.
- The training report includes feature-importance drift by month and regime.
- No live-only feature silently affects ranking without historical parity.

### First Implementation Slice

1. Add earnings and macro calendar features first.
2. Persist them into both training rows and live scan diagnostics.
3. Treat Sentinel as `live_overlay_only` until a replayable archive exists.

## Workstream 4: Stricter Walk-Forward Evaluation Pack

### Goal

Make the research process harder to fool.

### Scope

Primary files:

- `engine/backtest/alpha_experiment.py`
- `engine/backtest/results.py`
- `engine/backtest/replay.py`
- new evaluation modules and report templates under `docs/` and `engine/backtest/`

### Evaluation Upgrades

1. Multiple windows, not one headline window.
   Run at minimum:
   - trailing 3 months
   - trailing 6 months
   - trailing 12 months when data exists
   - rolling quarterly windows

2. Purged walk-forward structure.
   - train only on data available before the decision date
   - include embargo around overlapping label windows
   - no using future symbol priors or event labels

3. Regime and event slices.
   Report results by:
   - `risk_on`
   - `neutral`
   - `risk_off`
   - `extreme_vol`
   - earnings-week versus non-earnings-week
   - macro-event-week versus normal-week

4. Concentration diagnostics.
   Add:
   - top-3 trade share of total P&L
   - top-symbol share of total P&L
   - median and 90th percentile holding cost
   - worthless-expiry rate
   - full-loss rate

5. Calibration and ranking diagnostics.
   Add:
   - predicted-probability buckets
   - realized win rate by decile
   - Spearman IC by month
   - Brier score for success probabilities

6. Feasibility diagnostics.
   Add:
   - signals generated
   - candidates built
   - candidates passing cost cap
   - live-board selections
   - priced trades
   - real-data coverage

7. Friction diagnostics.
   Run sensitivity packs for:
   - wider entry spread assumption
   - wider exit spread assumption
   - no Friday fill on stale quotes
   - hard skip of trades with inadequate liquidity

### Acceptance Criteria

- A single Markdown and JSON bundle explains performance, calibration, feasibility, and data coverage.
- No strategy variant is promoted based on fewer than a defined minimum trade count.
- Any result dominated by a few outliers is explicitly flagged.
- Research sign-off requires passing both headline return and robustness checks.

### Promotion Gate

A variant only graduates to paper deployment if it clears all of:

- minimum `75` trades in strict real-data walk-forward tests
- profit factor `> 1.20`
- max drawdown materially below current baseline
- no single symbol contributing more than `20%` of total P&L
- top-3 trades contributing less than `35%` of total P&L
- stable positive performance across at least two regime buckets

## Workstream 5: Operating Discipline

### Goal

Make the system easier to trust day to day.

### Scope

Primary files:

- `engine/run_scan.py`
- `engine/tests/*`
- CI workflow files
- `docs/`

### Changes

1. Fix root-level execution reliability.
   - documented commands must run from the repo root
   - `engine/run_scan.py` should not require manual `PYTHONPATH`

2. Add CI checks for:
   - root-level scan smoke test
   - strict replay smoke test
   - model artifact presence or fallback behavior
   - coverage-metadata integrity

3. Version research artifacts.
   Every JSON report should include:
   - git commit
   - model hash
   - feature list
   - pricing mode
   - options data coverage summary

## Sequencing

### Phase 1: Make Backtests Honest

Target: 3 to 5 days

- add pricing-source metadata
- add strict/fallback options-data modes
- add coverage summaries to result artifacts
- fix root-level runner reliability

Output:

- honest baseline replay with coverage tags
- no silent synthetic pricing in strict mode

### Phase 2: Add Replayable Event Features

Target: 4 to 6 days

- add earnings and macro calendar loaders
- inject event features into Scout training and live diagnostics
- mark Sentinel as live-only until replayable

Output:

- deterministic event-aware training rows
- event slices in research reports

### Phase 3: Build Payoff-Aware Labels

Target: 5 to 8 days

- build replayed contract-outcome dataset
- train first option-success model
- compare against existing Scout-only ranking

Output:

- baseline contract-success model
- comparison report versus current ranking path

### Phase 4: Tighten the Evaluation Pack

Target: 3 to 5 days

- add calibration, concentration, regime, and friction diagnostics
- define promotion gates
- publish a standard research report template

Output:

- one report format used for every strategy decision

## Immediate Next Tasks

If we start now, the first five coding tasks should be:

1. Fix [engine/run_scan.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/run_scan.py) so the documented repo-root command works without `PYTHONPATH`.
2. Extend [engine/backtest/pricer.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/pricer.py) and [engine/backtest/results.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/results.py) with pricing-source metadata.
3. Add `strict_options_data` and coverage thresholds to [engine/backtest/runner.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/runner.py) and [engine/backtest/alpha_experiment.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/alpha_experiment.py).
4. Build a minimal event store for earnings and macro dates.
5. Add a new replay dataset builder for `prob_positive_option_pnl`.

## Go/No-Go Standard

Do not trust Sharpe improvements alone.

Trust the upgraded system only when:

- the edge survives strict real-data replay
- payoff-aware labels outperform stock-direction labels
- event-aware features improve calibration, not just headline return
- walk-forward robustness is stable across multiple windows and regimes
- live and research paths use the same ranking logic and the same feature families
