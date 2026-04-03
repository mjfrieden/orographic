import {
  fetchQuotes,
  getTradierSettings,
  jsonResponse,
  publicTradierConfig,
  requireSession,
} from "../../_lib/tradier.js";

function parseSymbols(request) {
  const url = new URL(request.url);
  return String(url.searchParams.get("symbols") || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
    .slice(0, 8);
}

export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }

  const settings = getTradierSettings(context.env);
  if (!settings.configured) {
    return jsonResponse({
      ok: true,
      broker: publicTradierConfig(settings),
      quotes: [],
      rateLimits: null,
    });
  }

  const symbols = parseSymbols(context.request);
  if (!symbols.length) {
    return jsonResponse(
      { ok: false, error: "At least one symbol is required." },
      400,
    );
  }

  try {
    const payload = await fetchQuotes(context.env, symbols);
    return jsonResponse({
      ok: true,
      broker: publicTradierConfig(settings),
      quotes: payload.quotes,
      rateLimits: payload.rateLimits,
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
