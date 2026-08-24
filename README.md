# Orographic

Orographic is a new short-term options platform built from the useful parts of the prior weather systems, but with a much cleaner contract between signal generation, contract selection, portfolio construction, and presentation.

It is split into three first-party layers:

- `Scout`: a Cirrus-style symbol and direction engine. It decides whether a name has enough short-term edge to even deserve option-chain work.
- `Forge`: a Cumulus-style contract engine. It chooses the actual weekly contract and combines quote quality, breakeven burden, payoff shape, learned payoff rank, path quality, and transaction-cost awareness.
- `Council`: a Stratus-style portfolio gate. It selects one ranked R&D board and enforces side, sector, correlation, sizing, and no-trade discipline.

The game layer lives in `web/`. It is designed to deploy cleanly to Cloudflare Pages as a static site.

The protected access layer lives in `functions/` and runs as Cloudflare Pages Functions. It signs a short-lived session cookie and validates users from environment secrets, so the public repository never needs to store login credentials.

The current game loop also uses Pages Functions as a thin Tradier proxy. The browser never sees the Tradier token directly. Order previews and submissions stay server-side.

## Why this version is different

- No synthetic bid/ask fallback in the scan engine.
- One canonical snapshot schema from Scout to Forge to Council.
- Hard abstain support instead of forcing a pick.
- One canonical production lane combines Scout, Sentinel, Forge, path quality, cost-aware ranking, and Council.
- The former gated/shadow stack remains available only as a comparison baseline with `--model-stack current_gated`.
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

The scan defaults to `--model-stack unified_rnd`. Its only production and
buy-to-open authority is `council.live_board`. Moonshot remains a separate,
visible experimental side lane: it emits at most one tail-upside pick for
prospective outcome tracking, but does not influence the primary ensemble,
Council, sizing, or Tradier routing. Shadow-board, counterfactual, arbitrary
Forge, Moonshot, and manual HOLD candidates cannot be previewed or opened
through the broker transmitter. Use
`--model-stack current_gated` only to reproduce the former scoring baseline; it
does not create another order lane.

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

Each snapshot still includes `promotion_readiness` for evidence and auditability. In the unified R&D stack it does not split models into a second product lane or silently disable a layer; it records whether the combined stack has enough evidence for deployment beyond R&D.

Each scan also appends a legacy-named side-aware Scout disagreement ledger beside the diagnostics:

- `web/data/diagnostics/side_aware_scout_shadow_ledger.json`

This ledger records offline model disagreements for historical comparability. It is research telemetry, not a candidate lane, and has no Council or broker authority.

The unified stack does not allocate a counterfactual observation lane. The old payload key remains as an always-empty compatibility field so archived snapshots and readers do not break. Broker-side quote freshness, spread, buying-power, credential, preview, and live-confirmation controls remain enforced independently of model-stack selection.

Each scan also appends a rolling board history ledger beside the diagnostics:

- `web/data/diagnostics/board_recommendation_history.json`

This ledger preserves historical live/shadow fields for archive compatibility. New production scans populate one Council board and leave the shadow field empty.

Each scan also appends a dedicated moonshot prospective ledger:

- `web/data/diagnostics/moonshot_prospective_ledger.json`

This ledger records the visible Moonshot side pick and tail-upside research observations with their score, eligibility reasons, emission quote, model context, risk features, and fixed outcome slots. Moonshot is its own experimental lane, but remains non-routable and outside the primary ensemble.

Prospective outcome capture uses policy v2. One-hour quotes must be retrieved within 15 minutes of the target; close-based quotes must be retrieved within 30 minutes. Broker quote timestamps are retained, quotes older than 15 minutes are rejected, and late windows are recorded as `missed_live_window` rather than filled with a current quote. Missing or stale quotes remain retryable inside the live window and recoverable from immutable archives afterward. Legacy pending picks are frozen so current prices cannot contaminate historical horizons.

The `Orographic Outcome Capture` workflow checks eligible windows hourly at `:25` from 09:25 through 15:25 Chicago time. It calls Tradier while a policy-v2 contract is active for trajectory evidence or inside a fixed capture window, refreshes challenger evidence, and persists operational state to R2 when capture state changes.

The post-login Research drawer reads `web/data/diagnostics/model_governance_summary_latest.json`, a stable UI contract generated from scan health, challenger cards, and prospective evidence. It separates data-capture health, research status, and live authority; provides keyboard-accessible Scout, payoff, veto, and exit tabs; and keeps every challenger explicitly locked out of Council, sizing, and Tradier routing. When Council abstains, the primary signal card also shows a compact Universe → Scout → Forge → Council funnel with research-only no-trade observations reported separately.

Archive live option chains for future model training:

```bash
./.venv/bin/python scripts/archive_live_option_chains.py \
  --snapshot web/data/latest_run.json \
  --snapshot-symbols-only \
  --output-dir engine/data/live_options_archive
```

The archive writes partitioned parquet chains plus a manifest under `engine/data/live_options_archive/`. Use `--snapshot-symbols-only` for a lighter scheduled capture, or omit it to archive the full configured universe.

Archive schema v2 retains the chain-capture timestamp, last-trade timestamp and age, quote mid, dollar and percentage spread, IV validity, and two-sided-quote validity. Its manifest reports quality coverage instead of treating every downloaded row as equally usable.

When `TRADIER_ACCESS_TOKEN` (or `OROGRAPHIC_TRADIER_ACCESS_TOKEN`) is configured, production expiration discovery and Greeks-enabled option-chain retrieval use Tradier’s market-data endpoints. Candidate and archive rows retain `tradier` source provenance. If Tradier market data is temporarily unavailable, the adapter falls back to yfinance and labels those rows `yfinance_fallback` so research can segment or exclude them.

Forge derives point-in-time volatility-surface telemetry from the complete call/put chain: ATM IV, quadratic smile slope and curvature, 3–8% OTM put-minus-call wing skew, 30-day-normalized ATM term slope, fit RMSE, observation count, contract IV relative to ATM, and signed IV-minus-realized volatility. These fields are persisted into prospective ledgers and canonical training rows. Existing model artifacts continue using their stored feature contract; the new fields affect ranking only after a retrained model passes the standard purged and prospective gates.

Build canonical research datasets from prospective ledgers:

```bash
./.venv/bin/python scripts/build_research_datasets.py \
  --prospective-ledger web/data/diagnostics/prospective_pick_ledger.json \
  --moonshot-ledger web/data/diagnostics/moonshot_prospective_ledger.json \
  --output-dir output/research_datasets
```

This produces option-recommendation and moonshot outcome tables suitable for future payoff, path, side-aware, and moonshot model training.

Production scans also capture one executable call and one executable put for
each eligible Forge symbol at the same expiry, choosing the contracts nearest
`0.35` absolute delta and then the tighter spread. These are matched research
observations, not another product lane: they cannot enter Forge ranking,
Council, Moonshot, sizing, order preview, or Tradier submission. If the chosen
same-side contract is already a Forge candidate, the ledger reuses that row
rather than duplicating it. Disable this data capture for an emergency or
rate-limit investigation with
`./.venv/bin/python -m engine.run_scan --no-paired-side-capture`.
Canonical outcome summaries report explicit paired contract rows, pair IDs,
complete call/put pairs, and paired symbol/dates so accumulation is measurable.

Tradier order provenance also records model-ready execution telemetry for previews and submissions: quote age and spread, broker round-trip latency, requested limit, observed average fill, fees, fill delay, and signed adverse slippage. Preview/submission failures are retained as events so fill-quality research is not biased toward successful requests only.

Before building datasets, enrich ledgers with dense path labels from archived option chains:

```bash
./.venv/bin/python scripts/mark_path_outcomes_from_archive.py \
  --archive-dir engine/data/live_options_archive \
  --ledger web/data/diagnostics/prospective_pick_ledger.json \
  --ledger web/data/diagnostics/moonshot_prospective_ledger.json
```

This adds an `archived_quote_path` block to each pick when archived chains contain the contract. It records observed quote marks, max favorable/adverse excursion, first hit, and take-profit-before-stop labels.

Audit research data capture after archiving and dataset generation:

```bash
./.venv/bin/python scripts/audit_research_data_capture.py \
  --live-archive-manifest engine/data/live_options_archive/coverage_manifest.json \
  --research-dataset-dir output/research_datasets
```

The scheduled workflow runs this audit and fails if the live chain archive is empty, required ledgers are missing, or the generated datasets are internally inconsistent.

For durable storage beyond GitHub's short-lived workflow artifacts, configure `OROGRAPHIC_RESEARCH_R2_BUCKET` and `CLOUDFLARE_R2_API_TOKEN` as GitHub secrets alongside `CLOUDFLARE_ACCOUNT_ID`. The R2 token should include Cloudflare's `Workers R2 Storage Edit` permission for the account or target bucket. When present, the scan workflow restores the prior canonical evidence bundle, compacts it with the current ledgers, strict outcomes, and option quotes, uploads a timestamped raw snapshot with a verifiable manifest/catalog entry, and publishes the canonical bundle manifest last.

Mutable prospective and shadow ledgers are synchronized separately as content-addressed, gzip-compressed R2 objects under `orographic/operational-ledgers/v1`. Both live workflows restore that state before writing and publish the manifest last after writing. The full ledgers remain research-only; `scripts/build_pages_bundle.py` stages a clean Pages directory that excludes them, while `scripts/build_dashboard_prospective_summary.py` publishes a small aggregate plus the eight most recent scan entries for the dashboard. Outcome capture runs hourly at `:25` from 09:25 through 15:25 Chicago time, leaving scans scheduled at `:07` enough time to release the shared research-ledger lock. It no longer creates frequent data commits on `main`, and visibly fails if R2 or Pages publication fails.

### Canonical evidence lifecycle

The evidence lifecycle and migration decision is documented in
[`docs/evidence-lifecycle-and-consolidation-2026-08-15.md`](docs/evidence-lifecycle-and-consolidation-2026-08-15.md).
The canonical bundle separates cumulative inventory, strict training-eligible
evidence, and the exact current-model prospective cohort.

Build and validate the bundle locally with:

```bash
python scripts/consolidate_research_evidence.py \
  --source-root output/restored_canonical_evidence \
  --source-root output/research_datasets \
  --source-root engine/data/live_options_archive \
  --output-dir output/canonical_evidence
```

Restore it on a clean machine with:

```bash
python scripts/restore_research_artifacts_from_r2.py \
  --mode canonical \
  --output-dir output/restored_canonical_evidence
```

For R2 objects created before snapshot manifests existed, use `--mode legacy`
once to bootstrap them into the compactor. The restore uses Cloudflare's R2
Objects API to list every matching object rather than relying on a developer's
local archive.

Materialize the canonical quote history in the partition layout consumed by
Cirrus with:

```bash
python scripts/materialize_canonical_evidence_for_cirrus.py \
  --canonical-dir output/canonical_evidence \
  --output-dir engine/data/options/blended/partitioned
```

### Shared Cirrus + Orographic research mart

The shared research mart conforms Orographic canonical evidence and Cirrus's neutral research
export into versioned analytical tables for point-in-time backtests and paired model comparisons.
It preserves source system, cohort, model version, label contract, and exit policy rather than
blending the two systems into an untraceable result.

```bash
python scripts/build_shared_research_mart.py \
  --orographic-canonical-dir output/canonical_evidence \
  --cirrus-export-dir ../Cirrus/analysis/output/options_research_bundle \
  --output-dir output/shared_research_mart

# Validate the complete publication plan; this does not write to Cloudflare.
python scripts/publish_shared_research_mart.py \
  --mart-dir output/shared_research_mart
```

Build Orographic's pinned, observation-only consumer bundle after validating a complete two-source
mart. These views power execution-quality research, executable exit replay, daily Cirrus/Orographic
comparisons, fold-frozen training, and model monitoring without changing live scoring or routing:

```bash
python scripts/build_shared_mart_consumers.py \
  --mart-dir output/shared_research_mart \
  --output-dir output/shared_mart_consumers

python scripts/build_rebuild_readiness.py \
  --mart-consumer-manifest output/shared_mart_consumers/consumer_manifest.json

python scripts/build_shared_mart_shadow_evidence.py \
  --consumer-dir output/shared_mart_consumers
```

The consumer manifest pins every output to one validated `mart_id` and declares
`observation_only_never_used_for_routing`. Rebuild readiness fails closed when the bundle is absent,
incomplete, built from only one source, or missing any registered research view.
The shadow-evidence artifact summarizes execution friction, target/stop touches on executable bid
paths, daily cross-system comparisons, training coverage, and monitoring cohorts. It can authorize
only a pre-registered shadow experiment; it always reports `production_changes_allowed: false`.

The optional Iceberg publisher requires `engine/requirements-mart.txt` and explicit
`OROGRAPHIC_R2_DATA_CATALOG_*` credentials. It will not publish a partial one-system mart.
Architecture, table grains, safety rules, and rollout gates are documented in
[`docs/shared-research-mart.md`](docs/shared-research-mart.md).

The committed `data/evidence_seed/strict_option_outcomes.json.gz` preserves the
740-row strict executable-label v2 dataset that predated R2 manifests. It is a
one-time immutable migration input, not a second production lane or a file to
replace during routine scans.

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

For replayable production inputs, first normalize raw observations into the Event Observatory. Each
record retains the canonical raw payload and source lineage, and uses the later of publication time and first-seen time as its
effective timestamp, preventing delayed observations from leaking into earlier training dates:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/build_event_observatory.py \
  --input sec=sec=/path/to/sec_filings.csv \
  --input wire=news=/path/to/timestamped_news.jsonl \
  --input macro=macro=/path/to/macro_events.csv \
  --output engine/data/event_features/event_observatory.parquet
```

Input specifications use `SOURCE=KIND=PATH`; supported kinds are `news`, `structured_event`,
`macro`, `sec`, and `social`. The companion `.quality.json` reports rejected and duplicate rows,
symbol/source coverage, missing evidence, and collection-delay percentiles. Existing event IDs are
immutable: rebuilding against an existing store fails if a source reuses an ID for changed content.

The scheduled scan restores a rolling Event Observatory cache, collects current SEC filings for the
latest snapshot symbols and current GDELT macro headlines, then rebuilds daily features before Scout
runs. Configure `OROGRAPHIC_SEC_USER_AGENT` as an SEC-compliant application identity containing a
real contact address. The rolling Observatory, quality report, raw collection files, daily features,
and event-enriched outcomes are included in workflow artifacts and the optional R2 research export.

Build daily features from the normalized store:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/build_event_features.py \
  --observatory-input engine/data/event_features/event_observatory.parquet
```

The legacy source-specific inputs remain available for research and migration:

```bash
./.venv/bin/python scripts/build_event_features.py \
  --fnspid-input /path/to/fnspid_news.csv \
  --edt-input /path/to/edt_events.jsonl \
  --mirai-input /path/to/mirai_events.csv \
  --stockemotions-input /path/to/stockemotions.csv
```

This writes `engine/data/event_features/daily_event_features.parquet` by default. Override the path with `OROGRAPHIC_EVENT_FEATURES_PATH` or `engine/train_scout_model.py --event-features-path`.

Build a leakage-safe event-coverage dataset after the canonical research datasets exist:

```bash
PYTHONPATH=. ./.venv/bin/python scripts/build_event_outcome_coverage.py \
  --observatory engine/data/event_observatory/event_observatory.parquet \
  --outcomes output/research_datasets/option_recommendation_outcomes.parquet
```

The resulting `event_enriched_option_outcomes.parquet` preserves every prospective recommendation row and adds
only observations whose effective timestamp was at or before that recommendation. Its companion
`event_outcome_coverage.json` tracks activation across all recommendations and completed outcomes.

Observatory news and social records also produce the Narrative Expectations feature family:

- one-day and three-day attention volume
- three-day attention acceleration using calendar-day gaps
- independent-source diversity and duplicate-story concentration
- mean novelty, directional intensity, and multi-source confirmation
- `narrative_hype_pressure`, a bounded diagnostic composite

Scout training may use the atomic narrative features. The composite hype-pressure score is excluded
from the training slice and remains diagnostic until payoff-aware ablations establish incremental
value. SEC filings and macro events remain separate evidence families; they do not count as narrative
attention merely because they exist in the Observatory. Event-enriched outcome rows include matching
`*_at_entry` fields calculated from observations available before each recommendation timestamp.

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

Run an exact primary-ensemble component ablation (Moonshot is intentionally
excluded because it is a separate side experiment):

```bash
./.venv/bin/python -m engine.backtest.alpha_experiment \
  --unified-ablation-only \
  --strict-options-data \
  --expiry-policy target_dte \
  --target-dte-min 7 \
  --target-dte-max 14
```

Compare the historical three-pick research policy with the one-pick production
core Council gates and account-level drawdown. This diagnostic does not replay
live market-shock or prior-board turnover state:

```bash
./.venv/bin/python -m engine.backtest.alpha_experiment \
  --council-risk-ablation-only \
  --initial-account-equity-usd 10000 \
  --strict-options-data \
  --expiry-policy target_dte \
  --target-dte-min 7 \
  --target-dte-max 14
```

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
- `OROGRAPHIC_MAX_ENTRY_COST_BASIS_USD`: server-enforced buy-to-open cost-basis ceiling, default `$600`; applied to preview and submission after the live quote is loaded
- `OROGRAPHIC_INTERNAL_CAPTURE_TOKEN`: shared secret used only for the private hosted position-history capture endpoint
- `OROGRAPHIC_SENTINEL_TOKEN`: shared secret for the internal `/api/ai/sentinel` headline-analysis route
- `OROGRAPHIC_MODEL_STACK`: optional; defaults to `unified_rnd`. This activates the integrated side, Sentinel, payoff, path, and cost-aware stack in one R&D lane. Set it to `current_gated` to reproduce the former promotion-gated baseline.
- `OROGRAPHIC_SENTINEL_MODE`, `OROGRAPHIC_SIDE_MODEL_MODE`, `OROGRAPHIC_PAYOFF_MODEL_MODE`, and `OROGRAPHIC_PATH_MODEL_MODE`: legacy per-layer overrides used by `current_gated`; the unified stack activates and combines these layers explicitly.

Scheduled Python scans that use Sentinel should also set `OROGRAPHIC_SENTINEL_TOKEN` locally or in GitHub Actions so the engine can call the token-protected Cloudflare route. `OROGRAPHIC_INTERNAL_AI_TOKEN` is also accepted as an alias for local/internal tooling. Without an explicit active mode, Sentinel is logged as a model-observation signal and does not steer live recommendations.

## Model Governance

Model artifacts are pinned in `engine/orographic/models/artifact_manifest.json`. Validate the manifest, model cards, and hashes with:

```bash
python scripts/validate_model_artifacts.py
```

Scout training writes `engine/orographic/models/scout_model_card.json` with feature lists, artifact hashes, walk-forward metrics, Brier score, calibration buckets, side/regime segments, coverage, and feature drift baselines. When trained with strict-real option outcome input, it also writes `engine/orographic/models/scout_side_model.pkl` and records the side-aware target as option-payoff based. The unified R&D stack uses it as a confidence-gated direction correction instead of a serial veto.

For hierarchical call-versus-put training, only rows carrying the same explicit
matched-pair identifier qualify as paired direction evidence. Merely finding an
unrelated call and put for the same symbol/date is reported but cannot satisfy
the paired-evidence promotion gate.

Build the fail-closed matched-pair readiness report with:

```bash
python scripts/build_scout_pair_readiness.py --warn-only
```

The report requires at least 150 complete strict-executable pairs, 50 call-edge
and 50 put-edge outcomes, 30 independent decision dates, two regimes with 25
pairs each, and three usable purged walk-forward folds. Each fold must train and
freeze its own model/scaler/calibrator bundle before validation. Passing these
collection gates permits an offline evaluation only; it cannot replace active
artifacts, affect Council, size an order, or route through Tradier.

The same training run now builds `scout_hierarchical_challenger.pkl` when strict option outcomes are available. Its first head predicts trade versus abstain; its second head predicts call versus put using only dates where both sides were observed. The unified stack blends this challenger into the side ensemble at a bounded 20% weight; `current_gated` keeps its legacy observation-only treatment.

Use `--hierarchical-only` to train that challenger while preserving every active Scout model, scaler, side model, and model card:

```bash
python -m engine.train_scout_model --cutoff 2026-08-11 --hierarchical-only \
  --option-outcome-input output/option_outcomes_live_recommendations.json
```

Payoff-model training writes both the requested report and `engine/orographic/models/payoff_model_card.json` with strict-real option-label definitions, side coverage, option-chain coverage, walk-forward AUC/Brier/log-loss, probability buckets, and the active-by-default activation policy for the recovered payoff ranker.

Path-model training writes both the requested report and `engine/orographic/models/path_model_card.json` with hold-window path targets, quote-path coverage, walk-forward early-take-profit calibration, and decay risk. The unified R&D stack folds that score into Forge ranking; legacy gated runs keep the prior observation-only policy.

R&D status today:

- canonical research path: `unified_council_cost_cap`
- comparison baseline: `council_cost_cap` with `model_stack=current_gated`
- deployment promotion: held pending leakage-safe out-of-sample validation

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

Train the cost-aware multi-task challenger without granting it any live authority:

```bash
./.venv/bin/python scripts/train_cost_aware_payoff_challenger.py \
  --input output/option_outcomes_12mo.json
```

The challenger adds q10/q50/q90 strict after-cost return estimates, fill quality, positive-P&L and breakeven probabilities, favorable/adverse path heads, and target-before-stop probability. Quantiles are projected into monotone order, while promotion gates check central-interval coverage and realized performance for the conservative `q10 > 0` selector. Its scores are persisted for prospective replay but cannot alter Forge order, Council eligibility, sizing, or Tradier routing.

Audit the payoff contribution with fold-specific retraining and exact zero-weight
and inverted-orientation comparisons:

```bash
python scripts/build_payoff_stack_audit.py
```

The evaluator groups rows by decision date, purges labels that were not
available before each validation block, fits and freezes a pre-registered
linear classifier and q10 model inside every fold, and hashes each fold's
training and validation evidence. Fixed full-history artifacts are reported
separately and cannot satisfy promotion gates. The output is research-only and
cannot write active models or change the unified production rank.

Recommended path-model training flow:

```bash
./.venv/bin/python engine/train_path_model.py \
  --input output/option_outcomes_12mo.json
```

`engine/train_path_model.py` reuses the canonical replay loader and strict-real quote-path reconstruction so the shadow path-quality observer can graduate from heuristics to a learned artifact.

Build the stricter competing-risk exit challenger and its data-quality report with:

```bash
python scripts/train_path_hazard_challenger.py \
  --input output/option_outcomes_live_recommendations.json
```

This evaluator models +25% target, -50% stop, and expiry hazards only when timestamp-valid pre-exit marks exist. It excludes and reports post-exit marks, compares fixed and shadow exits on identical entries with purged folds, and writes `web/data/diagnostics/path_hazard_challenger_latest.json`. If evidence is inadequate it emits a HOLD card and deliberately writes no model artifact.

Prospective path collection is performed by the Tradier outcome workflow that captures fixed exits. A Cloudflare cron dispatcher invokes it hourly from 09:25 through 15:25 America/Chicago on weekdays and reports dispatch delay as capture health. For every active policy-v2 contract, the workflow appends one fresh, minute-deduplicated `trajectory_mark` per run from emission through Friday close. These contract-specific marks take priority over the rotating full-chain archive when path labels are rebuilt. The scan-health report marks `trajectory_capture_health` degraded whenever an active pick receives a missing or stale quote. The workflow pages only when current-run trajectory coverage is 30% or lower; partial capture remains visible in governance without generating a failure email. Historical capture debt is reported separately from current-run health.

Model changes should remain pending until the challenger beats the current unified system where they disagree, after costs, across leakage-safe 3/6/12-month validation windows and at least 30 prospective trading days. Comparison is offline or telemetry-only; it never creates a second production lane.

Replay the canonical promotion comparison from the existing prospective and side-aware shadow ledgers:

```bash
python scripts/build_promotion_comparison.py
```

The replay writes `web/data/diagnostics/promotion_shadow_active_comparison_latest.json`. It compares one-contract active (`live`) and shadow recommendations at the emission ask and Friday-close bid, reports gross and after-spread P&L, calibration, annualized daily Sharpe, and drawdown for 3-, 6-, and 12-month windows. Repeated intraday scans are collapsed to daily contract exposures, and a paired-market-day bootstrap reports the challenger return-lift distribution. Promotion requires at least 30 paired days, a positive 95% lower confidence bound, at least 95% bootstrap probability of improvement, and positive cluster-adjusted challenger profitability—not merely a smaller loss than active. A window cannot pass until its full history is present. Normal scans refresh this artifact before rebuilding promotion readiness; the comparison is diagnostic-only and does not alter scoring or decision weights.

Build the paired prospective evidence report for the volatility/contract payoff challenger:

```bash
python scripts/build_payoff_challenger_evidence.py
```

This writes `web/data/diagnostics/payoff_challenger_evidence_latest.json`. It compares current and challenger probabilities on the exact same recommendations using only strict executable Friday-close labels, reports discrimination and calibration by side and regime, and replays each fully resolved scan with the top current-ranked versus challenger-ranked contract. Incomplete candidate sets fail closed and are excluded from the rank replay. A challenger may replace or change weight within the one unified stack only after adequate resolved samples and disagreements, both call and put coverage, at least two qualified regimes, positive challenger profitability, and a positive paired-run bootstrap lower bound. It never receives separate Tradier authority.

Build the Scout no-trade veto value and threshold-frontier report:

```bash
python scripts/build_counterfactual_veto_evidence.py
```

This writes `web/data/diagnostics/counterfactual_veto_evidence_latest.json`. It uses only strict policy-v2 Friday-close executable outcomes, isolates the latest Scout side-model version, and collapses repeated scans to the first recommendation for each Central trading date, symbol, and contract. It reports retained and vetoed after-cost outcomes across a probability/margin grid, segments the current rule by side and regime, and performs market-day clustered inference. Threshold selection is evaluated with expanding walk-forward blocks and a one-trading-day embargo, so test dates never choose their own cutoff. The artifact is advisory-only: even `eligible_for_policy_review` cannot change Council or Tradier behavior.

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
8. Buy-to-open placement gated by admin access and fresh snapshot timing, and restricted to `council.live_board`

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
3. Let a scheduled GitHub Actions scan write `web/data/latest_run.json`, commit the refreshed artifacts, and trigger the Pages redeploy from `main`.
4. Keep Cloudflare Pages connected to the repo so the commit remains the publish event.

The `orographic_scan.yml` workflow is the canonical production publisher. It refreshes the snapshot, pushes the artifact commit to GitHub, and deploys Pages from that commit.

If you want $0 with a private repo, use a self-hosted GitHub runner on your machine instead of GitHub-hosted minutes.

More detail lives in [deployment-options.md](/Users/mjfrieden/Desktop/2026/Orographic/docs/deployment-options.md).
