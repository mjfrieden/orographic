import { fetchBrokerStatus, jsonResponse } from "../../../_lib/tradier.js";
import {
  requireInternalCaptureToken,
  upsertPositionSnapshot,
} from "../../../_lib/position_history.js";

export async function onRequestPost(context) {
  const gate = await requireInternalCaptureToken(context.request, context.env);
  if (!gate.ok) {
    return gate.response;
  }

  let body = {};
  try {
    body = await context.request.json();
  } catch {
    body = {};
  }

  try {
    const broker = await fetchBrokerStatus(context.env);
    const capturedAtUtc = new Date().toISOString();
    const snapshot = {
      run_generated_at_utc:
        String(body.run_generated_at_utc || "").trim() || capturedAtUtc,
      captured_at_utc: capturedAtUtc,
      source: String(body.source || "github_actions_orographic_scan").trim(),
      configured: Boolean(broker.configured),
      mode: String(broker.mode || ""),
      account_masked: broker.account_masked || null,
      positions_count: Array.isArray(broker.positions) ? broker.positions.length : 0,
      balances: broker.balances || null,
      positions: broker.positions || [],
    };
    await upsertPositionSnapshot(context.env, snapshot);
    return jsonResponse({
      ok: true,
      captured_at_utc: snapshot.captured_at_utc,
      run_generated_at_utc: snapshot.run_generated_at_utc,
      positions_count: snapshot.positions_count,
      configured: snapshot.configured,
      source: snapshot.source,
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
