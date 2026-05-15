function toList(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

function cleanText(value, fallback = "") {
  return String(value ?? fallback).trim();
}

function asNumber(value, fallback = null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function parseLimit(value, fallback = 50, maximum = 250) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(1, Math.min(parsed, maximum));
}

function compactCandidate(candidate) {
  if (!candidate || typeof candidate !== "object") {
    return null;
  }
  return {
    symbol: candidate.symbol || null,
    contract_symbol: candidate.contract_symbol || null,
    option_type: candidate.option_type || null,
    expiry: candidate.expiry || null,
    strike: candidate.strike ?? null,
    forge_score: candidate.forge_score ?? null,
    learned_rank_score: candidate.learned_rank_score ?? null,
    payoff_edge_score: candidate.payoff_edge_score ?? null,
    expected_edge_after_friction_pct:
      candidate.expected_edge_after_friction_pct ?? null,
    path_decay_risk: candidate.path_decay_risk ?? null,
    path_early_profit_take_prob: candidate.path_early_profit_take_prob ?? null,
    contract_cost: candidate.contract_cost ?? null,
    spread_pct: candidate.spread_pct ?? null,
    extrinsic_ratio: candidate.extrinsic_ratio ?? null,
    council_risk_flags: Array.isArray(candidate.council_risk_flags)
      ? candidate.council_risk_flags
      : [],
    notes: Array.isArray(candidate.notes) ? candidate.notes : [],
  };
}

function jsonString(value) {
  return JSON.stringify(value ?? null);
}

async function ensureSchema(env) {
  if (!env.ORDER_LEDGER_DB && !env.POSITIONS_DB) {
    throw new Error("Missing ORDER_LEDGER_DB or POSITIONS_DB binding.");
  }
  const db = env.ORDER_LEDGER_DB || env.POSITIONS_DB;
  await db.batch([
    db.prepare(
      `CREATE TABLE IF NOT EXISTS order_provenance_events (
        id TEXT PRIMARY KEY,
        created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        event_type TEXT NOT NULL,
        mode TEXT NOT NULL,
        username TEXT,
        user_role TEXT,
        run_generated_at_utc TEXT,
        lane TEXT NOT NULL,
        symbol TEXT NOT NULL,
        option_symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        limit_price REAL,
        broker_order_id TEXT,
        broker_status TEXT,
        exit_policy_action TEXT,
        candidate_json TEXT,
        quote_json TEXT,
        order_json TEXT,
        payload_json TEXT NOT NULL
      )`,
    ),
    db.prepare(
      `CREATE INDEX IF NOT EXISTS idx_order_provenance_created
        ON order_provenance_events(created_at_utc DESC)`,
    ),
    db.prepare(
      `CREATE INDEX IF NOT EXISTS idx_order_provenance_contract
        ON order_provenance_events(option_symbol, created_at_utc DESC)`,
    ),
  ]);
  return db;
}

export function buildOrderProvenanceEvent({
  eventType,
  config,
  session,
  snapshot,
  snapshotInfo,
  lane,
  candidate,
  quote,
  envelope,
  result,
  exitPolicyAction,
  blockReason,
  httpStatus,
  error,
}) {
  const order = result?.confirmation || result?.order || null;
  const mode = cleanText(config?.mode, "disabled");
  const now = new Date().toISOString();
  const compact = compactCandidate(candidate);
  const event = {
    id:
      globalThis.crypto?.randomUUID?.() ||
      `order-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    created_at_utc: now,
    event_type: cleanText(eventType, "unknown"),
    mode,
    username: cleanText(session?.username || session?.user || ""),
    user_role: cleanText(session?.role || ""),
    run_generated_at_utc: cleanText(
      snapshot?.generated_at_utc || snapshotInfo?.generated_at_utc || "",
    ),
    snapshot_age_minutes: asNumber(snapshotInfo?.age_minutes, null),
    lane: cleanText(lane, "unknown"),
    symbol: cleanText(envelope?.symbol || candidate?.symbol || ""),
    option_symbol: cleanText(
      envelope?.option_symbol || candidate?.contract_symbol || "",
    ),
    side: cleanText(envelope?.side || ""),
    quantity: asNumber(envelope?.quantity, 0),
    limit_price: asNumber(envelope?.price, null),
    broker_order_id: cleanText(order?.id || result?.order?.id || ""),
    broker_status: cleanText(
      order?.status || result?.order?.status || blockReason || "",
    ),
    block_reason: cleanText(blockReason || ""),
    http_status: asNumber(httpStatus, null),
    error: cleanText(error || ""),
    exit_policy_action: cleanText(exitPolicyAction || ""),
    tag: cleanText(envelope?.tag || ""),
    candidate: compact,
    quote: quote || null,
    order: order || result?.order || null,
    confirmation: result?.confirmation || null,
  };
  return event;
}

export async function recordOrderProvenance(env, event) {
  const db = await ensureSchema(env);
  await db.prepare(
    `INSERT INTO order_provenance_events (
      id,
      created_at_utc,
      event_type,
      mode,
      username,
      user_role,
      run_generated_at_utc,
      lane,
      symbol,
      option_symbol,
      side,
      quantity,
      limit_price,
      broker_order_id,
      broker_status,
      exit_policy_action,
      candidate_json,
      quote_json,
      order_json,
      payload_json
    ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20)`,
  )
    .bind(
      event.id,
      event.created_at_utc,
      event.event_type,
      event.mode,
      event.username,
      event.user_role,
      event.run_generated_at_utc,
      event.lane,
      event.symbol,
      event.option_symbol,
      event.side,
      Math.max(0, Number.parseInt(String(event.quantity || 0), 10)),
      event.limit_price,
      event.broker_order_id,
      event.broker_status,
      event.exit_policy_action,
      jsonString(event.candidate),
      jsonString(event.quote),
      jsonString(event.confirmation || event.order),
      jsonString(event),
    )
    .run();
  return event;
}

export async function safeRecordOrderProvenance(env, event) {
  try {
    return { ok: true, event: await recordOrderProvenance(env, event) };
  } catch (error) {
    return {
      ok: false,
      error: String(error.message || error),
      event,
    };
  }
}

export async function listOrderProvenance(env, limit = 50) {
  const db = await ensureSchema(env);
  const safeLimit = parseLimit(limit);
  const result = await db.prepare(
    `SELECT
      id,
      created_at_utc,
      event_type,
      mode,
      username,
      user_role,
      run_generated_at_utc,
      lane,
      symbol,
      option_symbol,
      side,
      quantity,
      limit_price,
      broker_order_id,
      broker_status,
      exit_policy_action,
      payload_json
    FROM order_provenance_events
    ORDER BY created_at_utc DESC
    LIMIT ?1`,
  )
    .bind(safeLimit)
    .all();
  return toList(result?.results).map((row) => {
    let payload = null;
    try {
      payload = JSON.parse(String(row.payload_json || "null"));
    } catch {
      payload = null;
    }
    return {
      id: cleanText(row.id),
      created_at_utc: cleanText(row.created_at_utc),
      event_type: cleanText(row.event_type),
      mode: cleanText(row.mode),
      username: cleanText(row.username),
      user_role: cleanText(row.user_role),
      run_generated_at_utc: cleanText(row.run_generated_at_utc),
      lane: cleanText(row.lane),
      symbol: cleanText(row.symbol),
      option_symbol: cleanText(row.option_symbol),
      side: cleanText(row.side),
      quantity: Number(row.quantity || 0),
      limit_price: asNumber(row.limit_price, null),
      broker_order_id: cleanText(row.broker_order_id),
      broker_status: cleanText(row.broker_status),
      exit_policy_action: cleanText(row.exit_policy_action),
      payload,
    };
  });
}
