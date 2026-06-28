import assert from "node:assert/strict";
import test from "node:test";

import {
  applyLiveStateOverlays,
  buildExitFeatureMap,
  buildPositionExitAdvice,
  findPositionReference,
  scoreExitHeads,
} from "../../functions/_lib/position_exit.js";

const model = {
  feature_names: ["is_call", "days_to_expiry", "final_candidate_score", "path_decay_risk"],
  policy: {
    profit_harvest_min_open_pl_pct: 0.15,
    profit_harvest_head_min_prob: 0.6,
    sell_head_min_prob: 0.55,
    sell_head_loss_override_pct: -0.3,
  },
  heads: {
    hold: {
      kind: "logistic_regression",
      intercept: 0.4,
      coefficients: { is_call: 0.0, days_to_expiry: 0.8, final_candidate_score: 1.2, path_decay_risk: -1.4 },
      scaler_mean: { is_call: 0, days_to_expiry: 5, final_candidate_score: 0.5, path_decay_risk: 0.5 },
      scaler_scale: { is_call: 1, days_to_expiry: 2, final_candidate_score: 0.2, path_decay_risk: 0.2 },
    },
    harvest: {
      kind: "logistic_regression",
      intercept: -0.2,
      coefficients: { is_call: 0.0, days_to_expiry: -0.6, final_candidate_score: 0.3, path_decay_risk: 0.4 },
      scaler_mean: { is_call: 0, days_to_expiry: 5, final_candidate_score: 0.5, path_decay_risk: 0.5 },
      scaler_scale: { is_call: 1, days_to_expiry: 2, final_candidate_score: 0.2, path_decay_risk: 0.2 },
    },
    sell: {
      kind: "logistic_regression",
      intercept: -0.1,
      coefficients: { is_call: 0.0, days_to_expiry: -1.0, final_candidate_score: -0.6, path_decay_risk: 1.8 },
      scaler_mean: { is_call: 0, days_to_expiry: 5, final_candidate_score: 0.5, path_decay_risk: 0.5 },
      scaler_scale: { is_call: 1, days_to_expiry: 2, final_candidate_score: 0.2, path_decay_risk: 0.2 },
    },
  },
};

const referencePayload = {
  by_contract_symbol: {
    TEST260710C00100000: {
      contract_symbol: "TEST260710C00100000",
      option_type: "call",
      days_to_expiry: 10,
      regime_mode: "risk_on",
      scores: {
        final_candidate_score: 0.8,
        prob_no_trade: 0.1,
        prob_fill_quality_ok: 0.99,
        expected_edge_after_friction_pct: 0.22,
        expected_option_return_pct_model: 0.28,
        path_holding_quality_score: 0.82,
        path_early_profit_take_prob: 0.18,
        path_decay_risk: 0.22,
      },
      risk_features: {
        scout_call_edge_prob: 0.74,
        scout_put_edge_prob: 0.08,
        scout_no_trade_prob: 0.18,
        sentinel_confidence: 0.0,
        sentinel_call_relevance: 0.0,
        sentinel_put_relevance: 0.0,
        sentinel_no_trade_relevance: 1.0,
        delta: 0.38,
        implied_volatility: 0.32,
        iv_rank: 0.44,
        moneyness: 0.01,
        premium_pct_of_spot: 0.018,
        breakeven_move_pct: 0.032,
        projected_move_pct: 0.04,
        extrinsic_ratio: 0.88,
        realized_vol_20d: 0.24,
        atr_pct_14d: 0.021,
      },
    },
  },
};

test("findPositionReference returns latest compact contract context", () => {
  const row = findPositionReference(referencePayload, "TEST260710C00100000");
  assert.equal(row.contract_symbol, "TEST260710C00100000");
  assert.equal(row.option_type, "call");
});

test("buildExitFeatureMap pulls training-aligned features from reference rows", () => {
  const bundle = buildExitFeatureMap({
    view: { option_type: "call", dte: 8, regime_mode: "risk_on", regime_bias: 0.2 },
    reference: referencePayload.by_contract_symbol.TEST260710C00100000,
    candidate: null,
    lane: "live",
  });
  assert.equal(bundle.features.is_call, 1);
  assert.equal(bundle.features.final_candidate_score, 0.8);
  assert.equal(bundle.features.path_decay_risk, 0.22);
});

test("applyLiveStateOverlays boosts sell pressure on short-dated drawdowns", () => {
  const adjusted = applyLiveStateOverlays(
    { hold: 0.45, harvest: 0.2, sell: 0.4 },
    { open_pl_pct: -0.42, dte: 2, spread_pct: 0.18, regime_aligned: false },
  );
  assert.ok(adjusted.sell > adjusted.hold);
  assert.ok(adjusted.sell > 0.55);
});

test("scoreExitHeads and buildPositionExitAdvice favor hold when runway and quality are intact", () => {
  const reference = referencePayload.by_contract_symbol.TEST260710C00100000;
  const bundle = buildExitFeatureMap({
    view: { option_type: "call", dte: 9, regime_mode: "risk_on", regime_bias: 0.2 },
    reference,
    candidate: null,
    lane: "live",
  });
  const base = scoreExitHeads(model, bundle.features);
  assert.ok(base.hold > base.sell);

  const result = buildPositionExitAdvice({
    model,
    view: { option_type: "call", dte: 9, open_pl_pct: 0.06, spread_pct: 0.08, regime_mode: "risk_on", regime_aligned: true },
    reference,
    candidate: null,
    lane: "live",
  });
  assert.equal(result.advice.action, "hold");
  assert.equal(result.exit_style, "hold");
});

test("buildPositionExitAdvice harvests gains when open PnL and harvest head line up", () => {
  const harvestFriendlyModel = {
    ...model,
    heads: {
      ...model.heads,
      harvest: {
        ...model.heads.harvest,
        intercept: 0.9,
      },
    },
  };
  const result = buildPositionExitAdvice({
    model: harvestFriendlyModel,
    view: { option_type: "call", dte: 2, open_pl_pct: 0.32, spread_pct: 0.12, regime_mode: "risk_on", regime_aligned: true },
    reference: referencePayload.by_contract_symbol.TEST260710C00100000,
    candidate: null,
    lane: "live",
  });
  assert.equal(result.advice.action, "sell");
  assert.equal(result.exit_style, "profit_harvest");
});

test("buildPositionExitAdvice exits when sell pressure dominates", () => {
  const stressedReference = {
    ...referencePayload.by_contract_symbol.TEST260710C00100000,
    scores: {
      ...referencePayload.by_contract_symbol.TEST260710C00100000.scores,
      final_candidate_score: 0.28,
      path_decay_risk: 0.88,
      path_holding_quality_score: 0.18,
    },
  };
  const result = buildPositionExitAdvice({
    model,
    view: { option_type: "call", dte: 1, open_pl_pct: -0.38, spread_pct: 0.28, regime_mode: "risk_off", regime_aligned: false },
    reference: stressedReference,
    candidate: null,
    lane: "shadow",
  });
  assert.equal(result.advice.action, "sell");
  assert.equal(result.exit_style, "risk_exit");
  assert.ok(result.model_scores.sell >= result.model_scores.hold);
});
