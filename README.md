# Orographic

Orographic is a new short-term options platform built from the useful parts of the prior weather systems, but with a much cleaner contract between signal generation, contract selection, portfolio construction, and presentation.

It is split into three first-party layers:

- `Scout`: a Cirrus-style symbol and direction engine. It decides whether a name has enough short-term edge to even deserve option-chain work.
- `Forge`: a Cumulus-style contract engine. It chooses the actual weekly contract and scores quote quality, breakeven burden, payoff shape, and shadow-mode learned payoff rank.
- `Council`: a Stratus-style portfolio gate. It selects the live board, keeps a model-observation shadow board, and enforces side, sector, correlation, sizing, and no-trade discipline.

The game layer lives in `web/`. It is designed to deploy cleanly to Cloudflare Pages as a static site.

The protected access layer lives in `functions/` and runs as Cloudflare Pages Functions. It signs a short-lived session cookie and validates users from environment secrets, so the public repository never needs to store login credentials.

The current game loop also uses Pages Functions as a thin Tradier proxy. The browser never sees the Tradier token directly. Order previews and submissions stay server-side.

## Why this version is different

- No synthetic bid/ask fallback in the scan engine.
- One canonical snapshot schema from Scout to Forge to Council.
- Hard abstain support instead of forcing a pick.
- Live and shadow lanes are first-class from day one.
- New ML/AI features run in shadow mode by default until explicitly promoted.
- Scout snapshots include side-aware `call_edge`, `put_edge`, and `no_trade` probabilities.
- Sentinel extracts structured event features from headlines instead of acting as a raw sentiment oracle.
- Deployment path is intentionally cheap:
  - static game board on Cloudflare Pages
  - scheduled scan by GitHub Actions or a self-hosted runner
  - optional Worker later for lightweight API glue, not heavy scanning

## Local run

Create a venv and install dependencies:

```bash
cd /Users/mjfrieden/Desktop/2026/Orographic
python3 -m venv .venv
./.venv/bin/pip install -r engine/requirements.txt
```

Run a fresh scan:

```bash
./.venv/bin/python engine/run_scan.py --output web/data/latest_run.json
```

Live scan contract selection defaults to the recovered production DTE window:
`--minimum-days-to-expiry 7 --maximum-days-to-expiry 14`. Override those only
for explicit research runs, not routine production scans.

The pre-Council friction gate is observational in production scans so Council
sees the same candidate surface used by the recovered walk-forward validation.
Use `--enforce-pre-council-friction-gate` only for explicit research runs.

Live scans send 12 Scout signals into Forge by default. This keeps the live
candidate surface closer to walk-forward replay and reduces empty-board risk
from stopping after the first handful of high-extrinsic symbols.

Each scan also writes a Forge bottleneck artifact beside the snapshot:

- `web/data/diagnostics/forge_rejection_waterfall_latest.json`
- `web/data/diagnostics/forge_rejection_waterfall_YYYY-MM-DD.json`

Each snapshot also includes `promotion_readiness`, a shadow-mode governance report for Side-Aware Scout, Sentinel, the active payoff ranker, and Council risk intelligence. It records current shadow observations, pending acceptance gates, and the staged path from `shadow` to `tie_breaker`, `small_weight`, `limited_active`, and `active`. The dashboard renders this as the Promotion Readiness panel.

Each scan also appends a side-aware Scout shadow disagreement ledger beside the diagnostics:

- `web/data/diagnostics/side_aware_scout_shadow_ledger.json`

This ledger records where the shadow side-aware Scout preferred call, put, or no-trade differently from active Scout, plus whether the symbol reached Forge, live board, or shadow board.

Each scan also appends a rolling board history ledger beside the diagnostics:

- `web/data/diagnostics/board_recommendation_history.json`

This ledger records each run's live board, shadow board, regime, and board counts so recommendations can be tracked over time instead of being overwritten by the newest snapshot.

Each scan also appends a dedicated moonshot prospective ledger:

- `web/data/diagnostics/moonshot_prospective_ledger.json`

This ledger records moonshot picks and near-miss shadow candidates with their tail-upside score, eligibility reasons, emission quote, model context, risk features, and fixed outcome slots. It is intentionally separate from the all-Forge-candidate prospective ledger so moonshot research can be evaluated as its own lane.

Archive live option chains for future model training:

```bash
./.venv/bin/python scripts/archive_live_option_chains.py \
  --snapshot web/data/latest_run.json \
  --snapshot-symbols-only \
  --output-dir engine/data/live_options_archive
```

The archive writes partitioned parquet chains plus a manifest under `engine/data/live_options_archive/`. Use `--snapshot-symbols-only` for a lighter scheduled capture, or omit it to archive the full configured universe.

Build canonical research datasets from prospective ledgers:

```bash
./.venv/bin/python scripts/build_research_datasets.py \
  --prospective-ledger web/data/diagnostics/prospective_pick_ledger.json \
  --moonshot-ledger web/data/diagnostics/moonshot_prospective_ledger.json \
  --output-dir output/research_datasets
```

This produces option-recommendation and moonshot outcome tables suitable for future payoff, path, side-aware, and moonshot model training.

Audit research data capture after archiving and dataset generation:

```bash
./.venv/bin/python scripts/audit_research_data_capture.py \
  --live-archive-manifest engine/data/live_options_archive/coverage_manifest.json \
  --research-dataset-dir output/research_datasets
```

The scheduled workflow runs this audit and fails if the live chain archive is empty, required ledgers are missing, or the generated datasets are internally inconsistent.

For durable storage beyond GitHub's short-lived workflow artifacts, configure `OROGRAPHIC_RESEARCH_R2_BUCKET` as a GitHub secret alongside the Cloudflare account/token secrets. When present, the scheduled workflow uploads `engine/data/live_options_archive/` and `output/research_datasets/` to Cloudflare R2.

Optionally capture standing-position value on each run into a private local file:

```bash
./.venv/bin/python engine/run_scan.py \
  --output web/data/latest_run.json \
  --positions-log-output .local/position_history.json
```

Use a non-public path such as `.local/position_history.json`. Do not point position history at a git-tracked file or anything under `web/`.

Build the local historical options store and coverage manifest:

```bash
./.venv/bin/python -m engine.backtest.options_store --data-dir engine/data/optionsdx --force
```

The store builder accepts raw `.csv`, `.csv.gz`, `.gz`, and `.zip` archives dropped into `engine/data/optionsdx`.

Build the optional canonical daily event-feature store used by Scout and Sentinel shadow diagnostics:

```bash
./.venv/bin/python scripts/build_event_features.py \
  --fnspid-input /path/to/fnspid_news.csv \
  --edt-input /path/to/edt_events.jsonl \
  --mirai-input /path/to/mirai_events.csv \
  --stockemotions-input /path/to/stockemotions.csv
```

This writes `engine/data/event_features/daily_event_features.parquet` by default. Override the path with `OROGRAPHIC_EVENT_FEATURES_PATH` or `engine/train_scout_model.py --event-features-path`.

SEC filing inputs are now curated before Scout training: raw filing counts stay in the canonical store for diagnostics, but the model prefers higher-signal SEC features such as `8-K`, `10-Q`, `10-K`, proxy, and targeted offering flags plus compact rolling aggregates instead of training directly on the noisiest insider and ownership traffic. Broad capital-markets traffic like `424B2` and `FWP` is retained for diagnostics, but kept out of the Scout signal slice.

To build an overlapping MIRAI/GDELT-style macro overlay for a real backtest window, fetch raw GDELT article rows first and then feed that CSV into `--mirai-input`:

```bash
./.venv/bin/python scripts/fetch_gdelt_macro.py \
  --start-date 2026-01-05 \
  --end-date 2026-04-13 \
  --mondays-only \
  --max-records-per-day 10 \
  --continue-on-error \
  --output .local/raw_event_features/gdelt_macro_mondays_2026q1_q2.csv

PYTHONPATH=. ./.venv/bin/python scripts/build_event_features.py \
  --mirai-input .local/raw_event_features/gdelt_macro_mondays_2026q1_q2.csv \
  --output .local/event_features/daily_event_features_with_macro_overlap.parquet \
  --replace
```

Run a strict replay that only accepts real historical option-chain data:

```bash
./.venv/bin/python -m engine.backtest.runner \
  --months 3 \
  --base-budget-usd 300 \
  --hard-cost-ceiling-usd 600 \
  --strict-options-data \
  --min-real-coverage-pct 0.9
```

The replay now writes a canonical friction-aware option outcome dataset automatically:

- default: `output/option_outcomes_latest.json`
- override with `--option-outcome-output /your/path.json` when needed

Those canonical rows now carry explicit replay regime fields as well, including `regime_mode`, `regime_bias`, and `regime_source_symbol`, so downstream training and evaluation can segment true `risk_on` / `risk_off` / `neutral` regimes end to end.

If you already have an older `backtest_results.json` artifact and do not want to rerun replay, convert it with:

```bash
./.venv/bin/python scripts/convert_backtest_to_option_outcomes.py \
  --input output/backtest_results_2026-04-17_blended_target_dte_7_14_strict_real_execution_stress_12mo.json
```

Run the walk-forward alpha experiment with the same sizing policy:

```bash
./.venv/bin/python -m engine.backtest.alpha_experiment --months 12 --base-budget-usd 300 --hard-cost-ceiling-usd 600 --cost-cap-usd 600 --strict-options-data --min-real-coverage-pct 0.9
```

The alpha experiment now also writes one canonical `option_outcome_dataset` artifact per variant into `output/` by default. Override the directory with `--option-outcome-dir`.

Current production decision as of May 6, 2026:

- keep `council_cost_cap` as the production default validation and deployment variant
- keep the conservative path-aware tie-breaker as research-only
- treat the loose path-aware tie-breaker as rejected

The May 6, 2026 comparison note lives in [model-deployment-decision-2026-05-06.md](/Users/mjfrieden/Desktop/2026/Orographic/docs/model-deployment-decision-2026-05-06.md).

Preview the game board:

```bash
npx wrangler pages dev web
```

Then open the local Pages URL shown by Wrangler.

Controls on the main page:

- `WASD` or arrow keys: move the cutter
- `E`: tractor the nearest signal into the command deck
- `Space`: start or resume a run
- `F`: toggle fullscreen

## Repo layout

- `engine/`: Python scan pipeline and tests
- `web/`: static game board for Cloudflare Pages
- `functions/`: Pages Functions for login, logout, session lookup, and admin gating
- `docs/`: architecture and deployment notes
- `.github/workflows/`: scheduled scan workflow
- `scripts/hash_auth_users.py`: helper to hash viewer/admin accounts for the `OROGRAPHIC_AUTH_USERS_JSON` secret

## Auth secrets

Orographic expects two Cloudflare Pages secrets:

- `OROGRAPHIC_SESSION_SECRET`: random signing secret for the session cookie
- `OROGRAPHIC_AUTH_USERS_JSON`: JSON array of hashed users with `username`, `role`, `salt`, `hash`, and `iterations`

Keep both in local ignored files or Cloudflare secrets only. Do not commit them to the public repository.

Tradier integration expects these additional Pages secrets or local `.dev.vars` entries:

- `TRADIER_ACCESS_TOKEN`: your Tradier API token
- `TRADIER_ACCOUNT_ID`: the brokerage account id
- `TRADIER_SANDBOX_MODE`: `true` for paper trading, `false` for production base URLs
- `TRADIER_LIVE_TRADING_ENABLED`: `true` only when you explicitly want production order submission enabled
- `TRADIER_MAX_CONTRACTS`: hard cap for this arena's order quantity control, default `3`
- `OROGRAPHIC_INTERNAL_CAPTURE_TOKEN`: shared secret used only for the private hosted position-history capture endpoint
- `OROGRAPHIC_SENTINEL_TOKEN`: shared secret for the internal `/api/ai/sentinel` headline-analysis route
- `OROGRAPHIC_SENTINEL_MODE`: optional; defaults to `shadow`. Set to `active` only when Sentinel event multipliers should affect Scout scoring.
- `OROGRAPHIC_SIDE_MODEL_MODE`: optional; defaults to `shadow`. Set to `active` only after promotion gates pass if the option-payoff side-aware Scout model should steer live call/put direction.
- `OROGRAPHIC_PAYOFF_MODEL_MODE`: optional; defaults to `active` for the existing payoff ranker. Set to `shadow` for observation-only scoring.
- `OROGRAPHIC_PATH_MODEL_MODE`: optional; defaults to `shadow` and currently remains shadow-only even if set. This layer records hold-window quality, early profit-taking odds, and decay risk for research/promotion analysis.

Scheduled Python scans that use Sentinel should also set `OROGRAPHIC_SENTINEL_TOKEN` locally or in GitHub Actions so the engine can call the token-protected Cloudflare route. `OROGRAPHIC_INTERNAL_AI_TOKEN` is also accepted as an alias for local/internal tooling. Without an explicit active mode, Sentinel is logged as a model-observation signal and does not steer live recommendations.

## Model Governance

Model artifacts are pinned in `engine/orographic/models/artifact_manifest.json`. Validate the manifest, model cards, and hashes with:

```bash
python scripts/validate_model_artifacts.py
```

Scout training writes `engine/orographic/models/scout_model_card.json` with feature lists, artifact hashes, walk-forward metrics, Brier score, calibration buckets, side/regime segments, coverage, and feature drift baselines. When trained with strict-real option outcome input, it also writes `engine/orographic/models/scout_side_model.pkl` and records the side-aware target as option-payoff based. That side model remains shadow-only unless `OROGRAPHIC_SIDE_MODEL_MODE=active` is explicitly set.

Payoff-model training writes both the requested report and `engine/orographic/models/payoff_model_card.json` with strict-real option-label definitions, side coverage, option-chain coverage, walk-forward AUC/Brier/log-loss, probability buckets, and the active-by-default activation policy for the recovered payoff ranker.

Path-model training writes both the requested report and `engine/orographic/models/path_model_card.json` with hold-window path targets, quote-path coverage, walk-forward early-take-profit calibration, and a shadow-only activation policy.

Production status today:

- live production path: `council_cost_cap`
- research-only path: `council_cost_cap_path_tiebreaker`
- rejected experimental path: `council_cost_cap_path_tiebreaker_loose`

Recommended payoff-model training flow:

```bash
./.venv/bin/python -m engine.backtest.runner \
  --months 12 \
  --base-budget-usd 300 \
  --hard-cost-ceiling-usd 600 \
  --strict-options-data \
  --min-real-coverage-pct 0.9 \
  --option-outcome-output output/option_outcomes_12mo.json

./.venv/bin/python engine/train_payoff_model.py \
  --input output/option_outcomes_12mo.json
```

`engine/train_payoff_model.py` now prefers canonical `option_outcome_dataset` artifacts first, records those paths as the primary training source in the report/model card, and still accepts legacy backtest results JSON as a fallback. The report/model card now also includes canonical dataset summaries, friction-flip counts, side and regime segmentation, promotion-gate status derived from both dataset coverage and walk-forward metrics, plus a walk-forward family bakeoff across linear, tree, and ensemble model families with an explicit selected family.

Recommended path-model training flow:

```bash
./.venv/bin/python engine/train_path_model.py \
  --input output/option_outcomes_12mo.json
```

`engine/train_path_model.py` reuses the canonical replay loader and strict-real quote-path reconstruction so the shadow path-quality observer can graduate from heuristics to a learned artifact.

Promotion gates should stay pending until a shadow model beats the active system where they disagree, after costs, across 3/6/12-month validation windows and at least 30 live shadow trading days. Promote one layer at a time.

Recommended default:

- keep `TRADIER_SANDBOX_MODE=true`
- keep `TRADIER_LIVE_TRADING_ENABLED=false`
- validate previews and account snapshots locally before enabling live order traffic

The Tradier workflow in this repo currently supports:

1. Server-side status check
2. Server-side account snapshot via the status route
3. Server-side option quote refresh for the arena contracts
4. Quote-derived market value fallback for option positions when the broker omits `current_value`
5. Server-side option order preview using `preview=true`
6. Admin-only limit-order placement for both entries and manual exits
7. Optional private per-run position history capture during Python scan runs
8. Live entry placement gated by admin access and fresh snapshot timing for both live-board and shadow-board entries

## Hosted Position History

Hosted runs can persist private position snapshots in Cloudflare D1 without committing brokerage history into the repo.

- D1 binding: `POSITIONS_DB`
- Private capture route: `POST /api/internal/positions/capture`
- Admin read route: `GET /api/admin/positions-history?limit=20`

The scheduled GitHub Actions scan now posts to the private capture route after each run. The route is protected by `OROGRAPHIC_INTERNAL_CAPTURE_TOKEN`, which should exist in both Cloudflare Pages secrets and the GitHub repo secrets.

## Recommended free deployment

As of April 1, 2026, the default recommendation is:

1. Put the repo on GitHub.
2. Connect `web/` to Cloudflare Pages.
3. Let a scheduled GitHub Actions workflow write `web/data/latest_run.json`.
4. Let Cloudflare Pages redeploy on commit.

An optional `pages_deploy.yml` workflow is included for direct-upload deploys if you would rather use GitHub Actions plus a Cloudflare API token instead of dashboard Git integration.

If you want $0 with a private repo, use a self-hosted GitHub runner on your machine instead of GitHub-hosted minutes.

More detail lives in [deployment-options.md](/Users/mjfrieden/Desktop/2026/Orographic/docs/deployment-options.md).
