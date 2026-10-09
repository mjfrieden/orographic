// Only pre-existing, session-protected aggregate artifacts. No raw mart access.
export const LIMITS = Object.freeze({ bytes: 128 * 1024, rows: 32, timeout: 15000 });
export const SOURCES = Object.freeze({ sync: '/data/diagnostics/shared_mart_sync_latest.json', shadow: '/data/diagnostics/shared_mart_shadow_evidence_latest.json' });
export class SnapshotError extends Error { constructor(message, code = 'unavailable') { super(message); this.code = code; } }
export async function readBoundedJson(response, maxBytes = LIMITS.bytes) {
  if (response.status === 401 || response.status === 403) throw new SnapshotError('Sign in to read protected snapshots.', 'auth');
  if (!response.ok) throw new SnapshotError(`Snapshot unavailable (HTTP ${response.status}).`);
  if (response.redirected || !(response.headers.get('content-type') || '').toLowerCase().includes('application/json')) throw new SnapshotError('Unexpected snapshot response. Sign in again if your session expired.');
  const length = Number(response.headers.get('content-length'));
  if (length > maxBytes) { await response.body?.cancel(); throw new SnapshotError('Snapshot exceeds the safe size limit.', 'limit'); }
  // Never fall back to unbounded response.json()/text().
  if (!response.body?.getReader) throw new SnapshotError('Streaming snapshot reads are unavailable.');
  const reader = response.body.getReader(); let total = 0; const chunks = [];
  try { while (true) { const { done, value } = await reader.read(); if (done) break; total += value.byteLength; if (total > maxBytes) { await reader.cancel(); throw new SnapshotError('Snapshot exceeds the safe size limit.', 'limit'); } chunks.push(value); } }
  finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  let parsed; try { parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)); } catch { throw new SnapshotError('Snapshot is not valid JSON.'); }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new SnapshotError('Snapshot schema is unsupported.');
  return parsed;
}
export async function fetchSnapshot(kind, { signal, fetcher = fetch } = {}) {
  if (!Object.hasOwn(SOURCES, kind)) throw new SnapshotError('Unknown snapshot source.');
  const response = await fetcher(SOURCES[kind], { signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json' } });
  const data = await readBoundedJson(response);
  if (data.schema_version !== (kind === 'sync' ? 1 : 4)) throw new SnapshotError('Snapshot schema changed; this view needs an update.');
  return data;
}
export function count(value) { return Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString('en-US', { maximumFractionDigits: 0 }) : 'Not reported'; }
export function finite(value) { return typeof value === 'number' && Number.isFinite(value); }
export function compatible(sync, shadow) { return Boolean(typeof sync?.mart_id === 'string' && sync.mart_id.trim() && typeof shadow?.mart_id === 'string' && shadow.mart_id.trim() && sync.mart_id === shadow.mart_id); }
export function safeText(value) { return typeof value === 'string' ? value.slice(0, 240) : ''; }
export function timestamp(value) { if (typeof value !== 'string') return 'Not reported'; const normalized = value.replace(/^(\d{4}-\d{2}-\d{2}) /, '$1T').replace(/([+-]\d{2})$/, '$1:00'); const date = new Date(normalized); return Number.isFinite(date.valueOf()) ? date.toISOString().replace('T', ' ').replace('.000Z', ' UTC') : 'Not reported'; }
export const TOPICS = Object.freeze({
  data_quality: ['Data quality', 'Coverage is not integrity. A fully populated feature set can still contain anomalies.', ['cohorts', 'cohorts_with_integrity_anomalies', 'worst_integrity_anomaly_rate', 'worst_critical_null_rate', 'min_feature_coverage_rate', 'min_path_quote_coverage_rate', 'min_executable_outcome_coverage_rate']],
  joint_learning: ['Source-specific learning coverage', 'Source-specific eligibility is stricter than the generic training funnel. Orographic and Cirrus rows remain separate; pooled training is disabled.', ['candidate_rows', 'orographic_source_specific_training_rows', 'cirrus_source_specific_training_rows', 'source_specific_training_rows', 'pooled_training_rows', 'legacy_feature_rows', 'native_feature_rows']],
  cross_system_comparison: ['Cross-system comparability', 'Paired observations are not necessarily comparable returns. Exploratory shadow pairs do not establish alpha.', ['daily_symbol_comparisons', 'paired_comparisons', 'direct_return_comparable_pairs', 'live_direct_return_comparable_pairs', 'fixed_24h_exploratory_shadow_pairs', 'risk_normalized_live_comparable_pairs']],
  common_replay: ['Common replay coverage', 'Orographic LIVE and Cirrus SHADOW are distinct populations. This 24-hour replay is exploratory and is not production-alpha eligible.', ['orographic_live_recommendations', 'orographic_live_replays', 'cirrus_shadow_recommendations', 'cirrus_shadow_replays', 'fixed_24h_exploratory_shadow_pairs', 'fixed_24h_unsynchronized_days']],
  execution_quality: ['Execution coverage', 'Executable outcome coverage is a research-data measure. It is not broker-realized P&L or a live execution promise.', ['recommendations', 'source_systems', 'executable_recommendations', 'recommendations_with_features', 'avg_entry_spread_pct']],
  exit_replay: ['Exit replay coverage', 'Historical path observations can contain multiple quotes per recommendation. Counts do not describe independent trades.', ['recommendations', 'quote_observations']],
  training_evidence: ['Orographic training evidence', 'The dedicated training view has its own eligibility contract. Do not combine its rows with generic funnel counts.', ['training_rows', 'recommendations', 'market_dates', 'rows_with_features', 'option_sides', 'model_versions']],
  model_monitoring: ['Monitoring coverage', 'Cohort coverage only. These counts do not demonstrate model quality or improved performance.', ['monitoring_cohorts', 'recommendations', 'recommendations_with_executable_outcomes', 'recommendations_with_features']],
  training_funnel: ['Generic training funnel', 'Generic eligibility differs from source-specific training eligibility. A usable training mart is not permission to pool training or route trades.', ['recommendations', 'with_point_in_time_feature', 'with_valid_label_outcome', 'training_eligible_recommendations', 'dropped_missing_executable_outcome', 'min_training_eligibility_rate']],
  shadow_entry_gates: ['Shadow evidence gates', 'Research evidence thresholds only. Passing these gates does not grant live-trading authority.', ['paired_executable_outcomes', 'paired_market_dates', 'direct_return_comparable_pairs', 'direct_return_comparable_market_dates', 'live_direct_return_comparable_pairs', 'live_direct_return_comparable_market_dates']]
});
export function catalogRows(data, kind) {
  const catalog = kind === 'views' ? data?.consumer_bundle?.views : data?.rows;
  if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) return { rows: [], truncated: false };
  const entries = Object.entries(catalog);
  return { truncated: entries.length > LIMITS.rows, rows: entries.slice(0, LIMITS.rows).map(([name, value]) => ({ name: safeText(name), rows: kind === 'views' ? value?.rows : value, primaryKey: kind === 'views' && Array.isArray(value?.primary_key) ? value.primary_key.slice(0, 8).map(safeText).join(', ') : '', hash: kind === 'views' && /^[a-f0-9]{64}$/.test(value?.sha256) ? value.sha256 : '' })) };
}
export function filterRows(rows, query, sort) {
  return rows.filter(row => row.name.toLowerCase().includes(query.slice(0, 100).toLowerCase())).sort(sort === 'rows' ? (a, b) => (finite(b.rows) ? b.rows : -1) - (finite(a.rows) ? a.rows : -1) || a.name.localeCompare(b.name) : (a, b) => a.name.localeCompare(b.name));
}
export function metricValue(key, value) {
  if (key.endsWith('_rate') || key.endsWith('_pct')) return finite(value) && Number.isFinite(value * 100) && value >= 0 && (key.endsWith('_pct') || value <= 1) ? `${(value * 100).toLocaleString('en-US', { maximumFractionDigits: 4 })}%` : 'Not reported';
  return count(value);
}
