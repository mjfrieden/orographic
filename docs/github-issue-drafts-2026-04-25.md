# GitHub Issue Drafts

Date: 2026-04-25

These drafts translate the current Orographic improvement plan into ready-to-file GitHub issues.

## 1. Make `council_cost_cap` the canonical default validation variant

**Title**

`Promote council_cost_cap as the canonical default validation variant`

**Description**

Orographic still surfaces `council_cost_cap_symbol_priors` as the selected walk-forward default in user-facing validation artifacts, even though the strongest current strict-real evidence favors `council_cost_cap`. We should make the cost-capped Council variant the canonical default across dashboard, API, and generated validation outputs, while keeping symbol priors available as an experimental overlay.

**Files**

- [engine/backtest/alpha_experiment.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/backtest/alpha_experiment.py)
- [functions/api/backtest/summary.js](/Users/mjfrieden/Desktop/2026/Orographic/functions/api/backtest/summary.js)
- [web/app.js](/Users/mjfrieden/Desktop/2026/Orographic/web/app.js)
- [web/data/walk_forward_results.json](/Users/mjfrieden/Desktop/2026/Orographic/web/data/walk_forward_results.json)

**Checklist**

- [ ] Add an explicit recommended default variant field to alpha experiment outputs.
- [ ] Make `council_cost_cap` the primary surfaced walk-forward result.
- [ ] Reframe symbol priors as experimental in UI/API copy.
- [ ] Update committed walk-forward artifact to the canonical variant.

**Acceptance**

- The dashboard and `/api/backtest/summary` surface `council_cost_cap` as the default deployable path.
- No user-facing copy implies symbol priors are the current hero variant.

## 2. Stop presenting payoff-model outputs as calibrated probabilities

**Title**

`Relabel payoff-model outputs as ranking signals instead of calibrated probabilities`

**Description**

The payoff model is useful for ranking, but its calibration is not good enough to present user-facing `prob_*` fields as literal odds. We should preserve raw research fields for diagnostics while shifting the UI and snapshot contract toward rank/edge terminology.

**Files**

- [engine/orographic/schemas.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/schemas.py)
- [engine/orographic/payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/payoff_model.py)
- [web/app.js](/Users/mjfrieden/Desktop/2026/Orographic/web/app.js)
- [functions/api/ai/explain.js](/Users/mjfrieden/Desktop/2026/Orographic/functions/api/ai/explain.js)

**Checklist**

- [ ] Add display-safe alias fields such as `payoff_edge_score`.
- [ ] Stop labeling user-facing metrics as PnL probability.
- [ ] Keep raw `prob_*` fields for research/governance only.
- [ ] Update rationale text to use rank/edge language where needed.

**Acceptance**

- Dashboard cards and boards use `Edge` or `Rank` terminology.
- Raw probability-like fields remain available for research but are not presented as calibrated odds.

## 3. Add a hard pre-Council friction gate

**Title**

`Add a pre-Council friction gate using spread, extrinsic, and slippage-adjusted edge`

**Description**

Orographic still passes too many fragile long-premium weekly structures into Council. We need a hard pre-Council veto that estimates edge after friction and rejects candidates that do not clear a minimum threshold after spread, extrinsic burden, and slippage assumptions.

**Files**

- [engine/orographic/forge.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

**Checklist**

- [ ] Compute a friction buffer per candidate.
- [ ] Compute `expected_edge_after_friction_pct`.
- [ ] Reject candidates that fail the minimum threshold.
- [ ] Log friction rejections in Forge diagnostics and notes.

**Acceptance**

- High-extrinsic / wide-spread low-edge candidates are rejected before Council.
- Forge diagnostics expose friction veto counts and rejection details.

## 4. Deduplicate Forge before Council

**Title**

`Deduplicate clustered Forge candidates before Council selection`

**Description**

The current Forge output can contain several near-identical structures for the same symbol and side. This inflates candidate counts and makes Council look smarter than it is. We should deduplicate by symbol/side and keep only the top 1-2 materially distinct structures.

**Files**

- [engine/orographic/forge.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/forge.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)

**Checklist**

- [ ] Group candidates by symbol and option type.
- [ ] Keep only top-ranked materially distinct structures.
- [ ] Record dedupe removals in diagnostics.
- [ ] Verify current clustered cases collapse correctly.

**Acceptance**

- Candidate clusters like nearby same-side SLV calls collapse to at most 1-2 structures.
- Snapshot candidate counts reflect distinct ideas, not strike spam.

## 5. Make the snapshot contract truthful and enforceable

**Title**

`Enforce truthful snapshot count invariants across summary, forge, and council payloads`

**Description**

The snapshot currently allows top-level summary counts to disagree with stored arrays. That breaks governance and undermines every downstream audit. We should store full arrays or explicitly named subsets and add hard invariants to block mismatches.

**Files**

- [engine/orographic/pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/pipeline.py)
- [engine/tests/test_pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_pipeline.py)
- [.github/workflows/ci.yml](/Users/mjfrieden/Desktop/2026/Orographic/.github/workflows/ci.yml)
- [.github/workflows/orographic_scan.yml](/Users/mjfrieden/Desktop/2026/Orographic/.github/workflows/orographic_scan.yml)

**Checklist**

- [ ] Stop truncating stored `scout_signals` and `forge_candidates` or rename them as subsets.
- [ ] Validate summary counts against actual arrays.
- [ ] Fail snapshot generation if counts drift.
- [ ] Add CI/workflow checks for count consistency.

**Acceptance**

- Stored snapshot arrays and summary counts always agree.
- CI and scheduled scans fail on contract mismatches.

## 6. Add model parity and snapshot sanity publish gates

**Title**

`Strengthen publish gates with model parity and snapshot sanity checks`

**Description**

Orographic has already suffered a silent heuristic fallback regression. We need stronger gates before scheduled scans publish: artifact presence, hash validation, snapshot count sanity, and deterministic model parity checks when artifacts are present.

**Files**

- [.github/workflows/ci.yml](/Users/mjfrieden/Desktop/2026/Orographic/.github/workflows/ci.yml)
- [.github/workflows/orographic_scan.yml](/Users/mjfrieden/Desktop/2026/Orographic/.github/workflows/orographic_scan.yml)
- [scripts/validate_model_artifacts.py](/Users/mjfrieden/Desktop/2026/Orographic/scripts/validate_model_artifacts.py)
- [engine/tests/](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests)

**Checklist**

- [ ] Add snapshot count invariants to CI and scheduled scans.
- [ ] Add deterministic artifact-vs-fallback Scout parity tests.
- [ ] Fail scheduled publish if model artifacts are missing or hashless.
- [ ] Keep regression gates ahead of commit/deploy steps.

**Acceptance**

- Scheduled scans cannot publish when trained-vs-fallback parity fails or snapshot invariants break.

## 7. Retrain Scout on option-relevant outcomes

**Title**

`Retrain Scout on option-relevant targets instead of 5-day underlying direction`

**Description**

Scout still targets underlying direction, not the option outcomes Orographic actually monetizes. We should move the next Scout generation toward option-relevant labels such as positive option PnL or move-over-breakeven and fix the cutoff leakage while doing it.

**Files**

- [engine/train_scout_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_scout_model.py)
- [engine/orographic/scout.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/scout.py)
- [engine/orographic/models/scout_model_card.json](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/models/scout_model_card.json)

**Checklist**

- [ ] Define option-relevant Scout labels.
- [ ] Fix post-cutoff label leakage.
- [ ] Retrain and emit updated model card metrics by side and regime.
- [ ] Keep new Scout in shadow until disagreement lift is proven.

**Acceptance**

- New Scout training report is based on option-relevant targets and leakage-safe windows.

## 8. Fix put-side weakness explicitly

**Title**

`Improve put-side payoff model quality with side-balanced training and reporting`

**Description**

Puts are weaker than calls in both model quality and realized results. We should split reporting by side, add balancing or class weighting, and make put-side weakness a first-class tracked risk instead of a footnote.

**Files**

- [engine/train_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/train_payoff_model.py)
- [engine/orographic/models/payoff_model_card.json](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/models/payoff_model_card.json)
- [engine/tests/test_payoff_model.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/tests/test_payoff_model.py)

**Checklist**

- [ ] Add side-balanced sampling or class weighting.
- [ ] Emit call/put-specific AUC, Brier, and coverage metrics.
- [ ] Add acceptance thresholds for put-side quality.
- [ ] Reassess side-specific thresholds after retraining.

**Acceptance**

- Generated model reports show side-specific metrics by default.
- Put-side quality improves materially versus the current baseline.

## 9. Add daily live/shadow attribution by layer

**Title**

`Add daily live/shadow attribution artifacts for Scout, Forge, and Council`

**Description**

We still cannot reliably answer whether P&L came from market beta, Scout direction, Forge economics, or Council filtering. Add a daily attribution artifact and dashboard view for live vs shadow picks, rejected top candidates, side mix, regime, and friction losses.

**Files**

- [engine/orographic/pipeline.py](/Users/mjfrieden/Desktop/2026/Orographic/engine/orographic/pipeline.py)
- [web/data/diagnostics/](/Users/mjfrieden/Desktop/2026/Orographic/web/data/diagnostics)
- [web/app.js](/Users/mjfrieden/Desktop/2026/Orographic/web/app.js)

**Checklist**

- [ ] Emit a daily attribution JSON beside the existing waterfall diagnostics.
- [ ] Include Scout edge, Forge filter reasons, Council demotions, and live/shadow picks.
- [ ] Break down by side, regime, and sector.
- [ ] Surface the attribution in the dashboard.

**Acceptance**

- A daily artifact explains what was picked, what was rejected, and why.
- Live vs shadow comparisons are no longer narrative-only.
