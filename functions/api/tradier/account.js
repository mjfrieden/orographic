import {
  fetchBrokerStatus,
  jsonResponse,
  requireSession,
} from "../../_lib/tradier.js";

/**
 * GET /api/tradier/account
 *
 * Full account snapshot: profile, balances, positions, open orders.
 * Requires an authenticated session (any role).
 */
export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }

  try {
    const payload = await fetchBrokerStatus(context.env);
    return jsonResponse({
      ok: true,
      broker: payload,
      account: {
        id: payload.profile?.account?.account_number || null,
        type: payload.profile?.account?.classification || null,
        status: payload.profile?.account?.status || null,
        option_level: payload.profile?.account?.option_level || null,
      },
      balances: payload.balances || null,
      positions: payload.positions || [],
      orders: payload.orders || [],
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
