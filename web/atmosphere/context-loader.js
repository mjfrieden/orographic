import { readSessionJson } from '../session-response.js';

export const CONTEXT_SOURCES = Object.freeze({
  snapshot: { path: '/data/latest_run.json', limit: 1024 * 1024 },
  governance: { path: '/data/diagnostics/model_governance_summary_latest.json', limit: 64 * 1024 },
  ledger: { path: '/data/diagnostics/prospective_dashboard_summary_latest.json', limit: 2 * 1024 * 1024 },
  backtest: { path: '/api/backtest/summary', limit: 512 * 1024 },
});

export async function loadContextArtifact(key, dependencies = {}) {
  const source = CONTEXT_SOURCES[key];
  if (!source) throw new Error('Unsupported context source');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20000);
  const request = dependencies.fetch || globalThis.fetch;
  let reader;
  try {
    const response = await request(source.path, { cache: 'no-store', signal: controller.signal });
    if (response.status === 401 || (response.redirected && new URL(response.url).pathname.replace(/\/$/, '') === '/login')) {
      await readSessionJson(response, 'Research context', dependencies.location || globalThis.location);
    }
    if (!response.ok) throw new Error(`Context unavailable (${response.status})`);
    if (!(response.headers.get('content-type') || '').includes('json')) throw new Error('Context response is not JSON');
    const reported = Number(response.headers.get('content-length'));
    if (Number.isFinite(reported) && reported > source.limit) throw new Error(`Context exceeds the ${Math.round(source.limit / 1024)} KiB safety limit`);
    if (!response.body?.getReader) throw new Error('Bounded streaming is unavailable in this browser');
    reader = response.body.getReader();
    const chunks = []; let length = 0;
    while (true) {
      const next = await reader.read(); if (next.done) break;
      length += next.value.byteLength;
      if (length > source.limit) throw new Error(`Context exceeds the ${Math.round(source.limit / 1024)} KiB safety limit`);
      chunks.push(next.value);
    }
    const bytes = new Uint8Array(length); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const value = JSON.parse(new TextDecoder().decode(bytes));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Context artifact is malformed');
    return compactContext(key, value);
  } finally {
    clearTimeout(timer); controller.abort();
    if (reader) { try { await reader.cancel(); } catch { /* Already closed. */ } }
  }
}
const array = (value) => Array.isArray(value) ? value : [];
export function compactContext(key, value) {
  if (key === 'snapshot') {
    const council = value.council || {};
    const audit = council.summary?.abstain_audit || {};
    return {
      generated_at_utc: value.generated_at_utc,
      model_modes: value.model_modes, model_artifacts: value.model_artifacts,
      scan_settings: { research_outputs_routable: value.scan_settings?.research_outputs_routable },
      summary: { forge_candidate_count: value.summary?.forge_candidate_count ?? (Array.isArray(value.forge_candidates) ? value.forge_candidates.length : undefined) },
      diagnostics: { scout: Object.fromEntries(Object.entries(value.diagnostics?.scout || {}).filter(([key, item]) => !Array.isArray(item) && (typeof item !== 'object' || key === 'rejection_counts'))) },
      forge_candidates: Array.isArray(value.forge_candidates) ? value.forge_candidates.slice(0, 30).map((row) => Object.fromEntries(['symbol', 'contract_symbol', 'forge_score', 'spread_pct', 'contract_cost', 'execution_policy_passed', 'execution_policy_reasons', 'tail_gate_reasons'].map((key) => [key, row?.[key]]))) : undefined,
      council: { abstain: council.abstain, published_live_count: Array.isArray(council.live_board) ? council.live_board.length : undefined, live_board: Array.isArray(council.live_board) ? council.live_board.slice(0, 10).map((row) => ({ symbol: row?.symbol, contract_symbol: row?.contract_symbol, notes: array(row?.notes).slice(0, 2) })) : undefined,
        summary: { candidate_count: council.summary?.candidate_count, abstain_audit: Object.fromEntries(Object.entries(audit).filter(([, item]) => typeof item === 'string' || typeof item === 'number')) } },
    };
  }
  if (key === 'ledger') {
    // Merge fragments of the same scan before retention; never cut a board at a boundary.
    const scans = new Map();
    for (const entry of array(value.entries)) {
      const time = Date.parse(entry?.run_generated_at_utc);
      if (!Number.isFinite(time)) continue;
      const scan = scans.get(time) || { time, stamp: entry.run_generated_at_utc, picks: new Map(), incomplete: false };
      scan.incomplete ||= !Array.isArray(entry?.picks) || entry.comparison_truncated === true;
      for (const pick of array(entry?.picks)) {
        if (!pick || typeof pick !== 'object' || typeof pick.lane !== 'string') { scan.incomplete = true; continue; }
        if (pick.lane !== 'live') continue;
        if (typeof pick.contract_symbol !== 'string' || !pick.contract_symbol.trim()) { scan.incomplete = true; continue; }
        scan.picks.set(pick.contract_symbol, pick);
      }
      scans.set(time, scan);
    }
    return {
      updated_at_utc: value.updated_at_utc,
      entries: [...scans.values()].sort((a, b) => a.time - b.time).slice(-24).map((scan) => ({
        run_generated_at_utc: scan.stamp,
        comparison_truncated: scan.incomplete || scan.picks.size > 20,
        picks: [...scan.picks.values()].slice(0, 20).map((pick) => ({ lane: pick.lane, symbol: pick.symbol, contract_symbol: pick.contract_symbol, emission_quote: { ask: pick.emission_quote?.ask } })),
      })),
    };
  }
  if (key === 'backtest') {
    if (!value.ok || !value.backtest) throw new Error('No usable archived study summary');
    const bt = value.backtest;
    return { study_kind: value.kind || bt.study_kind || bt.study_type, total_trades: bt.total_trades, variant_label: bt.variant_label, generated_at_utc: bt.generated_at_utc || bt.generated_at, backtest_start: bt.backtest_start, backtest_end: bt.backtest_end, variant_key: bt.variant_key, options_data_coverage: { entry_source_counts: bt.options_data_coverage?.entry_source_counts, exit_source_counts: bt.options_data_coverage?.exit_source_counts }, execution_quality: { avg_entry_slippage_pct: bt.execution_quality?.avg_entry_slippage_pct, avg_exit_slippage_pct: bt.execution_quality?.avg_exit_slippage_pct } };
  }
  return value;
}
