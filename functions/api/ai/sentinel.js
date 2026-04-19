import { jsonResponse } from "../../_lib/tradier.js";

/**
 * POST /api/ai/sentinel
 *
 * Cloudflare Workers AI edge route for Orographic Sentinel.
 * Evaluates real-time news headlines to detect catalysts and compute
 * an asymmetric mathematical multiplier.
 *
 * Body:
 *   symbol    - The stock ticker (e.g. "AAPL")
 *   headlines - Array of recent news strings
 *
 * Returns:
 *   { ok: true, multiplier: float, catalyst: string, rationale: string }
 */
export async function onRequestPost(context) {
  const authError = requireInternalToken(context);
  if (authError) {
    return authError;
  }

  let body;
  try {
    body = await context.request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Request body must be valid JSON." }, 400);
  }

  const { symbol, headlines } = body || {};
  const direction = normalizeDirection(body?.direction);
  const scoutScore = clamp(Number(body?.scout_score || 0), -1, 1);
  
  if (!context.env?.AI) {
    return jsonResponse({ 
      ok: true, 
      multiplier: 1.0, 
      catalyst: "none", 
      rationale: "Cloudflare AI binding not available locally.",
      sentiment_score: 0.0,
      direction,
      source: "fallback_no_ai",
    });
  }

  if (!headlines || headlines.length === 0) {
    return jsonResponse({
        ok: true,
        multiplier: 1.0,
        catalyst: "none",
        rationale: "No recent news available to evaluate.",
        sentiment_score: 0.0,
        direction,
        source: "fallback_no_news",
    });
  }

  const newsText = headlines.map(h => `- ${h}`).join("\n");
  
  const prompt = `You are a strict quantitative trading Sentinel.
Evaluate these recent news headlines for the stock ${symbol}.
Identify if there is a fundamental catalyst driving the stock today (e.g., earnings beat, macro shift, scandal, buyout) or just noise.
The engine is considering a ${direction || "unknown"} setup with current signed Scout score ${scoutScore.toFixed(3)}.
Determine the news sentiment for the underlying stock, not for the option side.
- -1.0: Disaster, massive scandal, bankruptcy threat, severe downside catalyst.
- -0.5: Negative news, lawsuits, downgrades, demand weakness.
- 0.0: Neutral news, product updates, noise, or no clear edge.
- 0.5: Strong positive news, earnings beat, analyst upgrades.
- 1.0: Explosive unpriced buyout, massive systemic tailwind.

Headlines:
${newsText}

You MUST reply ONLY with a valid JSON object exactly matching this schema, completely unformatted (no markdown blocks or backticks):
{"sentiment_score": 0.7, "catalyst": "earnings", "rationale": "Strong Q4 earnings beat driving fundamental upside."}`;

  try {
    const response = await context.env.AI.run("@cf/meta/llama-3-8b-instruct", {
      messages: [
        {
          role: "system",
          content: "You are a rigid JSON-only output machine. Do not output anything except raw JSON."
        },
        {
          role: "user",
          content: prompt
        }
      ],
      max_tokens: 150,
      temperature: 0.1,
    });

    try {
      let rawText = response?.response || "";
      // Strip markdown block formatting if the model disobeys
      if (rawText.startsWith("\`\`\`json")) {
        rawText = rawText.replace(/\`\`\`json/g, "").replace(/\`\`\`/g, "").trim();
      } else if (rawText.startsWith("\`\`\`")) {
          rawText = rawText.replace(/\`\`\`/g, "").trim();
      }
      
      const parsed = JSON.parse(rawText);
      const sentimentScore = clamp(Number(parsed.sentiment_score ?? parsed.sentiment ?? 0), -1, 1);
      const multiplier = directionAwareMultiplier(sentimentScore, direction);
      return jsonResponse({ 
        ok: true, 
        multiplier,
        sentiment_score: sentimentScore,
        direction,
        catalyst: parsed.catalyst || "none",
        rationale: parsed.rationale || "Interpreted via Llama-3.",
        source: "@cf/meta/llama-3-8b-instruct",
      });
    } catch (parseError) {
      // Model hallucinated or returned malformed JSON
      return jsonResponse({
          ok: true,
          multiplier: 1.0, // Fail-safe degradation
          sentiment_score: 0.0,
          direction,
          catalyst: "parse_error",
          rationale: "LLM failed to adhere to strict JSON schema.",
          source: "parse_error",
          raw: response?.response
      });
    }

  } catch (error) {
    return jsonResponse({
      ok: true,
      multiplier: 1.0,
      sentiment_score: 0.0,
      direction,
      catalyst: "error",
      rationale: "Failed to connect to Cloudflare AI inference.",
      source: "error",
      error: String(error.message || error)
    });
  }
}

function requireInternalToken(context) {
  const configured =
    context.env?.OROGRAPHIC_SENTINEL_TOKEN ||
    context.env?.OROGRAPHIC_INTERNAL_AI_TOKEN ||
    context.env?.OROGRAPHIC_INTERNAL_CAPTURE_TOKEN;
  if (!configured) {
    return jsonResponse({ ok: false, error: "Sentinel token is not configured." }, 503);
  }
  const provided =
    context.request.headers.get("x-orographic-internal-token") ||
    context.request.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (provided !== configured) {
    return jsonResponse({ ok: false, error: "Unauthorized Sentinel request." }, 401);
  }
  return null;
}

function normalizeDirection(value) {
  const direction = String(value || "").toLowerCase();
  return direction === "call" || direction === "put" ? direction : null;
}

function clamp(value, low, high) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(low, Math.min(high, value));
}

function directionAwareMultiplier(sentimentScore, direction) {
  const directionalSentiment = direction === "put" ? -sentimentScore : sentimentScore;
  return Number(clamp(1.0 + directionalSentiment * 0.35, 0.0, 1.5).toFixed(4));
}
