import { requireSession, jsonResponse } from "../../_lib/tradier.js";

/**
 * GET /api/backtest/summary
 *
 * Serves the preferred pre-computed validation artifact.
 * Walk-forward results are preferred when present, with backtest results as fallback.
 */
export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) return auth.response;

  try {
    const assetFetch = context.env?.ASSETS?.fetch?.bind(context.env.ASSETS);
    const candidates = [
      {
        path: "/data/walk_forward_results.json",
        kind: "walk_forward",
      },
      {
        path: "/data/backtest_results.json",
        kind: "backtest",
      },
    ];

    for (const candidate of candidates) {
      const url = new URL(candidate.path, context.request.url);
      const response = assetFetch
        ? await assetFetch(new Request(url.toString()))
        : await fetch(url.toString());
      if (!response.ok) {
        continue;
      }
      const data = await response.json();
      return jsonResponse({
        ok: true,
        backtest: data,
        kind: candidate.kind,
      });
    }

    return jsonResponse(
      {
        ok: false,
        error:
          "Validation results not yet generated. Add walk-forward or backtest artifacts first.",
      },
      404,
    );
  } catch (error) {
    return jsonResponse({ ok: false, error: String(error.message || error) }, 500);
  }
}
