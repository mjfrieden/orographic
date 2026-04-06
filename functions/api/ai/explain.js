import { jsonResponse, requireSession } from "../../_lib/tradier.js";

/**
 * POST /api/ai/explain
 *
 * Generate a natural-language AI rationale for a Council-selected options contract.
 * Uses Cloudflare Workers AI (llama-3-8b-instruct) at the edge — no external API key.
 *
 * Body:
 *   candidate  – ContractCandidate object from the Council output
 *   regime     – MarketRegime object { mode, bias, source_symbol, notes }
 *
 * Returns:
 *   { ok, rationale: string }
 */
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

  const { candidate, regime } = body || {};
  if (!candidate || !candidate.symbol) {
    return jsonResponse({ ok: false, error: "candidate with symbol is required." }, 400);
  }

  // If Cloudflare AI binding is not available, fall back to a structured rule-based rationale
  if (!context.env?.AI) {
    const rationale = buildFallbackRationale(candidate, regime);
    return jsonResponse({ ok: true, rationale, ai_model: "rule-based-fallback" });
  }

  const prompt = buildPrompt(candidate, regime);

  try {
    const response = await context.env.AI.run("@cf/meta/llama-3-8b-instruct", {
      messages: [
        {
          role: "system",
          content:
            "You are a concise quantitative analyst summarizing a short-term options trade recommendation. " +
            "Write exactly 2-3 sentences. Focus on the directional thesis, the key risk, and why the contract " +
            "structure is appropriate for a weekly expiry. Do not repeat the raw numbers verbatim — " +
            "interpret them in plain English. Do not use bullet points.",
        },
        {
          role: "user",
          content: prompt,
        },
      ],
      max_tokens: 180,
      temperature: 0.45,
    });

    const rationale =
      typeof response?.response === "string"
        ? response.response.trim()
        : buildFallbackRationale(candidate, regime);

    return jsonResponse({ ok: true, rationale, ai_model: "@cf/meta/llama-3-8b-instruct" });
  } catch (error) {
    // Graceful degrade to rule-based if AI is unavailable
    const rationale = buildFallbackRationale(candidate, regime);
    return jsonResponse({
      ok: true,
      rationale,
      ai_model: "rule-based-fallback",
      ai_error: String(error.message || error),
    });
  }
}

function buildPrompt(candidate, regime) {
  const directionLabel =
    candidate.option_type?.toLowerCase() === "call"
      ? "bullish (CALL)"
      : "bearish (PUT)";
  const regimeLabel = regime?.mode?.replace("_", " ") || "neutral";
  const forgeScore = Number(candidate.forge_score || 0).toFixed(2);
  const scoutScore = Number(candidate.scout_score || 0).toFixed(2);
  const breakeven = Number((candidate.breakeven_move_pct || 0) * 100).toFixed(1);
  const expectedReturn = Number((candidate.expected_return_pct || 0) * 100).toFixed(0);
  const premium = Number(candidate.premium || 0).toFixed(2);
  const delta = candidate.delta ? Number(candidate.delta).toFixed(2) : "unknown";
  const iv = candidate.implied_volatility
    ? `${Number(candidate.implied_volatility * 100).toFixed(0)}%`
    : "unknown";
  const notes = Array.isArray(candidate.notes) ? candidate.notes.join("; ") : "";

  return (
    `Symbol: ${candidate.symbol} | Direction: ${directionLabel} | Regime: ${regimeLabel} (bias ${regime?.bias ?? 0})\n` +
    `Contract: ${candidate.contract_symbol} | Expiry: ${candidate.expiry} | Strike: $${candidate.strike}\n` +
    `Premium: $${premium} | Delta: ${delta} | IV: ${iv}\n` +
    `Forge Score: ${forgeScore} | Scout Score: ${scoutScore}\n` +
    `Break-even move needed: ${breakeven}% | Projected expected return: ${expectedReturn}%\n` +
    `Open interest: ${candidate.open_interest || 0} | Volume: ${candidate.volume || 0}\n` +
    (notes ? `Engine notes: ${notes}\n` : "")
  );
}

/**
 * Rule-based fallback rationale when AI is unavailable.
 * Produces a reasonably informative 2-sentence description
 * from the signal metadata alone.
 */
function buildFallbackRationale(candidate, regime) {
  const dir = candidate.option_type?.toLowerCase() === "call" ? "bullish" : "bearish";
  const regimeLabel = regime?.mode?.replace("_", " ") || "neutral";
  const breakeven = Number((candidate.breakeven_move_pct || 0) * 100).toFixed(1);
  const forgeScore = Number(candidate.forge_score || 0).toFixed(2);
  const expReturn = Number((candidate.expected_return_pct || 0) * 100).toFixed(0);
  const symbol = candidate.symbol || "this ticker";
  const expiry = candidate.expiry || "this week";

  const regimeLine =
    regime?.mode === "risk_on" && dir === "bullish"
      ? "The broad market is in a risk-on regime, reinforcing the directional bias."
      : regime?.mode === "risk_off" && dir === "bearish"
        ? "The broad market is in a risk-off regime, supporting the defensive positioning."
        : `The market regime is ${regimeLabel}, treated as context for sizing discipline.`;

  return (
    `${symbol} shows a ${dir} setup into the ${expiry} expiry with a Forge conviction score of ${forgeScore} ` +
    `and a projected expected return of ${expReturn}% if the thesis plays out; the stock needs to move ${breakeven}% ` +
    `through break-even before this contract pays off. ${regimeLine}`
  );
}
