# DoltHub Options Backtest - April 16, 2026

## Executive Summary

I added a provider-agnostic DoltHub ingestion path and used it to build a local partitioned parquet options store from `post-no-preference/options`. The ingest succeeded and materially improved raw historical option-chain coverage versus the sparse local OptionsDX sample.

However, DoltHub alone does not currently validate Orographic's exact same-week Friday workflow. The dataset is broad, but the available expiries generally start at the following Friday rather than the Friday of the signal week. Under strict-real replay, this produced zero tradable candidates across 3, 6, and 12 months.

I then added an OnclickMedia adapter and blended its historical exit-date chains into the same provider-agnostic partition store. That materially changed the conclusion: DoltHub + OnclickMedia produced 100% strict-real entry and exit pricing for the target-DTE workflow and lifted the 12-month all-candidate sample from 5 trades to 1,841 trades.

The conclusion is nuanced:

- DoltHub is a good EOD options-chain source for provider-agnostic ingestion.
- DoltHub is not a drop-in replacement for current strict weekly same-Friday validation.
- It becomes useful if Orographic supports a 7-14 DTE expiry mode, or if we obtain a source with same-week weekly expiries across the universe.

## Code Changes

- Added `engine/backtest/dolthub_ingest.py`.
- Added `write_partitioned_frames(...)` to `engine/backtest/options_store.py` so non-CSV providers can write the same partition format.
- Added `--options-data-dir` to `engine/backtest/runner.py`.
- Added `--options-data-dir` to `engine/backtest/alpha_experiment.py`.
- Added DoltHub normalization coverage in `engine/tests/test_options_store.py`.
- Added `engine/data/options/` to `.gitignore` to avoid committing the generated parquet cache.
- Added `engine/backtest/onclick_ingest.py`.
- Added source-priority deduplication so OnclickMedia rows can override lower-priority DoltHub rows for the same exact contract.
- Added `build_manifest_from_partitions(...)` for blended stores built from existing provider partitions.

## DoltHub Ingest

Command:

```bash
python -m engine.backtest.dolthub_ingest \
  --months 12 \
  --end-date 2026-04-15 \
  --output-dir engine/data/options/dolthub \
  --workers 8 \
  --timeout 45 \
  --retries 2 \
  --clear \
  --force
```

Result:

| Metric | Value |
|---|---:|
| DoltHub requests | 10,300 |
| Successful requests | 10,300 |
| Failed requests | 0 |
| Row-limited requests | 0 |
| Option rows stored | 1,373,946 |
| Partitions | 9,041 |
| Symbols covered | 89 |
| Quote dates covered | 102 |
| Local cache size | 178 MB |

Store:

- `engine/data/options/dolthub/coverage_manifest.json`
- `engine/data/options/dolthub/partitioned/`

The generated store is intentionally ignored by git.

## Strict-Real Backtests

All runs used:

- `--strict-options-data`
- `--min-real-coverage-pct 1.0`
- `--options-data-dir engine/data/options/dolthub`
- End date: `2026-04-15`

### Alpha Experiment

| Window | Production Variant Trades | P&L | Net Return | Sharpe |
|---|---:|---:|---:|---:|
| 3 months | 0 | $0.00 | 0.0% | 0.00 |
| 6 months | 0 | $0.00 | 0.0% | 0.00 |
| 12 months | 0 | $0.00 | 0.0% | 0.00 |

Outputs:

- `output/alpha_experiment_results_2026-04-16_dolthub_strict_real_3mo.json`
- `output/alpha_experiment_results_2026-04-16_dolthub_strict_real_6mo.json`
- `output/alpha_experiment_results_2026-04-16_dolthub_strict_real_12mo.json`

### Backtest Runner

| Window | Trades | P&L | Net Return | Sharpe |
|---|---:|---:|---:|---:|
| 3 months | 0 | $0.00 | 0.0% | 0.00 |
| 6 months | 0 | $0.00 | 0.0% | 0.00 |
| 12 months | 0 | $0.00 | 0.0% | 0.00 |

Outputs:

- `output/backtest_results_2026-04-16_dolthub_strict_real_3mo.json`
- `output/backtest_results_2026-04-16_dolthub_strict_real_6mo.json`
- `output/backtest_results_2026-04-16_dolthub_strict_real_12mo.json`

## Coverage Audit

Output:

- `output/dolthub_coverage_audit_2026-04-16.json`

The critical finding is expiry compatibility:

| Metric | Value |
|---|---:|
| Mondays checked | 52 |
| Symbols checked | 100 |
| Monday/symbol partitions found | 4,523 |
| Same-week expiry partitions | 12 |
| Same-week expiry partition share | 0.2653% |
| Same-week rows | 584 |
| Positive bid/ask same-week rows | 527 |
| Symbols with same-week expiry | KLAC only |

Nearest expiry gap distribution:

| Nearest listed expiry vs target Friday | Partitions |
|---|---:|
| Same day | 12 |
| +6 days | 90 |
| +7 days | 4,150 |
| +10 days | 75 |
| +11 days | 8 |
| +12 days | 4 |
| +13 days | 76 |
| +14 days | 92 |
| +21 days | 12 |
| +28 days | 4 |

Example: on `2025-04-21`, Orographic targeted `2025-04-25` expiry, but DoltHub's first listed SPY expiries were `2025-05-02`, `2025-05-16`, and `2025-06-20`.

## Recommendation

Keep the DoltHub adapter. It is useful infrastructure and gives us a clean provider-agnostic path.

Do not treat DoltHub as a complete OptionsDX replacement for the current weekly same-Friday strategy. The raw coverage is strong, but the expiry coverage does not match Orographic's current contract-selection assumptions.

The best next step is to add an explicit expiry policy to replay and live Forge:

- `same_week`: current behavior, strict Friday of signal week.
- `next_listed_weekly`: first listed expiry after the target Friday, typically 7 DTE in DoltHub.
- `target_dte`: choose the nearest expiry in a configurable range, such as 7-14 DTE.

Then rerun the DoltHub strict-real backtest under `next_listed_weekly` or `target_dte=7..14`. That will answer whether the single-leg calls/puts model has real edge on the Dolt-compatible contract universe without pretending it is the same exact weekly workflow.

## Target-DTE Rerun

I added explicit replay expiry policy support and reran the strict-real DoltHub tests using:

```bash
--expiry-policy target_dte --target-dte-min 7 --target-dte-max 14
```

Additional code changes:

- `engine/backtest/replay.py` now supports `same_week`, `next_listed_weekly`, and `target_dte`.
- `engine/backtest/pricer.py` now returns `None` in strict-real mode if the exact exit contract is missing, rather than silently modeling the exit.
- `engine/backtest/runner.py` and `engine/backtest/alpha_experiment.py` expose the expiry policy through CLI flags.

### Target-DTE Strict-Real Alpha Experiment

Production-style variant: `council_cost_cap_symbol_priors`.

| Window | Trades | P&L | Net Return | Sharpe |
|---|---:|---:|---:|---:|
| 3 months | 0 | $0.00 | 0.0% | 0.00 |
| 6 months | 0 | $0.00 | 0.0% | 0.00 |
| 12 months | 0 | $0.00 | 0.0% | 0.00 |

Research all-candidate variant:

| Window | Trades | Win Rate | P&L | Net Return | Sharpe |
|---|---:|---:|---:|---:|---:|
| 3 months | 3 | 0.0% | -$96.00 | -35.2% | 0.00 |
| 6 months | 3 | 0.0% | -$96.00 | -35.2% | 0.00 |
| 12 months | 5 | 0.0% | -$187.00 | -28.8% | -27.68 |

Outputs:

- `output/alpha_experiment_results_2026-04-16_dolthub_target_dte_7_14_strict_real_3mo.json`
- `output/alpha_experiment_results_2026-04-16_dolthub_target_dte_7_14_strict_real_6mo.json`
- `output/alpha_experiment_results_2026-04-16_dolthub_target_dte_7_14_strict_real_12mo.json`

### Target-DTE Strict-Real Backtest Runner

| Window | Trades | Win Rate | P&L | Net Return | Real Entry | Real Exit |
|---|---:|---:|---:|---:|---:|---:|
| 3 months | 3 | 0.0% | -$96.00 | -35.2% | 100.0% | 100.0% |
| 6 months | 3 | 0.0% | -$96.00 | -35.2% | 100.0% | 100.0% |
| 12 months | 5 | 0.0% | -$187.00 | -28.8% | 100.0% | 100.0% |

The 12-month strict-real trades were:

| Entry | Symbol | Side | Strike | Expiry | Entry | Exit | P&L |
|---|---|---|---:|---|---:|---:|---:|
| 2025-08-04 | SCHW | call | 97.0 | 2025-08-15 | $1.57 | $1.32 | -$25.00 |
| 2025-08-04 | SCHW | call | 99.0 | 2025-08-15 | $0.73 | $0.51 | -$66.00 |
| 2026-03-16 | BAC | put | 47.0 | 2026-03-27 | $1.08 | $0.76 | -$32.00 |
| 2026-03-16 | BAC | put | 46.5 | 2026-03-27 | $0.89 | $0.58 | -$31.00 |
| 2026-03-16 | BAC | put | 46.0 | 2026-03-27 | $0.76 | $0.43 | -$33.00 |

Outputs:

- `output/backtest_results_2026-04-16_dolthub_target_dte_7_14_strict_real_3mo.json`
- `output/backtest_results_2026-04-16_dolthub_target_dte_7_14_strict_real_6mo.json`
- `output/backtest_results_2026-04-16_dolthub_target_dte_7_14_strict_real_12mo.json`

### Remaining Bottleneck

The target-DTE policy fixed the entry expiry mismatch but exposed a second DoltHub limitation: exact exit-contract availability.

12-month diagnostic:

| Stage | Count |
|---|---:|
| Target-DTE candidates | 4,960 |
| Unaffordable under current sizing | 2,991 |
| Affordable candidates | 1,969 |
| Missing exact exit contract | 1,848 |
| Missing exit chain | 116 |
| Fully strict-real priceable | 5 |

This means DoltHub is useful for entry-chain realism, but the current strict-real exit requirement leaves an extremely small sample. The result is too small to treat as an alpha verdict. It is, however, enough to say DoltHub still cannot replace a fuller historical options dataset for robust validation of this workflow.

Updated recommendation before adding OnclickMedia:

- Keep the expiry-policy implementation.
- Keep DoltHub ingestion as a provider-agnostic EOD research source.
- Do not use these DoltHub strict-real P&L numbers as strategy evidence; sample size is too small.
- Next highest-value improvement is either a richer historical options source with continuous expiry/strike availability at entry and exit, or a separate Dolt-compatible exit policy that explicitly accepts nearest available real mark and reports that as a less strict test.

## OnclickMedia Blended Rerun

I added an OnclickMedia no-key adapter that fetches historical chains and writes them to the same partitioned parquet schema as DoltHub and OptionsDX. For this run, I used OnclickMedia as an exit-date overlay against DoltHub-selected target-DTE contracts.

Command:

```bash
python -m engine.backtest.onclick_ingest \
  --months 12 \
  --end-date 2026-04-15 \
  --source-data-dir engine/data/options/dolthub \
  --output-dir engine/data/options/onclick \
  --expiry-policy target_dte \
  --target-dte-min 7 \
  --target-dte-max 14 \
  --exit-only \
  --workers 30 \
  --timeout 30 \
  --retries 2 \
  --force \
  --clear
```

OnclickMedia ingest result:

| Metric | Value |
|---|---:|
| Requests | 4,228 |
| Successful requests | 4,038 |
| Failed requests | 190 |
| Empty results | 0 |
| Option rows stored | 588,778 |
| Partitions | 4,040 |
| Symbols covered | 91 |
| Quote dates covered | 48 |

Blended store result:

| Metric | Value |
|---|---:|
| Sources | DoltHub + OnclickMedia |
| Option rows stored | 1,962,090 |
| Partitions | 9,063 |
| Symbols covered | 91 |
| Quote dates covered | 102 |
| Local cache size | 215 MB |

Store:

- `engine/data/options/onclick/coverage_manifest.json`
- `engine/data/options/blended/coverage_manifest.json`
- `engine/data/options/blended/partitioned/`

The generated stores are intentionally ignored by git.

### DoltHub vs Blended Strict-Real Runner

All runs used:

- `--strict-options-data`
- `--min-real-coverage-pct 1.0`
- `--options-data-dir engine/data/options/blended`
- `--expiry-policy target_dte --target-dte-min 7 --target-dte-max 14`
- End date: `2026-04-15`

| Window | Source | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown | Real Entry | Real Exit |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 3 months | DoltHub only | 3 | 0.0% | -$96.00 | -35.2% | 0.00 | -35.2% | 100.0% | 100.0% |
| 3 months | Blended | 302 | 38.4% | $2,571.00 | 4.8% | 1.20 | -72.0% | 100.0% | 100.0% |
| 6 months | DoltHub only | 3 | 0.0% | -$96.00 | -35.2% | 0.00 | -35.2% | 100.0% | 100.0% |
| 6 months | Blended | 1,037 | 38.9% | $5,329.00 | 2.5% | 0.74 | -82.6% | 100.0% | 100.0% |
| 12 months | DoltHub only | 5 | 0.0% | -$187.00 | -28.8% | -27.68 | -50.9% | 100.0% | 100.0% |
| 12 months | Blended | 1,841 | 47.4% | $70,750.00 | 18.3% | 3.38 | -82.6% | 100.0% | 100.0% |

Outputs:

- `output/backtest_results_2026-04-16_blended_target_dte_7_14_strict_real_3mo.json`
- `output/backtest_results_2026-04-16_blended_target_dte_7_14_strict_real_6mo.json`
- `output/backtest_results_2026-04-16_blended_target_dte_7_14_strict_real_12mo.json`

### Blended Strict-Real Alpha Experiment

Production-style variant: `council_cost_cap_symbol_priors`.

| Window | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 3 months | 8 | 62.5% | $141.00 | 8.9% | 2.74 | -27.1% |
| 6 months | 22 | 54.5% | -$11.00 | -0.2% | 0.93 | -91.6% |
| 12 months | 45 | 62.2% | $3,807.00 | 40.9% | 3.21 | -98.0% |

Research all-candidate variant:

| Window | Trades | Win Rate | P&L | Net Return | Sharpe | Max Drawdown |
|---|---:|---:|---:|---:|---:|---:|
| 3 months | 302 | 38.4% | $2,571.00 | 4.8% | 1.20 | -72.0% |
| 6 months | 1,037 | 38.9% | $5,329.00 | 2.5% | 0.74 | -82.6% |
| 12 months | 1,841 | 47.4% | $70,750.00 | 18.3% | 3.38 | -82.6% |

Outputs:

- `output/alpha_experiment_results_2026-04-16_blended_target_dte_7_14_strict_real_3mo.json`
- `output/alpha_experiment_results_2026-04-16_blended_target_dte_7_14_strict_real_6mo.json`
- `output/alpha_experiment_results_2026-04-16_blended_target_dte_7_14_strict_real_12mo.json`

## Updated Conclusion

OnclickMedia fills the exact exit-date quote gap well enough to make strict-real backtesting meaningful. This is a major improvement over DoltHub alone.

The results are encouraging but not yet production-proof:

- The all-candidate runner now has a statistically meaningful sample and positive 12-month P&L, but drawdowns remain severe.
- The production-style symbol-prior variant has a much smaller but usable sample: 45 trades over 12 months.
- The 6-month symbol-prior result is flat, which argues against overfitting to the strong 12-month headline.
- The adapter should remain provider-agnostic so OnclickMedia can be swapped or supplemented if its no-key endpoint changes behavior.

Recommended next step: keep the blended DoltHub + OnclickMedia path for research, but add execution-cost realism before using these numbers as a go-live signal. The highest-value checks are spread/slippage stress, liquidity filters using bid/ask width and open interest, and a per-symbol contribution cap so one cluster such as banks cannot dominate the apparent edge.
