import { jsonResponse } from "../../_lib/tradier.js";

/**
 * POST /api/ai/sentinel
 *
 * Cloudflare Workers AI edge route for Orographic Sentinel.
 * Extracts structured event features from real-time news. Multipliers are
 * shadow-only unless mode=active is explicitly provided by internal tooling.
 *
 * Body:
 *   symbol    - The stock ticker (e.g. "AAPL")
 *   headlines - Array of recent news strings
 *
 * Returns:
 *   { ok: true, event_type, polarity, multiplier, shadow_multiplier, ... }
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
  const mode = normalizeMode(body?.mode || context.env?.OROGRAPHIC_SENTINEL_MODE);
  
  if (!context.env?.AI) {
    return jsonResponse({ 
      ok: true, 
      multiplier: 1.0, 
      shadow_multiplier: 1.0,
      catalyst: "none", 
      rationale: "Cloudflare AI binding not available locally.",
      sentiment_score: 0.0,
      event_polarity: 0.0,
      event_type: "none",
      directional_relevance: "neither",
      novelty: "unknown",
      source_reliability: "unknown",
      time_horizon: "unknown",
      confidence: 0.0,
      direction,
      mode,
      source: "fallback_no_ai",
    });
  }

  if (!headlines || headlines.length === 0) {
    return jsonResponse({
        ok: true,
        multiplier: 1.0,
        shadow_multiplier: 1.0,
        catalyst: "none",
        rationale: "No recent news available to evaluate.",
        sentiment_score: 0.0,
        event_polarity: 0.0,
        event_type: "none",
        directional_relevance: "neither",
        novelty: "unknown",
        source_reliability: "unknown",
        time_horizon: "unknown",
        confidence: 0.0,
        direction,
        mode,
        source: "fallback_no_news",
    });
  }

  const newsText = headlines.map(h => `- ${h}`).join("\n");
  
  const prompt = `You are a strict quantitative trading event extraction system.
Evaluate these recent news headlines for the stock ${symbol}.
Extract structured event features only. Do not recommend trades.
The engine is considering a ${direction || "unknown"} setup with current signed Scout score ${scoutScore.toFixed(3)}.
Determine event polarity for the underlying stock, not for the option side.
- event_polarity -1.0: severe downside catalyst.
- event_polarity -0.5: negative news, downgrade, lawsuit, demand weakness.
- event_polarity 0.0: neutral news, stale news, noise, or no clear edge.
- event_polarity 0.5: positive earnings, analyst upgrade, demand strength.
- event_polarity 1.0: highly material upside catalyst.

Use this controlled vocabulary:
event_type: earnings, guidance, m_and_a, legal_regulatory, analyst, macro, product, supply_chain, geopolitical, fraud_accounting, financing, no_clear_event
directional_relevance: call, put, both, neither
novelty: stale, incremental, new, unknown
source_reliability: primary, major_news, analyst, social_or_rumor, unknown
time_horizon: intraday, one_to_three_days, one_to_two_weeks, longer, unknown

Headlines:
${newsText}

You MUST reply ONLY with a valid JSON object exactly matching this schema, completely unformatted (no markdown blocks or backticks):
{"event_type":"earnings","event_polarity":0.7,"directional_relevance":"call","novelty":"new","source_reliability":"major_news","time_horizon":"one_to_three_days","confidence":0.74,"catalyst":"earnings beat","rationale":"Recent earnings headlines indicate upside demand surprise."}`;

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
      const eventPolarity = clamp(Number(parsed.event_polarity ?? parsed.sentiment_score ?? parsed.sentiment ?? 0), -1, 1);
      const confidence = clamp(Number(parsed.confidence ?? 0), 0, 1);
      const eventType = normalizeEventType(parsed.event_type);
      const directionalRelevance = normalizeDirectionalRelevance(parsed.directional_relevance);
      const shadowMultiplier = directionAwareMultiplier(eventPolarity, direction, directionalRelevance, confidence);
      const multiplier = mode === "active" ? shadowMultiplier : 1.0;
      return jsonResponse({ 
        ok: true, 
        multiplier,
        shadow_multiplier: shadowMultiplier,
        sentiment_score: eventPolarity,
        event_polarity: eventPolarity,
        event_type: eventType,
        directional_relevance: directionalRelevance,
        novelty: normalizeEnum(parsed.novelty, ["stale", "incremental", "new", "unknown"], "unknown"),
        source_reliability: normalizeEnum(parsed.source_reliability, ["primary", "major_news", "analyst", "social_or_rumor", "unknown"], "unknown"),
        time_horizon: normalizeEnum(parsed.time_horizon, ["intraday", "one_to_three_days", "one_to_two_weeks", "longer", "unknown"], "unknown"),
        confidence,
        direction,
        mode,
        catalyst: parsed.catalyst || "none",
        rationale: parsed.rationale || "Structured event extracted via Llama-3.",
        source: "@cf/meta/llama-3-8b-instruct",
      });
    } catch (parseError) {
      // Model hallucinated or returned malformed JSON
      return jsonResponse({
          ok: true,
          multiplier: 1.0, // Fail-safe degradation
          shadow_multiplier: 1.0,
          sentiment_score: 0.0,
          event_polarity: 0.0,
          event_type: "parse_error",
          directional_relevance: "neither",
          novelty: "unknown",
          source_reliability: "unknown",
          time_horizon: "unknown",
          confidence: 0.0,
          direction,
          mode,
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
      shadow_multiplier: 1.0,
      sentiment_score: 0.0,
      event_polarity: 0.0,
      event_type: "error",
      directional_relevance: "neither",
      novelty: "unknown",
      source_reliability: "unknown",
      time_horizon: "unknown",
      confidence: 0.0,
      direction,
      mode,
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

function normalizeMode(value) {
  return String(value || "").toLowerCase() === "active" ? "active" : "shadow";
}

function normalizeEnum(value, allowed, fallback) {
  const cleaned = String(value || "").toLowerCase();
  return allowed.includes(cleaned) ? cleaned : fallback;
}

function normalizeEventType(value) {
  return normalizeEnum(
    value,
    [
      "earnings",
      "guidance",
      "m_and_a",
      "legal_regulatory",
      "analyst",
      "macro",
      "product",
      "supply_chain",
      "geopolitical",
      "fraud_accounting",
      "financing",
      "no_clear_event",
      "parse_error",
      "error",
      "none",
    ],
    "no_clear_event",
  );
}

function normalizeDirectionalRelevance(value) {
  return normalizeEnum(value, ["call", "put", "both", "neither"], "neither");
}

function clamp(value, low, high) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(low, Math.min(high, value));
}

function directionAwareMultiplier(eventPolarity, direction, directionalRelevance, confidence) {
  if (!direction) return 1.0;
  if (directionalRelevance !== "both" && directionalRelevance !== direction) {
    return 1.0;
  }
  const directionalSentiment = direction === "put" ? -eventPolarity : eventPolarity;
  const scaled = directionalSentiment * 0.35 * clamp(confidence || 0, 0, 1);
  return Number(clamp(1.0 + scaled, 0.0, 1.5).toFixed(4));
}
