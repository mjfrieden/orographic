import { getTradierSettings, requireSession, tradierRequest } from "../../_lib/tradier.js";
import { GAINLOSS_LIMIT, normalizeGainloss, normalizeGainlossRateLimits, parseGainlossPage } from "../../_lib/gainloss.js";

function reply(payload, status = 200) {
  return Response.json(payload, { status, headers: { "cache-control": "private, no-store", "pragma": "no-cache" } });
}
export async function onRequestGet(context) {
  const auth = await requireSession(context, { admin: true });
  if (auth.response) {
    auth.response.headers.set("cache-control", "private, no-store");
    return auth.response;
  }
  const page = parseGainlossPage(context.request.url);
  if (page === null) return reply({ ok: false, code: "invalid_page", error: "Only one page parameter is allowed, an integer from 1 to 10000." }, 400);
  const settings = getTradierSettings(context.env);
  if (!settings.configured) return reply({ ok: false, code: "broker_unavailable", error: "Tradier gain/loss is unavailable. Check the broker configuration." }, 503);
  try {
    const response = await tradierRequest(settings, {
      path: `/accounts/${encodeURIComponent(settings.accountId)}/gainloss`,
      method: "GET",
      query: { page, limit: GAINLOSS_LIMIT, sortBy: "closeDate", sort: "desc" },
    });
    const rate_limits = normalizeGainlossRateLimits(response.rateLimits);
    if (!response.ok) return reply({
      ok: false,
      code: response.status === 429 ? "rate_limited" : "broker_error",
      error: response.status === 429
        ? "Tradier rate limit reached. Wait until the reported rate-limit expiry before manually trying again; no automatic retry was made."
        : "Tradier could not return closed-position results. Existing results may be stale; try again manually later.",
      rate_limits,
    }, response.status === 429 ? 429 : 502);
    let normalized;
    try { normalized = normalizeGainloss(response.data, page); }
    catch { return reply({ ok: false, code: "malformed_response", error: "Tradier returned an unrecognized gain/loss response. No complete-history result is available." }, 502); }
    return reply({
      ok: true,
      source: "tradier_gainloss",
      label: "Broker-reported realized gain/loss",
      environment: settings.mode,
      fetched_at_utc: new Date().toISOString(),
      broker_as_of: null,
      ...normalized,
      rate_limits,
      warnings: [
        "Cost basis is reconciled nightly. Today's closes may not appear yet.",
        "Loaded rows are a partial view, not complete account performance. Fees coverage is unverified; this is not net account return.",
        "Pages are separate requests, not a fixed snapshot; broker revisions can shift rows between pages.",
        ...(settings.mode === "sandbox" ? ["Sandbox gain/loss behavior is unverified; these are not live account results."] : []),
      ],
    });
  } catch {
    // Never expose broker bodies, credentials, full account IDs, or thrown URLs.
    return reply({ ok: false, code: "broker_error", error: "Tradier gain/loss could not be loaded. No automatic retry was made." }, 502);
  }
}
