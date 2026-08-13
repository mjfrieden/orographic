import {
  buildOrderProvenanceEvent,
  safeRecordOrderProvenance,
} from "../../_lib/order_ledger.js";
import {
  buildEligibility,
  buildOrderEnvelope,
  buildSubmissionPreview,
  buildSpreadExecutionBlock,
  describeSnapshot,
  fetchOptionQuote,
  findCandidate,
  getTradierSettings,
  jsonResponse,
  loadLatestSnapshot,
  previewOrPlaceOrder,
  requireSession,
  validateEntryRiskBudget,
  validateSubmission,
} from "../../_lib/tradier.js";

/**
 * POST /api/tradier/orders
 *
 * Preview or place a Tradier option order.
 *
 * Body:
 *   preview (bool, required)     – true = preview only, false = place live/sandbox order
 *   option_symbol (string)       – OCC option symbol, e.g. AAPL250411C00185000
 *   symbol (string)              – underlying equity symbol
 *   side (string)                – "buy_to_open" or "sell_to_close"
 *   quantity (int)               – number of option contracts (capped by env config)
 *   type (string)                – "limit" only
 *   duration (string)            – "day" or "gtc"
 *   price (number)               – limit price
 *   confirm_live (bool)          – must be true for live (non-sandbox) order placement
 *
 * Preview: authenticated session; new entries must still be on a fresh Council
 * live board. Placement: admin-only. Position-closing previews and submissions
 * remain available even when today's recommendation has changed.
 */

function buildRequestEnvelope({
  candidate,
  optionSymbol,
  underlyingSymbol,
  side,
  quantity,
  orderType,
  duration,
  price,
  config,
}) {
  const maxContracts = Math.max(1, Number(config?.maxContracts || 1));
  const parsedQuantity = Number.parseInt(String(quantity ?? 1), 10);
  return {
    class: "option",
    symbol: String(candidate?.symbol || underlyingSymbol || "")
      .trim()
      .toUpperCase(),
    option_symbol: String(candidate?.contract_symbol || optionSymbol || "")
      .trim()
      .toUpperCase(),
    side,
    quantity: Math.min(Math.max(Number.isFinite(parsedQuantity) ? parsedQuantity : 1, 1), maxContracts),
    type: orderType,
    duration,
    price: Number(price || 0).toFixed(2),
    tag: `orographic-${String(config?.mode || "disabled")}-${String(candidate?.symbol || underlyingSymbol || "manual").toLowerCase()}`,
  };
}

async function recordBlockedAttempt({
  context,
  eventType,
  config,
  session,
  snapshot,
  snapshotInfo,
  lane,
  candidate,
  optionSymbol,
  underlyingSymbol,
  side,
  quantity,
  orderType,
  duration,
  price,
  requestedExitPolicyAction,
  blockReason,
  httpStatus,
  error,
}) {
  const envelope = buildRequestEnvelope({
    candidate,
    optionSymbol,
    underlyingSymbol,
    side,
    quantity,
    orderType,
    duration,
    price,
    config,
  });
  return safeRecordOrderProvenance(
    context.env,
    buildOrderProvenanceEvent({
      eventType,
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      quote: null,
      envelope,
      result: null,
      exitPolicyAction: requestedExitPolicyAction,
      blockReason,
      httpStatus,
      error,
    }),
  );
}

export async function onRequestPost(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }
  const { session } = auth;

  let body;
  try {
    body = await context.request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Request body must be valid JSON." }, 400);
  }

  const {
    preview: isPreview,
    option_symbol: optionSymbol,
    symbol: underlyingSymbol,
    side = "buy_to_open",
    quantity = 1,
    type: orderType = "limit",
    duration = "day",
    price,
    confirm_live: confirmLive,
  } = body || {};
  const requestedExitPolicyAction = String(
    body?.exit_policy_action || "",
  ).trim();

  if (!optionSymbol) {
    return jsonResponse({ ok: false, error: "option_symbol is required." }, 400);
  }
  if (!price || Number(price) <= 0) {
    return jsonResponse({ ok: false, error: "A positive limit price is required." }, 400);
  }
  if (side !== "buy_to_open" && side !== "sell_to_close") {
    return jsonResponse({ ok: false, error: "Only buy_to_open and sell_to_close are supported." }, 400);
  }
  if (orderType !== "limit") {
    return jsonResponse({ ok: false, error: "Only limit orders are supported." }, 400);
  }

  const config = getTradierSettings(context.env);
  if (!config.configured) {
    return jsonResponse(
      {
        ok: false,
        error: "Tradier is not configured. Set TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID.",
        broker: { configured: false },
      },
      503,
    );
  }

  // Load snapshot for candidate validation and freshness check
  let snapshot = null;
  let snapshotInfo = null;
  try {
    snapshot = await loadLatestSnapshot(context);
    snapshotInfo = describeSnapshot(snapshot, config.maxSignalAgeMinutes);
  } catch {
    snapshotInfo = { is_fresh: false, reason: "Could not load signal snapshot." };
  }

  // Locate the candidate in the snapshot so we know its lane
  const found = snapshot ? findCandidate(snapshot, optionSymbol) : null;
  const { lane = "unknown", candidate = null } = found || {};

  const eligibility = buildEligibility({ config, lane, snapshotInfo });
  const submission = buildSubmissionPreview({
    config,
    session,
    lane,
    snapshotInfo,
    side,
  });
  const spreadBlock = buildSpreadExecutionBlock(candidate, side);
  if (spreadBlock) {
    const provenance = await recordBlockedAttempt({
      context,
      eventType: "blocked_spread",
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      optionSymbol,
      underlyingSymbol,
      side,
      quantity,
      orderType,
      duration,
      price,
      requestedExitPolicyAction,
      blockReason: "spread_execution_block",
      httpStatus: spreadBlock.status,
      error: spreadBlock.error,
    });
    return jsonResponse(
      {
        ok: false,
        error: spreadBlock.error,
        spread: spreadBlock.spread,
        eligibility,
        submission,
        provenance,
      },
      spreadBlock.status,
    );
  }

  // ----- PREVIEW path (any authenticated user) -----
  if (isPreview) {
    const previewValidation = validateSubmission({
      config,
      session,
      lane,
      snapshotInfo,
      side,
      requireAdmin: false,
    });
    if (!previewValidation.ok) {
      const provenance = await recordBlockedAttempt({
        context,
        eventType: "blocked_preview_validation",
        config,
        session,
        snapshot,
        snapshotInfo,
        lane,
        candidate,
        optionSymbol,
        underlyingSymbol,
        side,
        quantity,
        orderType,
        duration,
        price,
        requestedExitPolicyAction,
        blockReason: previewValidation.error,
        httpStatus: previewValidation.status,
        error: previewValidation.error,
      });
      return jsonResponse(
        { ok: false, error: previewValidation.error, eligibility, submission, provenance },
        previewValidation.status,
      );
    }
    // Fetch a live quote so the preview price is fresh
    let liveQuote = null;
    try {
      const quoteResult = await fetchOptionQuote(config, optionSymbol);
      liveQuote = quoteResult.quote;
    } catch {
      liveQuote = null;
    }
    const quoteCapturedAtUtc = liveQuote ? new Date().toISOString() : null;

    const envelope = buildOrderEnvelope(
      candidate || { symbol: underlyingSymbol, contract_symbol: optionSymbol },
      quantity,
      config,
      liveQuote,
      side
    );
    const riskBudget = validateEntryRiskBudget({ config, envelope, side });
    if (!riskBudget.ok) {
      const provenance = await recordBlockedAttempt({
        context,
        eventType: "blocked_risk_budget",
        config,
        session,
        snapshot,
        snapshotInfo,
        lane,
        candidate,
        optionSymbol,
        underlyingSymbol,
        side,
        quantity: envelope.quantity,
        orderType,
        duration,
        price: envelope.price,
        requestedExitPolicyAction,
        blockReason: "entry_cost_basis_limit",
        httpStatus: riskBudget.status,
        error: riskBudget.error,
      });
      return jsonResponse(
        { ok: false, error: riskBudget.error, risk_budget: riskBudget, eligibility, submission, provenance },
        riskBudget.status,
      );
    }

    const brokerRequestedAtUtc = new Date().toISOString();
    try {
      const result = await previewOrPlaceOrder(context.env, envelope, { preview: true });
      const brokerResponseAtUtc = new Date().toISOString();
      const provenance = await safeRecordOrderProvenance(
        context.env,
        buildOrderProvenanceEvent({
          eventType: "preview",
          config,
          session,
          snapshot,
          snapshotInfo,
          lane,
          candidate,
          quote: liveQuote,
          envelope,
          result,
          exitPolicyAction: requestedExitPolicyAction,
          executionTiming: {
            quote_captured_at_utc: quoteCapturedAtUtc,
            broker_requested_at_utc: brokerRequestedAtUtc,
            broker_response_at_utc: brokerResponseAtUtc,
          },
        }),
      );
      return jsonResponse({
        ok: true,
        preview: true,
        order: result.order,
        envelope,
        eligibility,
        submission,
        provenance,
        rate_limits: result.rateLimits,
      });
    } catch (error) {
      const provenance = await safeRecordOrderProvenance(
        context.env,
        buildOrderProvenanceEvent({
          eventType: "preview_error",
          config,
          session,
          snapshot,
          snapshotInfo,
          lane,
          candidate,
          quote: liveQuote,
          envelope,
          result: null,
          exitPolicyAction: requestedExitPolicyAction,
          error: String(error.message || error),
          executionTiming: {
            quote_captured_at_utc: quoteCapturedAtUtc,
            broker_requested_at_utc: brokerRequestedAtUtc,
            broker_response_at_utc: new Date().toISOString(),
          },
        }),
      );
      return jsonResponse(
        { ok: false, error: String(error.message || error), eligibility, provenance },
        502,
      );
    }
  }

  // ----- LIVE/SANDBOX PLACEMENT path (admin-only) -----
  const validation = validateSubmission({
    config,
    session,
    lane,
    snapshotInfo,
    side,
  });
  if (!validation.ok) {
    const provenance = await recordBlockedAttempt({
      context,
      eventType: "blocked_validation",
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      optionSymbol,
      underlyingSymbol,
      side,
      quantity,
      orderType,
      duration,
      price,
      requestedExitPolicyAction,
      blockReason: validation.error,
      httpStatus: validation.status,
      error: validation.error,
    });
    return jsonResponse(
      { ok: false, error: validation.error, eligibility, submission, provenance },
      validation.status,
    );
  }

  // Live mode requires explicit confirm_live flag from the client
  if (config.mode === "live" && !confirmLive) {
    const provenance = await recordBlockedAttempt({
      context,
      eventType: "blocked_live_confirmation",
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      optionSymbol,
      underlyingSymbol,
      side,
      quantity,
      orderType,
      duration,
      price,
      requestedExitPolicyAction,
      blockReason: "missing_live_confirmation",
      httpStatus: 409,
      error: "Live order blocked: confirm_live must be true for live-mode placement.",
    });
    return jsonResponse(
      {
        ok: false,
        error: "Live order blocked: confirm_live must be true for live-mode placement.",
        eligibility,
        submission,
        provenance,
      },
      409,
    );
  }

  if (config.mode === "live" && !config.liveTradingEnabled) {
    const provenance = await recordBlockedAttempt({
      context,
      eventType: "blocked_live_disabled",
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      optionSymbol,
      underlyingSymbol,
      side,
      quantity,
      orderType,
      duration,
      price,
      requestedExitPolicyAction,
      blockReason: "live_trading_disabled",
      httpStatus: 412,
      error: "Live trading is not enabled. Set TRADIER_LIVE_TRADING_ENABLED=true to arm live orders.",
    });
    return jsonResponse(
      {
        ok: false,
        error: "Live trading is not enabled. Set TRADIER_LIVE_TRADING_ENABLED=true to arm live orders.",
        eligibility,
        submission,
        provenance,
      },
      412,
    );
  }

  // Fetch live quote for order pricing
  let liveQuote = null;
  try {
    const quoteResult = await fetchOptionQuote(config, optionSymbol);
    liveQuote = quoteResult.quote;
  } catch {
    liveQuote = null;
  }
  const quoteCapturedAtUtc = liveQuote ? new Date().toISOString() : null;

  const envelope = buildOrderEnvelope(
    candidate || { symbol: underlyingSymbol, contract_symbol: optionSymbol },
    quantity,
    config,
    liveQuote,
    side
  );
  const riskBudget = validateEntryRiskBudget({ config, envelope, side });
  if (!riskBudget.ok) {
    const provenance = await recordBlockedAttempt({
      context,
      eventType: "blocked_risk_budget",
      config,
      session,
      snapshot,
      snapshotInfo,
      lane,
      candidate,
      optionSymbol,
      underlyingSymbol,
      side,
      quantity: envelope.quantity,
      orderType,
      duration,
      price: envelope.price,
      requestedExitPolicyAction,
      blockReason: "entry_cost_basis_limit",
      httpStatus: riskBudget.status,
      error: riskBudget.error,
    });
    return jsonResponse(
      { ok: false, error: riskBudget.error, risk_budget: riskBudget, eligibility, submission, provenance },
      riskBudget.status,
    );
  }

  const brokerRequestedAtUtc = new Date().toISOString();
  try {
    const result = await previewOrPlaceOrder(context.env, envelope, { preview: false });
    const brokerResponseAtUtc = new Date().toISOString();
    const provenance = await safeRecordOrderProvenance(
      context.env,
      buildOrderProvenanceEvent({
        eventType: "submit",
        config,
        session,
        snapshot,
        snapshotInfo,
        lane,
        candidate,
        quote: liveQuote,
        envelope,
        result,
        exitPolicyAction: requestedExitPolicyAction,
        executionTiming: {
          quote_captured_at_utc: quoteCapturedAtUtc,
          broker_requested_at_utc: brokerRequestedAtUtc,
          broker_response_at_utc: brokerResponseAtUtc,
        },
      }),
    );
    return jsonResponse({
      ok: true,
      preview: false,
      order: result.order,
      confirmation: result.confirmation,
      envelope,
      eligibility,
      submission,
      provenance,
      rate_limits: result.rateLimits,
    });
  } catch (error) {
    const provenance = await safeRecordOrderProvenance(
      context.env,
      buildOrderProvenanceEvent({
        eventType: "submit_error",
        config,
        session,
        snapshot,
        snapshotInfo,
        lane,
        candidate,
        quote: liveQuote,
        envelope,
        result: null,
        exitPolicyAction: requestedExitPolicyAction,
        error: String(error.message || error),
        executionTiming: {
          quote_captured_at_utc: quoteCapturedAtUtc,
          broker_requested_at_utc: brokerRequestedAtUtc,
          broker_response_at_utc: new Date().toISOString(),
        },
      }),
    );
    return jsonResponse(
      { ok: false, error: String(error.message || error), eligibility, provenance },
      502,
    );
  }
}
