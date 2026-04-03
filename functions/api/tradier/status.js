import {
  fetchBrokerStatus,
  jsonResponse,
  requireSession,
} from "../../_lib/tradier.js";

export async function onRequestGet(context) {
  const auth = await requireSession(context);
  if (auth.response) {
    return auth.response;
  }

  try {
    const payload = await fetchBrokerStatus(context.env);
    return jsonResponse({ ok: true, broker: payload });
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
