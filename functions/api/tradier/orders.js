import {
  buildEligibility,
  buildOrderEnvelope,
  buildSubmissionPreview,
  describeSnapshot,
  fetchOptionQuote,
  findCandidate,
  getTradierSettings,
  jsonResponse,
  loadLatestSnapshot,
  previewOrPlaceOrder,
  requireSession,
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
 * Preview: any authenticated session, no snapshot freshness gate.
 * Placement: admin-only. New entries require a fresh snapshot and, in live mode,
 * must still be on the live board. Manual exits stay available even if the
 * snapshot has gone stale.
 */
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

  // ----- PREVIEW path (any authenticated user) -----
  if (isPreview) {
    // Fetch a live quote so the preview price is fresh
    let liveQuote = null;
    try {
      const quoteResult = await fetchOptionQuote(config, optionSymbol);
      liveQuote = quoteResult.quote;
    } catch {
      liveQuote = null;
    }

    const envelope = buildOrderEnvelope(
      candidate || { symbol: underlyingSymbol, contract_symbol: optionSymbol },
      quantity,
      config,
      liveQuote,
      side
    );

    try {
      const result = await previewOrPlaceOrder(context.env, envelope, { preview: true });
      return jsonResponse({
        ok: true,
        preview: true,
        order: result.order,
        envelope,
        eligibility,
        submission,
        rate_limits: result.rateLimits,
      });
    } catch (error) {
      return jsonResponse(
        { ok: false, error: String(error.message || error), eligibility },
        502,
      );
    }
  }

  // ----- LIVE/SANDBOX PLACEMENT path (admin-only) -----
  const validation = validateSubmission({ config, session, lane, snapshotInfo, side });
  if (!validation.ok) {
    return jsonResponse(
      { ok: false, error: validation.error, eligibility, submission },
      validation.status,
    );
  }

  // Live mode requires explicit confirm_live flag from the client
  if (config.mode === "live" && !confirmLive) {
    return jsonResponse(
      {
        ok: false,
        error: "Live order blocked: confirm_live must be true for live-mode placement.",
        eligibility,
        submission,
      },
      409,
    );
  }

  if (config.mode === "live" && !config.liveTradingEnabled) {
    return jsonResponse(
      {
        ok: false,
        error: "Live trading is not enabled. Set TRADIER_LIVE_TRADING_ENABLED=true to arm live orders.",
        eligibility,
        submission,
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

  const envelope = buildOrderEnvelope(
    candidate || { symbol: underlyingSymbol, contract_symbol: optionSymbol },
    quantity,
    config,
    liveQuote,
    side
  );

  try {
    const result = await previewOrPlaceOrder(context.env, envelope, { preview: false });
    return jsonResponse({
      ok: true,
      preview: false,
      order: result.order,
      confirmation: result.confirmation,
      envelope,
      eligibility,
      submission,
      rate_limits: result.rateLimits,
    });
  } catch (error) {
    return jsonResponse(
      { ok: false, error: String(error.message || error), eligibility },
      502,
    );
  }
}
