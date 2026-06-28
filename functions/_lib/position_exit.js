const MODEL_PATH = "/data/diagnostics/position_exit_model_latest.json";
const REFERENCE_PATH = "/data/diagnostics/position_exit_reference_latest.json";

function asNumber(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clamp(value, low = 0, high = 1) {
  if (!Number.isFinite(value)) {
    return low;
  }
  return Math.max(low, Math.min(high, value));
}

function sigmoid(value) {
  const clipped = Math.max(-30, Math.min(30, Number(value) || 0));
  return 1 / (1 + Math.exp(-clipped));
}

async function loadJsonAsset(context, assetPath) {
  const url = new URL(assetPath, context.request.url);
  const cookie = context.request.headers.get("cookie") || "";
  const assetFetch = context.env?.ASSETS?.fetch?.bind(context.env.ASSETS);
  const response = assetFetch
    ? await assetFetch(
        new Request(url.toString(), {
          headers: cookie ? { cookie } : undefined,
        }),
      )
    : await fetch(url.toString(), {
        headers: cookie ? { cookie } : undefined,
      });
  if (!response.ok) {
    throw new Error(`Unable to load ${assetPath} (${response.status})`);
  }
  return response.json();
}

export async function loadPositionExitModel(context) {
  return loadJsonAsset(context, MODEL_PATH);
}

export async function loadPositionExitReference(context) {
  return loadJsonAsset(context, REFERENCE_PATH);
}

export function findPositionReference(referencePayload, contractSymbol) {
  const target = String(contractSymbol || "").trim().toUpperCase();
  if (!target) {
    return null;
  }
  const lookup = referencePayload?.by_contract_symbol;
  if (!lookup || typeof lookup !== "object") {
    return null;
  }
  return lookup[target] || null;
}

function candidateToReference(candidate, lane, regimeMode, regimeBias) {
  if (!candidate || typeof candidate !== "object") {
    return null;
  }
  return {
    contract_symbol: String(candidate.contract_symbol || "").trim().toUpperCase(),
    symbol: candidate.symbol || null,
    option_type: candidate.option_type || null,
    expiry: candidate.expiry || null,
    strike: candidate.strike ?? null,
    days_to_expiry: candidate.days_to_expiry ?? null,
    lane,
    lane_reason: null,
    run_generated_at_utc: null,
    entry_cost_per_contract: candidate.contract_cost ?? candidate.ask ?? null,
    regime_mode: regimeMode || null,
    regime_bias: regimeBias ?? null,
    scores: {
      final_candidate_score: candidate.final_candidate_score ?? candidate.forge_score ?? null,
      forge_score: candidate.forge_score ?? null,
      expected_edge_after_friction_pct: candidate.expected_edge_after_friction_pct ?? null,
      expected_option_return_pct_model: candidate.expected_option_return_pct_model ?? null,
      prob_no_trade: candidate.prob_no_trade ?? null,
      prob_fill_quality_ok: candidate.prob_fill_quality_ok ?? null,
      prob_positive_option_pnl: candidate.prob_positive_option_pnl ?? null,
      path_holding_quality_score: candidate.path_holding_quality_score ?? null,
      path_early_profit_take_prob: candidate.path_early_profit_take_prob ?? null,
      path_decay_risk: candidate.path_decay_risk ?? null,
    },
    risk_features: {
      delta: candidate.delta ?? null,
      implied_volatility: candidate.implied_volatility ?? null,
      iv_rank: candidate.iv_rank ?? null,
      moneyness: candidate.moneyness ?? null,
      premium_pct_of_spot: candidate.premium_pct_of_spot ?? null,
      breakeven_move_pct: candidate.breakeven_move_pct ?? null,
      projected_move_pct: candidate.projected_move_pct ?? null,
      extrinsic_ratio: candidate.extrinsic_ratio ?? null,
      realized_vol_20d: candidate.realized_vol_20d ?? null,
      atr_pct_14d: candidate.atr_pct_14d ?? null,
      scout_call_edge_prob: candidate.scout_call_edge_prob ?? null,
      scout_put_edge_prob: candidate.scout_put_edge_prob ?? null,
      scout_no_trade_prob: candidate.scout_no_trade_prob ?? null,
      sentinel_confidence: candidate.sentinel_confidence ?? null,
      sentinel_call_relevance: candidate.sentinel_call_relevance ?? null,
      sentinel_put_relevance: candidate.sentinel_put_relevance ?? null,
      sentinel_no_trade_relevance: candidate.sentinel_no_trade_relevance ?? null,
    },
  };
}

export function buildExitFeatureMap({ view, reference, candidate, lane }) {
  const referenceRow =
    reference ||
    candidateToReference(candidate, lane, view.regime_mode, view.regime_bias);
  if (!referenceRow) {
    return null;
  }
  const scores = referenceRow.scores || {};
  const risk = referenceRow.risk_features || {};
  const optionType = String(referenceRow.option_type || view.option_type || "").toLowerCase();
  const regimeMode = String(referenceRow.regime_mode || view.regime_mode || "").toLowerCase();
  return {
    reference: referenceRow,
    features: {
      is_call: optionType === "call" ? 1 : 0,
      days_to_expiry: asNumber(referenceRow.days_to_expiry ?? view.dte, 0) ?? 0,
      final_candidate_score: asNumber(scores.final_candidate_score, 0) ?? 0,
      prob_no_trade: asNumber(scores.prob_no_trade, 0) ?? 0,
      prob_fill_quality_ok: asNumber(scores.prob_fill_quality_ok, 0) ?? 0,
      expected_edge_after_friction_pct: asNumber(scores.expected_edge_after_friction_pct, 0) ?? 0,
      expected_option_return_pct_model: asNumber(scores.expected_option_return_pct_model, 0) ?? 0,
      path_holding_quality_score: asNumber(scores.path_holding_quality_score, 0) ?? 0,
      path_early_profit_take_prob: asNumber(scores.path_early_profit_take_prob, 0) ?? 0,
      path_decay_risk: asNumber(scores.path_decay_risk, 0) ?? 0,
      scout_call_edge_prob: asNumber(risk.scout_call_edge_prob, 0) ?? 0,
      scout_put_edge_prob: asNumber(risk.scout_put_edge_prob, 0) ?? 0,
      scout_no_trade_prob: asNumber(risk.scout_no_trade_prob, 0) ?? 0,
      sentinel_confidence: asNumber(risk.sentinel_confidence, 0) ?? 0,
      sentinel_call_relevance: asNumber(risk.sentinel_call_relevance, 0) ?? 0,
      sentinel_put_relevance: asNumber(risk.sentinel_put_relevance, 0) ?? 0,
      sentinel_no_trade_relevance: asNumber(risk.sentinel_no_trade_relevance, 1) ?? 1,
      delta: Math.abs(asNumber(risk.delta, 0) ?? 0),
      implied_volatility: asNumber(risk.implied_volatility, 0) ?? 0,
      iv_rank: asNumber(risk.iv_rank, 0) ?? 0,
      moneyness: asNumber(risk.moneyness, 0) ?? 0,
      premium_pct_of_spot: asNumber(risk.premium_pct_of_spot, 0) ?? 0,
      breakeven_move_pct: asNumber(risk.breakeven_move_pct, 0) ?? 0,
      projected_move_pct: asNumber(risk.projected_move_pct, 0) ?? 0,
      extrinsic_ratio: asNumber(risk.extrinsic_ratio, 0) ?? 0,
      realized_vol_20d: asNumber(risk.realized_vol_20d, 0) ?? 0,
      atr_pct_14d: asNumber(risk.atr_pct_14d, 0) ?? 0,
      regime_is_risk_on: regimeMode === "risk_on" ? 1 : 0,
      regime_is_risk_off: regimeMode === "risk_off" ? 1 : 0,
    },
  };
}

export function scoreExitHeads(model, featureMap) {
  const heads = model?.heads;
  if (!heads || !featureMap) {
    return null;
  }
  const scored = {};
  for (const [name, head] of Object.entries(heads)) {
    if (head?.kind === "constant") {
      scored[name] = clamp(asNumber(head.probability, 0.5), 0, 1);
      continue;
    }
    let margin = asNumber(head?.intercept, 0) ?? 0;
    for (const featureName of model.feature_names || []) {
      const value = asNumber(featureMap[featureName], 0) ?? 0;
      const mean = asNumber(head?.scaler_mean?.[featureName], 0) ?? 0;
      const scale = asNumber(head?.scaler_scale?.[featureName], 1) || 1;
      const weight = asNumber(head?.coefficients?.[featureName], 0) ?? 0;
      margin += ((value - mean) / scale) * weight;
    }
    scored[name] = clamp(sigmoid(margin), 0, 1);
  }
  return scored;
}

export function applyLiveStateOverlays(baseScores, view) {
  const scores = {
    hold: clamp(asNumber(baseScores?.hold, 0.5), 0, 1),
    harvest: clamp(asNumber(baseScores?.harvest, 0.5), 0, 1),
    sell: clamp(asNumber(baseScores?.sell, 0.5), 0, 1),
  };
  const openPlPct = asNumber(view?.open_pl_pct, null);
  const dte = asNumber(view?.dte, null);
  const spreadPct = asNumber(view?.spread_pct, null);

  if (openPlPct !== null) {
    if (openPlPct >= 0.4) {
      scores.harvest += 0.28;
      scores.hold -= 0.05;
    } else if (openPlPct >= 0.25) {
      scores.harvest += 0.16;
    } else if (openPlPct <= -0.5) {
      scores.sell += 0.42;
      scores.hold -= 0.12;
    } else if (openPlPct <= -0.3) {
      scores.sell += 0.18;
      scores.hold -= 0.06;
    } else if (openPlPct >= -0.05 && openPlPct <= 0.15) {
      scores.hold += 0.08;
    }
  }

  if (dte !== null) {
    if (dte <= 1) {
      scores.sell += 0.25;
      scores.hold -= 0.12;
    } else if (dte <= 3) {
      scores.sell += 0.12;
      scores.hold -= 0.08;
      if (openPlPct !== null && openPlPct > 0.1) {
        scores.harvest += 0.10;
      }
    } else if (dte >= 7) {
      scores.hold += 0.06;
    }
  }

  if (view?.regime_aligned === false) {
    scores.sell += 0.10;
    scores.hold -= 0.05;
  } else if (view?.regime_aligned === true) {
    scores.hold += 0.06;
  }

  if (spreadPct !== null && spreadPct >= 0.25) {
    scores.sell += 0.08;
  }

  return {
    hold: clamp(scores.hold, 0, 1),
    harvest: clamp(scores.harvest, 0, 1),
    sell: clamp(scores.sell, 0, 1),
  };
}

export function buildPositionExitAdvice({ model, view, reference, candidate, lane }) {
  const featureBundle = buildExitFeatureMap({ view, reference, candidate, lane });
  if (!featureBundle) {
    return null;
  }
  const baseScores = scoreExitHeads(model, featureBundle.features);
  if (!baseScores) {
    return null;
  }
  const scores = applyLiveStateOverlays(baseScores, view);
  const policy = model?.policy || {};
  const openPlPct = asNumber(view?.open_pl_pct, null);
  const dte = asNumber(view?.dte, null);
  const riskFlags = [];
  let action = "hold";
  let urgency = "low";
  let thesisStatus = "intact";
  let headline = "Hold with the thesis";
  let rationale =
    "The position still has enough runway relative to its entry profile and current state to justify holding for now.";
  let exitStyle = "hold";

  const sellMinProb = asNumber(policy.sell_head_min_prob, 0.55) ?? 0.55;
  const sellLossOverridePct = asNumber(policy.sell_head_loss_override_pct, -0.3) ?? -0.3;
  const harvestMinProb = asNumber(policy.profit_harvest_head_min_prob, 0.6) ?? 0.6;
  const harvestMinOpenPl = asNumber(policy.profit_harvest_min_open_pl_pct, 0.15) ?? 0.15;
  const strongProfitOpenPl = Math.max(harvestMinOpenPl, 0.25);
  const harvestSignal =
    openPlPct !== null &&
    openPlPct >= harvestMinOpenPl &&
    (
      (scores.harvest >= harvestMinProb && scores.harvest >= scores.hold) ||
      (openPlPct >= strongProfitOpenPl && scores.harvest >= 0.5 && scores.sell < scores.harvest + 0.1)
    );

  if (
    harvestSignal &&
    scores.harvest >= scores.sell - 0.05
  ) {
    action = "sell";
    urgency = dte !== null && dte <= 3 ? "high" : "medium";
    thesisStatus = "mature";
    headline = "Harvest the open gain";
    rationale =
      "The exit model sees a credible profit-taking setup and the position is already green. Locking in the move is more attractive than donating it back to reversal or short-dated decay.";
    exitStyle = "profit_harvest";
    riskFlags.push("profit_capture", "exit_model_harvest");
    if (dte !== null && dte <= 3) {
      riskFlags.push("theta_decay");
    }
  } else if (
    scores.sell >= sellMinProb ||
    (openPlPct !== null && openPlPct <= sellLossOverridePct && scores.sell >= 0.45)
  ) {
    action = "sell";
    urgency = dte !== null && dte <= 3 ? "high" : "medium";
    thesisStatus = openPlPct !== null && openPlPct <= -0.25 ? "broken" : "drifting";
    headline = "Exit on downside pressure";
    rationale =
      "The exit model sees more downside and decay risk than remaining hold value. Current PnL, time left, and regime context all argue for reducing exposure now.";
    exitStyle = "risk_exit";
    riskFlags.push("exit_model_sell", "decay_risk");
    if (view?.regime_aligned === false) {
      riskFlags.push("regime_mismatch");
    }
    if (openPlPct !== null && openPlPct <= -0.25) {
      riskFlags.push("drawdown");
    }
  } else {
    action = "hold";
    urgency = dte !== null && dte <= 3 ? "medium" : "low";
    thesisStatus = view?.regime_aligned === false ? "mixed" : "intact";
    headline = "Hold with disciplined review";
    rationale =
      "The hold head remains competitive against the exit heads, so the position still earns more time. Keep reassessing if time-to-expiry compresses or the regime keeps drifting away from the option side.";
    exitStyle = "hold";
    riskFlags.push("exit_model_hold");
    if (dte !== null && dte <= 3) {
      riskFlags.push("theta_decay");
    }
    if (view?.regime_aligned === false) {
      riskFlags.push("regime_mismatch");
    }
  }

  return {
    advice: {
      action,
      confidence: roundScore(Math.max(scores.hold, scores.harvest, scores.sell)),
      urgency,
      headline,
      rationale,
      thesis_status: thesisStatus,
      risk_flags: [...new Set(riskFlags)].slice(0, 4),
    },
    model_scores: {
      hold: roundScore(scores.hold),
      harvest: roundScore(scores.harvest),
      sell: roundScore(scores.sell),
      base_hold: roundScore(baseScores.hold),
      base_harvest: roundScore(baseScores.harvest),
      base_sell: roundScore(baseScores.sell),
    },
    exit_style: exitStyle,
    reference: featureBundle.reference,
  };
}

function roundScore(value) {
  return Number(clamp(value, 0, 1).toFixed(4));
}
