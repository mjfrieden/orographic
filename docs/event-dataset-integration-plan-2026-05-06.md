# Event Dataset Integration Plan

Date: `2026-05-06`

## Goal

Add a shadow-safe event-intelligence layer to Orographic that can use:

- `FNSPID` for ticker-linked news breadth, novelty, sentiment, and catalyst density
- `EDT / TradeTheEvent` for structured corporate event labels
- `MIRAI / GDELT` for macro and geopolitical overlay features
- `StockEmotions` for retail sentiment and emotion regime features

The first implementation slice keeps the live system stable:

- no event dataset is required to run the scan
- missing event data resolves to neutral zeros
- existing Scout artifacts remain compatible because inference still uses the artifact-pinned `feature_cols`
- event features enter the system first as optional training columns and shadow diagnostics

## Canonical Contract

The repo now treats all upstream datasets as inputs to one canonical daily feature store:

- default path: `engine/data/event_features/daily_event_features.parquet`
- override with `OROGRAPHIC_EVENT_FEATURES_PATH` or `--event-features-path`
- supported formats: `.parquet`, `.csv`, `.json`, `.jsonl`

Required columns:

- `symbol`
- `date`

Special symbol:

- `__GLOBAL__` for macro overlays that should apply to every ticker, such as `MIRAI / GDELT` features

Current canonical numeric columns:

- `fnspid_news_volume_1d`
- `fnspid_news_volume_3d`
- `fnspid_sentiment_mean`
- `fnspid_sentiment_std`
- `fnspid_novelty_score`
- `fnspid_catalyst_density`
- `edt_event_intensity`
- `edt_acquisition_score`
- `edt_clinical_trial_score`
- `edt_dividend_score`
- `edt_guidance_score`
- `edt_new_contract_score`
- `edt_repurchase_score`
- `edt_split_score`
- `edt_financing_score`
- `edt_violation_score`
- `edt_risk_warning_score`
- `edt_rating_action_score`
- `mirai_macro_shock_score`
- `mirai_geopolitical_risk_score`
- `mirai_commodity_risk_score`
- `mirai_risk_on_score`
- `mirai_risk_off_score`
- `stocktwits_message_count`
- `stocktwits_bullish_ratio`
- `stocktwits_bearish_ratio`
- `stocktwits_emotion_intensity`

Optional metadata column:

- `dataset_tags`

## Builder Usage

Build the canonical store from raw files with:

```bash
./.venv/bin/python scripts/build_event_features.py \
  --fnspid-input /path/to/fnspid_news.csv \
  --edt-input /path/to/edt_events.jsonl \
  --mirai-input /path/to/mirai_events.csv \
  --stockemotions-input /path/to/stockemotions.csv
```

By default this writes:

- `engine/data/event_features/daily_event_features.parquet`

Use `--replace` if you want to rebuild from scratch instead of merging with an existing store.

## Integration Points

### `Scout` inference

Files:

- [engine/orographic/scout.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py)
- [engine/orographic/event_features.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/event_features.py)

Behavior:

- load the canonical event-feature store once per scan
- look up the latest symbol row on or before the scan date
- merge numeric event features into Scout’s inference feature row
- attach the dataset-backed context to diagnostics
- pass the same context to Sentinel so headline extraction can see the structured daily backdrop

### `Scout` training

Files:

- [engine/train_scout_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_scout_model.py)

Behavior:

- optionally join canonical daily event features by `symbol` and feature date
- expose those columns as candidate training features
- keep labels and walk-forward behavior unchanged
- record event-store coverage in the model-card observability block

### `Sentinel`

Files:

- [engine/orographic/sentinel.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/sentinel.py)
- [functions/api/ai/sentinel.js](/Users/mjfrieden/Desktop/2026/Orographic/functions/api/ai/sentinel.js)

Behavior:

- continue using recent headlines as the primary text source
- also pass dataset-backed event context into the prompt
- keep the route shadow-safe when context is absent

## Rollout Order

### Phase 1: `FNSPID`

Build the canonical daily aggregates first:

- news volume over `1d` and `3d`
- average sentiment and sentiment dispersion
- novelty score
- catalyst density

This is the highest-value first add because the current live system only sees a few Yahoo headlines.

### Phase 2: `EDT / TradeTheEvent`

Add event-type intensity features:

- acquisition
- clinical trial
- dividend
- guidance
- new contract
- repurchase
- split
- overall event intensity

This is the most direct path toward the Sentinel v2 structured-event goal.

Important nuance:

- the official EDT repo documents this narrower corporate-event taxonomy
- broader buckets like `financing`, `violation`, `risk_warning`, and `rating_action` remain in Orographic’s canonical schema for future event sources, but the official EDT processor currently leaves them at zero because EDT does not natively label them

### Phase 3: `MIRAI / GDELT`

Add macro overlay features:

- macro shock score
- geopolitical risk score
- commodity-sensitive risk score
- risk-on score
- risk-off score

This should feed both symbol diagnostics and future regime work.

Implementation status:

- the processor now writes `MIRAI` rows under `symbol="__GLOBAL__"`
- Orographic merges those global rows into every ticker’s event snapshot during training and inference

### Phase 4: `StockEmotions`

Add retail overlay features:

- message count
- bullish ratio
- bearish ratio
- emotion intensity

This stays shadow-first and is expected to be most useful on retail-heavy names.

Implementation status:

- the processor now builds daily `stocktwits_message_count`, `stocktwits_bullish_ratio`, `stocktwits_bearish_ratio`, and `stocktwits_emotion_intensity` features from StockEmotions-style labeled comment files

## Next Implementation Tickets

1. Add walk-forward coverage reporting for event-store sparsity by symbol and date.
2. Add a dedicated research report comparing event-feature uplift versus the current baseline on `3/6/12` month windows.
3. Add a MIRAI/GDELT processor for macro regime overlays.
4. Add a StockEmotions processor for retail sentiment overlays.
