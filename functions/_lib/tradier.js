import { readSession } from "./auth.js";

const DEFAULT_LIVE_BASE_URL = "https://api.tradier.com/v1";
const DEFAULT_SANDBOX_BASE_URL = "https://sandbox.tradier.com/v1";
const DEFAULT_MAX_SIGNAL_AGE_MINUTES = 240;
const DEFAULT_PREVIEW_TTL_SECONDS = 300;
const DEFAULT_LIVE_CONFIRM_PHRASE = "EXECUTE LIVE TRADE";
const TRUE_VALUES = new Set(["1", "true", "yes", "on"]);

function boolFromEnv(value) {
  return TRUE_VALUES.has(
    String(value || "")
      .trim()
      .toLowerCase(),
  );
}

function parsePositiveInt(value, fallback, minimum = 1, maximum = 20) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(Math.max(parsed, minimum), maximum);
}

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

function toList(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

function asNumber(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function maskedAccountId(value) {
  const accountId = String(value || "").trim();
  if (!accountId) {
    return "Unavailable";
  }
  if (accountId.length <= 4) {
    return accountId;
  }
  return `${accountId.slice(0, 2)}***${accountId.slice(-2)}`;
}

function cleanMessage(value) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .trim();
}

function tradierErrorMessage(payload, status) {
  const fromPayload =
    payload?.fault?.faultstring ||
    payload?.error ||
    payload?.errors?.error ||
    payload?.errors?.message ||
    payload?.message ||
    payload?.raw;
  if (fromPayload) {
    return cleanMessage(fromPayload);
  }
  return `Tradier request failed with status ${status}`;
}

function normalizeQuoteCollection(payload) {
  const quotes = payload?.quotes?.quote;
  return toList(quotes).map((quote) => ({
    symbol: String(quote.symbol || ""),
    description: String(quote.description || ""),
    last: asNumber(quote.last, null),
    bid: asNumber(quote.bid, null),
    ask: asNumber(quote.ask, null),
    close: asNumber(quote.close, null),
    volume: asNumber(quote.volume, null),
    type: String(quote.type || ""),
    underlying: String(quote.underlying || ""),
    open_interest: asNumber(quote.open_interest, null),
    expiration_date: String(quote.expiration_date || ""),
    option_type: String(quote.option_type || ""),
    greeks: quote.greeks || null,
  }));
}

function normalizePositions(payload) {
  return toList(payload?.positions?.position).map((position) => ({
    symbol: String(position.symbol || ""),
    quantity: asNumber(position.quantity, 0),
    cost_basis: asNumber(position.cost_basis, null),
    current_value: asNumber(position.current_value, null),
    date_acquired: String(position.date_acquired || ""),
  }));
}

function normalizeProfile(payload, accountId) {
  const profile = payload?.profile || {};
  const accounts = toList(profile.account);
  const selected =
    accounts.find(
      (account) => String(account.account_number || "").trim() === accountId,
    ) ||
    accounts[0] ||
    null;
  return {
    id: String(profile.id || ""),
    name: String(profile.name || ""),
    account: selected
      ? {
          account_number: String(selected.account_number || ""),
          classification: String(selected.classification || ""),
          status: String(selected.status || ""),
          type: String(selected.type || ""),
          option_level: asNumber(selected.option_level, null),
          day_trader: Boolean(selected.day_trader),
        }
      : null,
  };
}

function normalizeBalances(payload) {
  const balances = payload?.balances || {};
  return {
    account_type: String(balances.account_type || ""),
    total_equity: asNumber(balances.total_equity, null),
    total_cash: asNumber(balances.total_cash, null),
    option_buying_power: asNumber(balances.option_buying_power, null),
    stock_buying_power: asNumber(balances.stock_buying_power, null),
    open_pl: asNumber(balances.open_pl, null),
    close_pl: asNumber(balances.close_pl, null),
  };
}

function normalizeOrderPayload(payload) {
  const order = payload?.order || {};
  return {
    id: order.id ?? null,
    status: String(order.status || ""),
    result: order.result ?? null,
    symbol: String(order.symbol || ""),
    quantity: asNumber(order.quantity, null),
    side: String(order.side || ""),
    type: String(order.type || ""),
    duration: String(order.duration || ""),
    price: asNumber(order.price, null),
    avg_fill_price: asNumber(order.avg_fill_price, null),
    create_date: String(order.create_date || order.request_date || ""),
    commission: asNumber(order.commission, null),
    fees: asNumber(order.fees, null),
    cost: asNumber(order.cost, null),
    order_cost: asNumber(order.order_cost, null),
    margin_change: asNumber(order.margin_change, null),
    option_symbol: String(order.option_symbol || ""),
    class: String(order.class || ""),
    strategy: String(order.strategy || ""),
    raw: order,
  };
}

function normalizeOrders(payload) {
  return toList(payload?.orders?.order).map((order) => ({
    id: order.id ?? null,
    symbol: String(order.symbol || ""),
    option_symbol: String(order.option_symbol || ""),
    status: String(order.status || ""),
    side: String(order.side || ""),
    type: String(order.type || ""),
    duration: String(order.duration || ""),
    quantity: asNumber(order.quantity, null),
    remaining_quantity: asNumber(order.remaining_quantity, null),
    create_date: String(order.create_date || ""),
    avg_fill_price: asNumber(order.avg_fill_price, null),
    price: asNumber(order.price, null),
    tag: String(order.tag || ""),
  }));
}

export function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

export async function requireSession(context, { admin = false } = {}) {
  const session = await readSession(context.request, context.env);
  if (!session) {
    return {
      response: jsonResponse(
        { ok: false, error: "Authentication required." },
        401,
      ),
    };
  }
  if (admin && session.role !== "admin") {
    return {
      response: jsonResponse(
        { ok: false, error: "Admin session required." },
        403,
      ),
    };
  }
  return { session };
}

export function getTradierSettings(env) {
  const accessToken = String(
    env.TRADIER_ACCESS_TOKEN || env.OROGRAPHIC_TRADIER_ACCESS_TOKEN || "",
  ).trim();
  const accountId = String(
    env.TRADIER_ACCOUNT_ID || env.OROGRAPHIC_TRADIER_ACCOUNT_ID || "",
  ).trim();
  const requestedBaseUrl = trimTrailingSlash(
    env.TRADIER_BASE_URL || env.OROGRAPHIC_TRADIER_BASE_URL,
  );
  const requestedMode = String(
    env.OROGRAPHIC_TRADIER_MODE || env.TRADIER_TRADING_MODE || "",
  )
    .trim()
    .toLowerCase();
  const sandboxMode =
    requestedMode === "sandbox" ||
    boolFromEnv(env.TRADIER_SANDBOX_MODE) ||
    requestedBaseUrl.includes("sandbox.tradier.com");
  const mode =
    requestedMode === "live"
      ? "live"
      : sandboxMode
        ? "sandbox"
        : accessToken && accountId
          ? "live"
          : "disabled";
  return {
    configured: Boolean(accessToken && accountId && mode !== "disabled"),
    accessToken,
    accountId,
    accountIdMasked: maskedAccountId(accountId),
    baseUrl:
      requestedBaseUrl ||
      (sandboxMode ? DEFAULT_SANDBOX_BASE_URL : DEFAULT_LIVE_BASE_URL),
    sandboxMode,
    mode,
    enabled: Boolean(accessToken && accountId && mode !== "disabled"),
    liveTradingEnabled: boolFromEnv(
      env.TRADIER_LIVE_TRADING_ENABLED ||
        env.OROGRAPHIC_TRADIER_ENABLE_LIVE_ORDERS,
    ),
    maxContracts: parsePositiveInt(
      env.TRADIER_MAX_CONTRACTS,
      mode === "live" ? 1 : 3,
      1,
      10,
    ),
    maxSignalAgeMinutes: parsePositiveInt(
      env.OROGRAPHIC_MAX_SIGNAL_AGE_MINUTES,
      DEFAULT_MAX_SIGNAL_AGE_MINUTES,
      15,
      1440,
    ),
    previewTtlSeconds: parsePositiveInt(
      env.TRADIER_PREVIEW_TTL_SECONDS ||
        env.OROGRAPHIC_TRADIER_PREVIEW_TTL_SECONDS,
      DEFAULT_PREVIEW_TTL_SECONDS,
      60,
      3600,
    ),
    liveConfirmPhrase: String(
      env.TRADIER_LIVE_CONFIRM_PHRASE ||
        env.OROGRAPHIC_TRADIER_LIVE_CONFIRM_PHRASE ||
        DEFAULT_LIVE_CONFIRM_PHRASE,
    ).trim(),
  };
}

export function publicTradierConfig(settings) {
  return {
    configured: settings.configured,
    environment: settings.sandboxMode ? "sandbox" : "live",
    mode: settings.mode,
    liveTradingEnabled: settings.liveTradingEnabled,
    accountIdMasked: settings.accountIdMasked,
    maxContracts: settings.maxContracts,
    maxSignalAgeMinutes: settings.maxSignalAgeMinutes,
    previewTtlSeconds: settings.previewTtlSeconds,
  };
}

export async function tradierRequest(
  envOrConfig,
  pathOrOptions,
  maybeOptions = {},
) {
  const settings =
    typeof pathOrOptions === "string" || envOrConfig?.accessToken
      ? envOrConfig
      : getTradierSettings(envOrConfig);
  const request =
    typeof pathOrOptions === "string"
      ? {
          path: pathOrOptions,
          method: maybeOptions.method || "GET",
          query: maybeOptions.query || maybeOptions.search,
          form: maybeOptions.form,
        }
      : pathOrOptions;
  if (!settings.configured) {
    throw new Error(
      "Tradier is not configured. Set TRADIER_ACCESS_TOKEN and TRADIER_ACCOUNT_ID first.",
    );
  }

  const url = new URL(
    `${settings.baseUrl}${request.path.startsWith("/") ? request.path : `/${request.path}`}`,
  );
  for (const [key, value] of Object.entries(request.query || {})) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    if (Array.isArray(value)) {
      for (const item of value) {
        url.searchParams.append(key, String(item));
      }
      continue;
    }
    url.searchParams.set(key, String(value));
  }

  const headers = new Headers({
    Accept: "application/json",
    Authorization: `Bearer ${settings.accessToken}`,
  });

  const requestInit = {
    method: request.method || "GET",
    headers,
  };

  if (request.form) {
    headers.set("Content-Type", "application/x-www-form-urlencoded");
    const body = new URLSearchParams();
    for (const [key, value] of Object.entries(request.form)) {
      if (value === undefined || value === null || value === "") {
        continue;
      }
      body.set(key, String(value));
    }
    requestInit.body = body.toString();
  }

  const response = await fetch(url.toString(), requestInit);
  const text = await response.text();

  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text ? { raw: text } : null;
  }

  return {
    ok: response.ok,
    status: response.status,
    data,
    payload: data,
    rateLimits: {
      allowed: response.headers.get("X-Ratelimit-Allowed"),
      used: response.headers.get("X-Ratelimit-Used"),
      available: response.headers.get("X-Ratelimit-Available"),
      expiry: response.headers.get("X-Ratelimit-Expiry"),
    },
    rateLimit: {
      allowed: response.headers.get("X-Ratelimit-Allowed"),
      used: response.headers.get("X-Ratelimit-Used"),
      available: response.headers.get("X-Ratelimit-Available"),
      expiry: response.headers.get("X-Ratelimit-Expiry"),
    },
  };
}

export async function fetchBrokerStatus(env) {
  const settings = getTradierSettings(env);
  if (!settings.configured) {
    return {
      ...publicTradierConfig(settings),
      profile: null,
      balances: null,
      positions: [],
      orders: [],
      rateLimits: null,
    };
  }

  const [profileResponse, balanceResponse, positionsResponse, ordersResponse] =
    await Promise.all([
      tradierRequest(env, { path: "/user/profile" }),
      tradierRequest(env, { path: `/accounts/${settings.accountId}/balances` }),
      tradierRequest(env, {
        path: `/accounts/${settings.accountId}/positions`,
      }),
      tradierRequest(env, { path: `/accounts/${settings.accountId}/orders` }),
    ]);

  for (const response of [
    profileResponse,
    balanceResponse,
    positionsResponse,
    ordersResponse,
  ]) {
    if (!response.ok) {
      throw new Error(tradierErrorMessage(response.data, response.status));
    }
  }

  return {
    ...publicTradierConfig(settings),
    profile: normalizeProfile(profileResponse.data, settings.accountId),
    balances: normalizeBalances(balanceResponse.data),
    positions: normalizePositions(positionsResponse.data),
    orders: normalizeOrders(ordersResponse.data),
    rateLimits: {
      balances: balanceResponse.rateLimits,
      positions: positionsResponse.rateLimits,
      orders: ordersResponse.rateLimits,
    },
  };
}

export async function fetchQuotes(env, symbols) {
  const response = await tradierRequest(env, {
    path: "/markets/quotes",
    query: {
      symbols: symbols.join(","),
      greeks: "true",
    },
  });
  if (!response.ok) {
    throw new Error(tradierErrorMessage(response.data, response.status));
  }
  return {
    quotes: normalizeQuoteCollection(response.data),
    rateLimits: response.rateLimits,
  };
}

export async function previewOrPlaceOrder(env, payload, { preview }) {
  const settings = getTradierSettings(env);
  const response = await tradierRequest(env, {
    path: `/accounts/${settings.accountId}/orders`,
    method: "POST",
    form: {
      class: payload.class,
      symbol: payload.symbol,
      option_symbol: payload.option_symbol,
      side: payload.side,
      quantity: payload.quantity,
      type: payload.type,
      duration: payload.duration,
      price: payload.price,
      preview: preview ? "true" : undefined,
      tag: "orographic-arena",
    },
  });

  if (!response.ok) {
    throw new Error(tradierErrorMessage(response.data, response.status));
  }

  const order = normalizeOrderPayload(response.data);
  if (order.result === false) {
    throw new Error(tradierErrorMessage(response.data, response.status));
  }

  if (!preview && order.id) {
    const detailResponse = await tradierRequest(env, {
      path: `/accounts/${settings.accountId}/orders/${order.id}`,
    });
    if (!detailResponse.ok) {
      throw new Error(
        tradierErrorMessage(detailResponse.data, detailResponse.status),
      );
    }
    return {
      order,
      confirmation: normalizeOrderPayload(detailResponse.data),
      rateLimits: detailResponse.rateLimits,
    };
  }

  return {
    order,
    confirmation: null,
    rateLimits: response.rateLimits,
  };
}

export function getTradierConfig(env) {
  return getTradierSettings(env);
}

export function summarizeAccount(payload, config) {
  const balances = normalizeBalances(payload);
  return {
    account_id: config.accountIdMasked,
    total_equity: balances.total_equity,
    total_cash: balances.total_cash,
    option_buying_power: balances.option_buying_power,
    margin_equity: null,
  };
}

export function summarizePositions(payload) {
  return normalizePositions(payload).slice(0, 6);
}

export function summarizeOrders(payload) {
  return normalizeOrders(payload).slice(0, 8);
}

export async function loadLatestSnapshot(context) {
  const url = new URL("/data/latest_run.json", context.request.url);
  const cookie = context.request.headers.get("cookie") || "";
  const assetFetch = context.env?.ASSETS?.fetch?.bind(context.env.ASSETS);
  const response = assetFetch
    ? await assetFetch(
        new Request(url.toString(), {
          headers: cookie ? { cookie } : undefined,
        }),
      )
    : await fetch(url.toString(), {
        headers: cookie ? { cookie } : undefined,
      });
  if (!response.ok) {
    throw new Error(`Unable to load latest_run.json (${response.status})`);
  }
  return response.json();
}

export function describeSnapshot(snapshot, maxSignalAgeMinutes) {
  const generatedAt = String(snapshot?.generated_at_utc || "");
  const generatedTime = Date.parse(generatedAt);
  if (!generatedAt || Number.isNaN(generatedTime)) {
    return {
      generated_at_utc: generatedAt || null,
      age_minutes: null,
      is_fresh: false,
      reason: "Snapshot timestamp is unavailable.",
    };
  }
  const ageMinutes = Math.max(0, (Date.now() - generatedTime) / 60000);
  const isFresh = ageMinutes <= maxSignalAgeMinutes;
  return {
    generated_at_utc: generatedAt,
    age_minutes: Number(ageMinutes.toFixed(1)),
    is_fresh: isFresh,
    reason: isFresh
      ? "Signal snapshot is within the live-trading freshness window."
      : `Signal snapshot is older than ${maxSignalAgeMinutes} minutes.`,
  };
}

export function findCandidate(snapshot, contractSymbol) {
  const target = String(contractSymbol || "").trim();
  if (!target) {
    return null;
  }
  const pools = [
    { lane: "live", rows: toList(snapshot?.council?.live_board) },
    { lane: "shadow", rows: toList(snapshot?.council?.shadow_board) },
    { lane: "forge", rows: toList(snapshot?.forge_candidates) },
  ];
  for (const pool of pools) {
    const candidate = pool.rows.find(
      (row) => String(row.contract_symbol || "").trim() === target,
    );
    if (candidate) {
      return {
        lane: pool.lane,
        candidate,
      };
    }
  }
  return null;
}

export async function fetchOptionQuote(config, optionSymbol) {
  const response = await tradierRequest(config, "/markets/quotes", {
    search: {
      symbols: optionSymbol,
      greeks: "false",
    },
  });
  if (!response.ok) {
    throw new Error(tradierErrorMessage(response.payload, response.status));
  }
  const quoteNode = response.payload?.quotes?.quote;
  return {
    quote: Array.isArray(quoteNode) ? quoteNode[0] || null : quoteNode || null,
    rateLimits: response.rateLimits,
  };
}

export function buildOrderEnvelope(candidate, quantity, config, quote, side = "buy_to_open") {
  const mode = String(config?.mode || "disabled").toLowerCase();
  const maxContracts = parsePositiveInt(config?.maxContracts, mode === "live" ? 1 : 3, 1, 10);
  const liveAsk = asNumber(quote?.ask, null);
  const liveBid = asNumber(quote?.bid, null);
  const fallbackAsk = asNumber(candidate?.ask, null);
  const fallbackBid = asNumber(candidate?.bid, null);

  const referencePrice = side === "sell_to_close"
    ? (liveBid || fallbackBid || asNumber(candidate?.premium, 0.01) || 0.01)
    : (liveAsk || fallbackAsk || liveBid || fallbackBid || asNumber(candidate?.premium, 0.01) || 0.01);

  return {
    class: "option",
    symbol: String(candidate.symbol || "")
      .trim()
      .toUpperCase(),
    option_symbol: String(candidate.contract_symbol || "")
      .trim()
      .toUpperCase(),
    side,
    quantity: parsePositiveInt(
      quantity,
      1,
      1,
      maxContracts,
    ),
    type: "limit",
    duration: "day",
    price: Number(referencePrice).toFixed(2),
    tag: `orographic-${mode}-${String(candidate.symbol || "").toLowerCase()}`,
  };
}

export function buildSubmissionPreview({
  config,
  session,
  lane,
  snapshotInfo,
  side = "buy_to_open",
}) {
  const validation = validateSubmission({ config, session, lane, snapshotInfo, side });
  let allowed = validation.ok;
  let reason = validation.ok ? null : validation.error;

  if (allowed && config.mode === "live" && !config.liveTradingEnabled) {
    allowed = false;
    reason = "Live trading is not enabled. Set TRADIER_LIVE_TRADING_ENABLED=true to arm live orders.";
  }

  return {
    allowed,
    reason,
    status: validation.ok
      ? (allowed ? 200 : 412)
      : validation.status,
    requires_admin: true,
    requires_live_confirmation: config.mode === "live",
    live_confirmation_phrase: config.mode === "live" ? config.liveConfirmPhrase : null,
    max_contracts: config.maxContracts,
    mode: config.mode,
    side,
  };
}

export function buildEligibility({ config, lane, snapshotInfo }) {
  const warnings = [];
  if (config.mode === "sandbox") {
    warnings.push("Sandbox mode uses delayed data and paper orders.");
  }
  if (!snapshotInfo?.is_fresh) {
    warnings.push(snapshotInfo?.reason || "Signal snapshot is stale.");
  }
  return {
    lane,
    live_submittable: config.mode !== "live" || lane === "live",
    warnings,
  };
}

export function validateSubmission({ config, session, lane, snapshotInfo, side = "buy_to_open" }) {
  if (!session) {
    return { ok: false, status: 401, error: "Authenticated session required." };
  }
  if (session.role !== "admin") {
    return {
      ok: false,
      status: 403,
      error: "Admin session required to transmit broker orders.",
    };
  }
  if (!config.enabled) {
    return {
      ok: false,
      status: 412,
      error: "Tradier credentials are not configured.",
    };
  }

  // Bypassed for closing positions because manual exit does not depend on AI radar freshness
  if (side === "buy_to_open") {
    if (!snapshotInfo?.is_fresh) {
      return {
        ok: false,
        status: 409,
        error: snapshotInfo?.reason || "Signal snapshot is stale.",
      };
    }
    if (config.mode === "live" && lane !== "live") {
      return {
        ok: false,
        status: 409,
        error:
          "Live mode only allows contracts currently promoted to the live council board.",
      };
    }
  }
  return { ok: true };
}

export function formatTradierError(
  error,
  fallbackMessage = "Tradier request failed.",
) {
  return {
    message:
      error?.payload?.fault?.faultstring ||
      error?.payload?.error ||
      error?.message ||
      fallbackMessage,
    status: Number(error?.status || 502),
    details: error?.payload || null,
    rate_limits: error?.rateLimits || error?.rateLimit || null,
  };
}
