import {
  getTradierSettings,
  jsonResponse,
  previewOrPlaceOrder,
  publicTradierConfig,
  requireSession,
} from "../../_lib/tradier.js";

const ALLOWED_SIDES = new Set([
  "buy_to_open",
  "buy_to_close",
  "sell_to_open",
  "sell_to_close",
]);

async function loadSnapshot(request) {
  const response = await fetch(
    new URL("/data/latest_run.json", request.url).toString(),
    {
      headers: {
        accept: "application/json",
      },
    },
  );
  if (!response.ok) {
    throw new Error("Unable to load the latest Orographic snapshot.");
  }
  return response.json();
}

function snapshotAgeMinutes(snapshot) {
  const generatedAt = Date.parse(String(snapshot?.generated_at_utc || ""));
  if (!Number.isFinite(generatedAt)) {
    return null;
  }
  return (Date.now() - generatedAt) / 60000;
}

function normalizeOrderPayload(payload, settings) {
  const quantity = Number.parseInt(String(payload.quantity ?? ""), 10);
  if (!Number.isFinite(quantity) || quantity < 1) {
    throw new Error("Quantity must be a positive whole number.");
  }
  if (quantity > settings.maxContracts) {
    throw new Error(
      `Quantity exceeds the configured limit of ${settings.maxContracts} contracts.`,
    );
  }

  const side = String(payload.side || "buy_to_open")
    .trim()
    .toLowerCase();
  if (!ALLOWED_SIDES.has(side)) {
    throw new Error("Unsupported option side.");
  }

  const type = String(payload.type || "limit")
    .trim()
    .toLowerCase();
  if (type !== "limit") {
    throw new Error("This build only allows limit orders.");
  }

  const duration = String(payload.duration || "day")
    .trim()
    .toLowerCase();
  if (duration !== "day") {
    throw new Error("This build only allows day duration orders.");
  }

  const price = Number(payload.price);
  if (!Number.isFinite(price) || price <= 0) {
    throw new Error("A positive limit price is required.");
  }

  const symbol = String(payload.symbol || "")
    .trim()
    .toUpperCase();
  const optionSymbol = String(payload.option_symbol || "")
    .trim()
    .toUpperCase();
  if (!symbol || !optionSymbol) {
    throw new Error("Both symbol and option_symbol are required.");
  }

  return {
    class: "option",
    symbol,
    option_symbol: optionSymbol,
    side,
    quantity,
    type,
    duration,
    price: price.toFixed(2),
  };
}

export async function onRequestPost(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }
  const session = auth.session;

  const settings = getTradierSettings(context.env);
  if (!settings.configured) {
    return jsonResponse(
      {
        ok: false,
        error: "Tradier is not configured.",
        broker: publicTradierConfig(settings),
      },
      400,
    );
  }

  let payload;
  try {
    payload = await context.request.json();
  } catch {
    return jsonResponse({ ok: false, error: "Invalid JSON payload." }, 400);
  }

  const preview = payload.preview !== false;
  if (!preview) {
    if (session.role !== "admin") {
      return jsonResponse(
        { ok: false, error: "Admin session required for order transmission." },
        403,
      );
    }
    if (settings.mode === "live" && !settings.liveTradingEnabled) {
      return jsonResponse(
        {
          ok: false,
          error:
            "Live trading is disabled. Set TRADIER_LIVE_TRADING_ENABLED=true to allow production order placement.",
          broker: publicTradierConfig(settings),
        },
        403,
      );
    }
    if (settings.mode === "live" && payload.confirm_live !== true) {
      return jsonResponse(
        { ok: false, error: "Live orders require explicit confirmation." },
        400,
      );
    }
  }

  let orderPayload;
  try {
    orderPayload = normalizeOrderPayload(payload, settings);
  } catch (error) {
    return jsonResponse(
      { ok: false, error: String(error.message || error) },
      400,
    );
  }

  try {
    if (!preview) {
      const snapshot = await loadSnapshot(context.request);
      const liveContracts = snapshot?.council?.live_board || [];
      const liveMatch = liveContracts.find(
        (candidate) =>
          String(candidate.contract_symbol || "")
            .trim()
            .toUpperCase() === orderPayload.option_symbol,
      );
      const ageMinutes = snapshotAgeMinutes(snapshot);
      if (settings.mode === "live" && !liveMatch) {
        return jsonResponse(
          {
            ok: false,
            error:
              "Live transmission is limited to contracts currently on the live board.",
          },
          403,
        );
      }
      if (
        settings.mode === "live" &&
        (ageMinutes === null || ageMinutes > settings.maxSignalAgeMinutes)
      ) {
        return jsonResponse(
          {
            ok: false,
            error: `Live transmission requires a snapshot newer than ${settings.maxSignalAgeMinutes} minutes.`,
          },
          409,
        );
      }
    }

    const result = await previewOrPlaceOrder(context.env, orderPayload, {
      preview,
    });
    return jsonResponse({
      ok: true,
      preview,
      broker: publicTradierConfig(settings),
      order: result.order,
      confirmation: result.confirmation,
      rateLimits: result.rateLimits,
    });
  } catch (error) {
    return jsonResponse(
      {
        ok: false,
        preview,
        error: String(error.message || error),
        broker: publicTradierConfig(settings),
      },
      502,
    );
  }
}
