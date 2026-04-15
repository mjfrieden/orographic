function toList(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: {
      "cache-control": "no-store",
      "content-type": "application/json; charset=utf-8",
    },
  });
}

function cleanText(value, fallback = "") {
  return String(value ?? fallback).trim();
}

function parseLimit(value, fallback = 20, maximum = 100) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.max(1, Math.min(parsed, maximum));
}

function timingSafeEqualText(a, b) {
  const left = new TextEncoder().encode(String(a || ""));
  const right = new TextEncoder().encode(String(b || ""));
  if (left.length !== right.length) {
    return false;
  }
  let result = 0;
  for (let i = 0; i < left.length; i += 1) {
    result |= left[i] ^ right[i];
  }
  return result === 0;
}

async function ensureSchema(env) {
  if (!env.POSITIONS_DB) {
    throw new Error("Missing POSITIONS_DB binding.");
  }
  await env.POSITIONS_DB.batch([
    env.POSITIONS_DB.prepare(
      `CREATE TABLE IF NOT EXISTS position_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_generated_at_utc TEXT NOT NULL,
        captured_at_utc TEXT NOT NULL,
        source TEXT NOT NULL,
        positions_count INTEGER NOT NULL DEFAULT 0,
        payload_json TEXT NOT NULL,
        created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(source, run_generated_at_utc)
      )`,
    ),
    env.POSITIONS_DB.prepare(
      `CREATE INDEX IF NOT EXISTS idx_position_snapshots_captured
        ON position_snapshots(captured_at_utc DESC)`,
    ),
  ]);
}

export async function requireInternalCaptureToken(request, env) {
  const expected = cleanText(env.OROGRAPHIC_INTERNAL_CAPTURE_TOKEN);
  if (!expected) {
    return {
      ok: false,
      response: jsonResponse(
        { ok: false, error: "Missing OROGRAPHIC_INTERNAL_CAPTURE_TOKEN secret." },
        500,
      ),
    };
  }
  const authHeader = cleanText(request.headers.get("authorization"));
  const bearer = authHeader.toLowerCase().startsWith("bearer ")
    ? authHeader.slice(7).trim()
    : "";
  const headerToken =
    bearer || cleanText(request.headers.get("x-orographic-internal-token"));
  if (!headerToken || !timingSafeEqualText(headerToken, expected)) {
    return {
      ok: false,
      response: jsonResponse({ ok: false, error: "Forbidden." }, 403),
    };
  }
  return { ok: true };
}

export async function upsertPositionSnapshot(env, snapshot) {
  await ensureSchema(env);
  const normalized = {
    run_generated_at_utc: cleanText(
      snapshot.run_generated_at_utc,
      new Date().toISOString(),
    ),
    captured_at_utc: cleanText(snapshot.captured_at_utc, new Date().toISOString()),
    source: cleanText(snapshot.source, "unknown"),
    positions_count: Number(snapshot.positions_count || 0),
    payload_json: JSON.stringify(snapshot),
  };
  await env.POSITIONS_DB.prepare(
    `INSERT INTO position_snapshots (
      run_generated_at_utc,
      captured_at_utc,
      source,
      positions_count,
      payload_json
    ) VALUES (?1, ?2, ?3, ?4, ?5)
    ON CONFLICT(source, run_generated_at_utc) DO UPDATE SET
      captured_at_utc = excluded.captured_at_utc,
      positions_count = excluded.positions_count,
      payload_json = excluded.payload_json`,
  )
    .bind(
      normalized.run_generated_at_utc,
      normalized.captured_at_utc,
      normalized.source,
      normalized.positions_count,
      normalized.payload_json,
    )
    .run();
  return normalized;
}

export async function listPositionSnapshots(env, limit = 20) {
  await ensureSchema(env);
  const safeLimit = parseLimit(limit, 20, 100);
  const result = await env.POSITIONS_DB.prepare(
    `SELECT
      id,
      run_generated_at_utc,
      captured_at_utc,
      source,
      positions_count,
      payload_json,
      created_at_utc
    FROM position_snapshots
    ORDER BY captured_at_utc DESC, id DESC
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
      id: row.id ?? null,
      run_generated_at_utc: cleanText(row.run_generated_at_utc),
      captured_at_utc: cleanText(row.captured_at_utc),
      source: cleanText(row.source),
      positions_count: Number(row.positions_count || 0),
      created_at_utc: cleanText(row.created_at_utc),
      snapshot: payload,
    };
  });
}
