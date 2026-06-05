import { listOrderProvenance } from "../../_lib/order_ledger.js";
import { jsonResponse, requireSession } from "../../_lib/tradier.js";

export async function onRequestGet(context) {
  const auth = await requireSession(context, { admin: true });
  if (auth.response) {
    return auth.response;
  }

  try {
    const url = new URL(context.request.url);
    const limit = Number.parseInt(url.searchParams.get("limit") || "50", 10);
    const events = await listOrderProvenance(context.env, limit);
    return jsonResponse({
      ok: true,
      events,
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
