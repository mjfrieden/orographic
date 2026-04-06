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
  let body;
  try {
    body = await context.request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Request body must be valid JSON." }, 400);
  }

  const { symbol, headlines } = body || {};
  
  if (!context.env?.AI) {
    return jsonResponse({ 
      ok: true, 
      multiplier: 1.0, 
      catalyst: "none", 
      rationale: "Cloudflare AI binding not available locally." 
    });
  }

  if (!headlines || headlines.length === 0) {
    return jsonResponse({
        ok: true,
        multiplier: 1.0,
        catalyst: "none",
        rationale: "No recent news available to evaluate."
    });
  }

  const newsText = headlines.map(h => `- ${h}`).join("\n");
  
  const prompt = `You are a strict quantitative trading Sentinel.
Evaluate these recent news headlines for the stock ${symbol}.
Identify if there is a fundamental catalyst driving the stock today (e.g., earnings beat, macro shift, scandal, buyout) or just noise.
Determine a numerical edge multiplier between 0.0 and 1.5.
- 0.0: Disaster, massive scandal, bankruptcy threat. DO NOT TRADE.
- 0.5: Negative news, lawsuits, downgrades.
- 1.0: Neutral news, product updates, noise, or no clear edge.
- 1.25: Strong positive news, earnings beat, analyst upgrades.
- 1.5: Explosive unpriced buyout, massive systemic tailwind.

Headlines:
${newsText}

You MUST reply ONLY with a valid JSON object exactly matching this schema, completely unformatted (no markdown blocks or backticks):
{"multiplier": 1.2, "catalyst": "earnings", "rationale": "Strong Q4 earnings beat driving fundamental upside."}`;

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
      return jsonResponse({ 
        ok: true, 
        multiplier: Number(parsed.multiplier) || 1.0,
        catalyst: parsed.catalyst || "none",
        rationale: parsed.rationale || "Interpreted via Llama-3."
      });
    } catch (parseError) {
      // Model hallucinated or returned malformed JSON
      return jsonResponse({
          ok: true,
          multiplier: 1.0, // Fail-safe degradation
          catalyst: "parse_error",
          rationale: "LLM failed to adhere to strict JSON schema.",
          raw: response?.response
      });
    }

  } catch (error) {
    return jsonResponse({
      ok: true,
      multiplier: 1.0,
      catalyst: "error",
      rationale: "Failed to connect to Cloudflare AI inference.",
      error: String(error.message || error)
    });
  }
}
