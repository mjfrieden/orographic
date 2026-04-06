import { requireSession, jsonResponse } from "../../_lib/tradier.js";

/**
 * GET /api/backtest/summary
 *
 * Serves the pre-computed backtest_results.json written by the Python runner.
 * Public read (viewer role) — no admin required.
 */
export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) return auth.response;

  try {
    const url = new URL("/data/backtest_results.json", context.request.url);
    const assetFetch = context.env?.ASSETS?.fetch?.bind(context.env.ASSETS);
    const response = assetFetch
      ? await assetFetch(new Request(url.toString()))
      : await fetch(url.toString());

    if (!response.ok) {
      return jsonResponse(
        { ok: false, error: "Backtest results not yet generated. Run the backtest engine first." },
        404,
      );
    }

    const data = await response.json();
    return jsonResponse({ ok: true, backtest: data });
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error.message || error) }, 500);
  }
}
