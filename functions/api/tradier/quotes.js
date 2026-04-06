import {
  fetchQuotes,
  jsonResponse,
  requireSession,
} from "../../_lib/tradier.js";

/**
 * GET /api/tradier/quotes?symbols=AAPL250411C00185000,...
 *
 * Batch option quote refresh for the current board's contract symbols.
 * Includes greeks (delta, iv) when available from Tradier response.
 * Requires an authenticated session (any role).
 */
export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }

  const url = new URL(context.request.url);
  const raw = (url.searchParams.get("symbols") || "").trim();
  if (!raw) {
    return jsonResponse({ ok: false, error: "symbols parameter is required." }, 400);
  }

  const symbols = raw
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);

  if (!symbols.length) {
    return jsonResponse({ ok: false, error: "No valid symbols provided." }, 400);
  }

  // Cap to prevent runaway requests
  if (symbols.length > 12) {
    return jsonResponse({ ok: false, error: "Maximum 12 symbols per request." }, 400);
  }

  try {
    const result = await fetchQuotes(context.env, symbols);
    return jsonResponse({
      ok: true,
      quotes: result.quotes,
      rate_limits: result.rateLimits,
    });
  } catch (error) {
    return jsonResponse(
      {
        ok: false,
        error: String(error.message || error),
      },
      502,
    );
  }
}
