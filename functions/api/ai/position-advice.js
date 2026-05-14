import {
  fetchQuotes,
  findCandidate,
  jsonResponse,
  loadLatestSnapshot,
  requireSession,
} from "../../_lib/tradier.js";

const AI_MODEL = "@cf/meta/llama-3-8b-instruct";

export async function onRequestPost(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }

  let body;
  try {
    body = await context.request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Request body must be valid JSON." }, 400);
  }

  const position = normalizePosition(body?.position);
  if (!position.symbol) {
    return jsonResponse({ ok: false, error: "position with symbol is required." }, 400);
  }

  const contract = parseOptionContract(position.symbol);
  if (!contract.is_option) {
    return jsonResponse({
      ok: true,
      advice: {
        action: "hold",
        confidence: 0,
        urgency: "low",
        headline: "Equity position",
        rationale: "Exit AI currently evaluates option contracts only.",
        thesis_status: "unknown",
        risk_flags: ["non_option_position"],
      },
      ai_model: "rule-based-fallback",
      scoring_role: "exit_advice_only",
    });
  }

  const snapshot = await safeLoadSnapshot(context);
  const found = snapshot ? findCandidate(snapshot, position.symbol) : null;
  const regime = snapshot?.regime || null;
  const liveQuote = await safeFetchQuote(context.env, position.symbol);
  const quote = normalizeQuote(liveQuote);

  const view = buildAdviceView({
    position,
    contract,
    quote,
    candidate: found?.candidate || null,
    lane: found?.lane || "untracked",
    regime,
  });

  if (view.open_pl_pct !== null && view.open_pl_pct <= -0.5) {
    return jsonResponse({
      ok: true,
      advice: buildFallbackAdvice(view),
      ai_model: "mechanical-policy",
      scoring_role: "exit_advice_only",
    });
  }

  if (!context.env?.AI) {
    return jsonResponse({
      ok: true,
      advice: buildFallbackAdvice(view),
      ai_model: "rule-based-fallback",
      scoring_role: "exit_advice_only",
    });
  }

  try {
    const response = await context.env.AI.run(AI_MODEL, {
      messages: [
        {
          role: "system",
          content:
            "You are an options exit-risk assistant for existing long option positions. " +
            "Choose HOLD only when the thesis still looks intact enough to justify more time risk. " +
            "Choose SELL when time decay, regime drift, weak live pricing, or realized P&L make the hold unattractive. " +
            "Be conservative with near-dated long premium. Output only raw JSON.",
        },
        {
          role: "user",
          content: buildPrompt(view),
        },
      ],
      max_tokens: 220,
      temperature: 0.2,
    });

    const parsed = parseAiAdvice(response?.response);
    return jsonResponse({
      ok: true,
      advice: parsed || buildFallbackAdvice(view),
      ai_model: parsed ? AI_MODEL : "rule-based-fallback",
      scoring_role: "exit_advice_only",
      raw: parsed ? undefined : response?.response,
    });
  } catch (error) {
    return jsonResponse({
      ok: true,
      advice: buildFallbackAdvice(view),
      ai_model: "rule-based-fallback",
      scoring_role: "exit_advice_only",
      ai_error: String(error.message || error),
    });
  }
}

async function safeLoadSnapshot(context) {
  try {
    return await loadLatestSnapshot(context);
  } catch {
    return null;
  }
}

async function safeFetchQuote(env, symbol) {
  try {
    const result = await fetchQuotes(env, [symbol]);
    return Array.isArray(result?.quotes) ? result.quotes[0] || null : null;
  } catch {
    return null;
  }
}

function normalizePosition(position) {
  const row = position && typeof position === "object" ? position : {};
  return {
    symbol: String(row.symbol || "")
      .trim()
      .toUpperCase(),
    quantity: asNumber(row.quantity, 0),
    cost_basis: asNumber(row.cost_basis, null),
    current_value: asNumber(row.current_value, null),
    open_pl: asNumber(row.open_pl, null),
    mark_price: asNumber(row.mark_price, null),
    date_acquired: String(row.date_acquired || ""),
    mark_source: String(row.mark_source || ""),
    current_value_source: String(row.current_value_source || ""),
  };
}

function normalizeQuote(quote) {
  if (!quote || typeof quote !== "object") {
    return null;
  }
  return {
    symbol: String(quote.symbol || "")
      .trim()
      .toUpperCase(),
    bid: asNumber(quote.bid, null),
    ask: asNumber(quote.ask, null),
    last: asNumber(quote.last, null),
    close: asNumber(quote.close, null),
    underlying: String(quote.underlying || "")
      .trim()
      .toUpperCase(),
    open_interest: asNumber(quote.open_interest, null),
    volume: asNumber(quote.volume, null),
    greeks: quote.greeks || null,
  };
}

function parseOptionContract(symbol) {
  const text = String(symbol || "")
    .trim()
    .toUpperCase();
  const match = text.match(/^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$/);
  if (!match) {
    return {
      symbol: text,
      root: text,
      is_option: false,
      option_type: null,
      expiry: null,
      strike: null,
    };
  }

  const [, root, yy, mm, dd, side, strikeRaw] = match;
  const expiry = `20${yy}-${mm}-${dd}`;
  return {
    symbol: text,
    root,
    is_option: true,
    option_type: side === "C" ? "call" : "put",
    expiry,
    strike: Number(strikeRaw) / 1000,
  };
}

function buildAdviceView({ position, contract, quote, candidate, lane, regime }) {
  const unitCost =
    position.cost_basis !== null && position.quantity > 0
      ? position.cost_basis / (position.quantity * 100)
      : null;
  const unitMark =
    quote?.bid ??
    position.mark_price ??
    (position.current_value !== null && position.quantity > 0
      ? position.current_value / (position.quantity * 100)
      : null);
  const openPl =
    position.open_pl !== null
      ? position.open_pl
      : position.current_value !== null && position.cost_basis !== null
        ? position.current_value - position.cost_basis
        : null;
  const plPct =
    openPl !== null && position.cost_basis
      ? openPl / position.cost_basis
      : null;
  const bid = quote?.bid ?? null;
  const ask = quote?.ask ?? null;
  const spreadPct =
    bid !== null && ask !== null && ask > 0
      ? (ask - bid) / ask
      : null;
  const dte = daysToExpiry(contract.expiry);
  const regimeMode = String(regime?.mode || "").toLowerCase();
  const aligned = regimeAligns(contract.option_type, regimeMode);

  return {
    symbol: contract.root,
    contract_symbol: contract.symbol,
    option_type: contract.option_type,
    expiry: contract.expiry,
    strike: contract.strike,
    quantity: position.quantity,
    date_acquired: position.date_acquired,
    dte,
    cost_basis: position.cost_basis,
    current_value: position.current_value,
    open_pl: openPl,
    open_pl_pct: plPct,
    mark_price: unitMark,
    bid,
    ask,
    last: quote?.last ?? null,
    spread_pct: spreadPct,
    mark_source: position.mark_source || position.current_value_source || null,
    underlying: quote?.underlying || contract.root,
    open_interest: quote?.open_interest ?? null,
    volume: quote?.volume ?? null,
    delta: asNumber(quote?.greeks?.delta, null),
    mid_iv: asNumber(quote?.greeks?.mid_iv, null),
    regime_mode: regimeMode || "unknown",
    regime_bias: asNumber(regime?.bias, null),
    regime_aligned: aligned,
    regime_notes: Array.isArray(regime?.notes) ? regime.notes : [],
    lane,
    candidate_found: Boolean(candidate),
    candidate_option_type: String(candidate?.option_type || "").toLowerCase() || null,
    candidate_forge_score: asNumber(candidate?.forge_score, null),
    candidate_expected_return_pct: asNumber(candidate?.expected_return_pct, null),
    candidate_edge_after_friction_pct: asNumber(
      candidate?.expected_edge_after_friction_pct,
      null,
    ),
    candidate_notes: Array.isArray(candidate?.notes) ? candidate.notes : [],
    candidate_risk_flags: Array.isArray(candidate?.council_risk_flags)
      ? candidate.council_risk_flags
      : [],
    entry_cost_per_contract: unitCost,
  };
}

function buildPrompt(view) {
  const quoteSummary = [
    `Bid ${numberOrUnknown(view.bid, 2)}`,
    `Ask ${numberOrUnknown(view.ask, 2)}`,
    `Last ${numberOrUnknown(view.last, 2)}`,
    `SpreadPct ${pctOrUnknown(view.spread_pct)}`,
    `Delta ${numberOrUnknown(view.delta, 2)}`,
    `MidIV ${pctOrUnknown(view.mid_iv)}`,
  ].join(" | ");

  const candidateSummary = view.candidate_found
    ? `Tracked by current snapshot in lane ${view.lane}. Forge ${numberOrUnknown(view.candidate_forge_score, 2)} | expected return ${pctOrUnknown(view.candidate_expected_return_pct)} | edge after friction ${pctOrUnknown(view.candidate_edge_after_friction_pct)} | notes ${listOrNone(view.candidate_notes)} | risk flags ${listOrNone(view.candidate_risk_flags)}`
    : "This exact contract is not on the current Council/Forge board snapshot.";

  return `You are advising whether to HOLD or SELL an already-open long option position.
Return only a compact JSON object with this exact schema:
{"action":"hold","confidence":0.73,"urgency":"medium","headline":"short title","rationale":"2 short sentences max","thesis_status":"intact","risk_flags":["theta_decay","regime_mismatch"]}

Decision rules:
- HOLD means the thesis still justifies carrying more decay and mark risk.
- SELL means the exit is more prudent right now.
- Favor SELL for near-expiry long premium, thesis drift, deep drawdowns, or when profits should be harvested.
- Keep rationale concrete and concise.

Position:
Underlying ${view.symbol}
Contract ${view.contract_symbol}
Type ${String(view.option_type || "").toUpperCase()}
Expiry ${view.expiry || "unknown"} | DTE ${numberOrUnknown(view.dte, 1)}
Strike ${numberOrUnknown(view.strike, 2)}
Qty ${numberOrUnknown(view.quantity, 0)}
Acquired ${view.date_acquired || "unknown"}
Entry cost/contract ${numberOrUnknown(view.entry_cost_per_contract, 2)}
Current mark/contract ${numberOrUnknown(view.mark_price, 2)}
Open PnL ${moneyOrUnknown(view.open_pl)} | Open PnL pct ${pctOrUnknown(view.open_pl_pct)}
Quote snapshot ${quoteSummary}
Mark source ${view.mark_source || "unknown"}
Open interest ${numberOrUnknown(view.open_interest, 0)} | Volume ${numberOrUnknown(view.volume, 0)}

Market context:
Regime ${view.regime_mode || "unknown"} | bias ${numberOrUnknown(view.regime_bias, 2)} | aligned ${view.regime_aligned ? "yes" : "no"}
Regime notes ${listOrNone(view.regime_notes)}

Original trade context:
${candidateSummary}`;
}

function parseAiAdvice(raw) {
  if (!raw || typeof raw !== "string") {
    return null;
  }
  let text = raw.trim();
  if (text.startsWith("```json")) {
    text = text.replace(/```json/gi, "").replace(/```/g, "").trim();
  } else if (text.startsWith("```")) {
    text = text.replace(/```/g, "").trim();
  }

  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return null;
  }

  const action = normalizeAction(parsed?.action);
  if (!action) {
    return null;
  }

  return {
    action,
    confidence: clamp(asNumber(parsed.confidence, 0.5), 0, 1),
    urgency: normalizeUrgency(parsed.urgency),
    headline: cleanText(parsed.headline || `${action === "sell" ? "Take the exit" : "Stay with the thesis"}`),
    rationale: cleanText(parsed.rationale || ""),
    thesis_status: normalizeThesis(parsed.thesis_status),
    risk_flags: normalizeFlags(parsed.risk_flags),
  };
}

function buildFallbackAdvice(view) {
  const flags = [];
  let action = "hold";
  let confidence = 0.58;
  let urgency = "low";
  let thesisStatus = "intact";
  let headline = "Hold for now";
  let rationale =
    "The live option still has enough runway relative to its current mark and regime context to justify patience.";

  if (view.open_pl_pct !== null && view.open_pl_pct <= -0.5) {
    action = "sell";
    confidence = 0.96;
    urgency = "high";
    thesisStatus = "broken";
    headline = "Mechanical stop reached";
    flags.push("mechanical_stop_50_pct", "drawdown");
    rationale =
      "The long option is down at least 50% from cost basis. The mechanical exit rule says to sell and preserve remaining capital.";
  } else if (view.dte !== null && view.dte <= 1) {
    action = "sell";
    confidence = 0.9;
    urgency = "high";
    thesisStatus = "degrading";
    headline = "Expiry risk dominates";
    flags.push("expiry_imminent", "theta_decay");
    rationale =
      "This long premium is at or near expiry, so remaining theta risk dominates the upside from waiting. Selling is the cleaner risk decision.";
  } else if (view.open_pl_pct !== null && view.open_pl_pct >= 0.4) {
    action = "sell";
    confidence = 0.78;
    urgency = view.dte !== null && view.dte <= 3 ? "high" : "medium";
    thesisStatus = "mature";
    headline = "Harvest the gain";
    flags.push("profit_capture");
    rationale =
      "The position has already delivered a sizable gain relative to capital at risk. Locking that in is more attractive than donating it back to decay or a reversal.";
  } else if (
    view.open_pl_pct !== null &&
    view.open_pl_pct <= -0.3 &&
    view.dte !== null &&
    view.dte <= 3
  ) {
    action = "sell";
    confidence = 0.82;
    urgency = "high";
    thesisStatus = "broken";
    headline = "Cut the weak weekly";
    flags.push("drawdown", "theta_decay");
    rationale =
      "The position is already in a meaningful drawdown and does not have much time left to recover. Preserving capital matters more than hoping for a late rescue.";
  } else if (view.regime_aligned === false && view.dte !== null && view.dte <= 3) {
    action = "sell";
    confidence = 0.74;
    urgency = "medium";
    thesisStatus = "drifting";
    headline = "Thesis no longer aligned";
    flags.push("regime_mismatch");
    rationale =
      "The current regime is no longer supportive of this option side, and there is limited time left for the thesis to realign. That combination weakens the case for holding.";
  } else {
    if (view.dte !== null && view.dte <= 3) {
      flags.push("theta_decay");
      confidence = 0.64;
      urgency = "medium";
      rationale =
        "The thesis is still defensible, but short-dated decay is now meaningful. Hold only while staying disciplined around the next refresh.";
    }
    if (view.spread_pct !== null && view.spread_pct >= 0.25) {
      flags.push("wide_spread");
    }
    if (view.regime_aligned === false) {
      flags.push("regime_mismatch");
      thesisStatus = "mixed";
      rationale =
        "The contract has not fully broken, but the market regime is less supportive than it was at entry. Holding is still reasonable only if you accept that the thesis is mixed.";
    }
  }

  return {
    action,
    confidence,
    urgency,
    headline,
    rationale,
    thesis_status: thesisStatus,
    risk_flags: flags,
  };
}

function daysToExpiry(expiry) {
  if (!expiry) {
    return null;
  }
  const expiryTs = Date.parse(`${expiry}T20:00:00Z`);
  if (Number.isNaN(expiryTs)) {
    return null;
  }
  const days = (expiryTs - Date.now()) / 86400000;
  return Number(days.toFixed(1));
}

function regimeAligns(optionType, regimeMode) {
  if (!optionType || !regimeMode) {
    return null;
  }
  if (optionType === "call") {
    return regimeMode === "risk_on";
  }
  if (optionType === "put") {
    return regimeMode === "risk_off";
  }
  return null;
}

function asNumber(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function clamp(value, low, high) {
  if (!Number.isFinite(value)) {
    return low;
  }
  return Math.max(low, Math.min(high, value));
}

function normalizeAction(value) {
  const text = String(value || "").trim().toLowerCase();
  return text === "hold" || text === "sell" ? text : null;
}

function normalizeUrgency(value) {
  const text = String(value || "").trim().toLowerCase();
  return text === "high" || text === "medium" ? text : "low";
}

function normalizeThesis(value) {
  const text = String(value || "").trim().toLowerCase();
  return ["intact", "mixed", "drifting", "degrading", "mature", "broken"].includes(text)
    ? text
    : "unknown";
}

function normalizeFlags(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((flag) =>
      String(flag || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_]+/g, "_"),
    )
    .filter(Boolean)
    .slice(0, 4);
}

function cleanText(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function listOrNone(values) {
  return Array.isArray(values) && values.length
    ? values.map((value) => cleanText(value)).filter(Boolean).join("; ")
    : "none";
}

function numberOrUnknown(value, digits = 2) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "unknown";
}

function pctOrUnknown(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(1)}%` : "unknown";
}

function moneyOrUnknown(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("en-US", { style: "currency", currency: "USD" })
    : "unknown";
}
